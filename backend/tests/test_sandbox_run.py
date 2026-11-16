import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import sandbox, web
from app import workspace as wsp
from app.config import settings
from app.db import SessionLocal, User, new_id
from app.skills import RunContext
from app.skills import run_tools
from tests.conftest import signup

macos = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"), reason="needs sandbox-exec")


@pytest.fixture(autouse=True)
def seatbelt(monkeypatch):
    monkeypatch.setattr(settings(), "sandbox", "seatbelt")
    monkeypatch.setattr(settings(), "tunnel", "")


def context(*answers: str) -> RunContext:
    with SessionLocal() as db:
        user = User(email=f"r{new_id()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
    return RunContext(user.id, "s", "", [], AsyncMock(), AsyncMock(side_effect=list(answers)))


def base_of(ctx) -> Path:
    with SessionLocal() as db:
        return wsp.root(asyncio.run(wsp.current(db, ctx.user_id)))


def test_sandbox_off_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "sandbox", "off")
    with pytest.raises(sandbox.SandboxError, match="turned off"):
        asyncio.run(sandbox.run(tmp_path, "echo hi"))


@macos
def test_seatbelt_confines_writes_reads_and_localhost(tmp_path):
    work = Path(tmp_path).resolve()
    ok = asyncio.run(sandbox.run(work, "echo hi > a.txt && cat a.txt"))
    assert ok.ok and ok.output.strip() == "hi"
    escape = asyncio.run(sandbox.run(work, f"echo x > {Path.home()}/vox_escape_probe.txt"))
    assert not escape.ok and not (Path.home() / "vox_escape_probe.txt").exists()
    secrets = asyncio.run(sandbox.run(work, f"cat {sandbox.APP_ROOT}/pyproject.toml"))
    assert not secrets.ok and "not permitted" in secrets.output
    local = asyncio.run(sandbox.run(work, "curl -s -m 3 http://127.0.0.1:8000/healthz && echo REACHED || echo BLOCKED"))
    assert "BLOCKED" in local.output
    slow = asyncio.run(sandbox.run(work, "sleep 10", timeout=1))
    assert slow.timed_out and slow.seconds < 5


@macos
def test_seatbelt_workspace_under_private_data_dir_can_resolve_its_own_path():
    base = base_of(context())  # real workspaces live inside the data dir the profile hides
    venv = asyncio.run(sandbox.run(base, "python3 -m venv .venv && .venv/bin/python -c 'print(42)'", timeout=120))
    assert venv.ok and venv.output.strip().endswith("42"), venv.output  # ensurepip realpath()s the parents
    siblings = asyncio.run(sandbox.run(base, f"ls {base.parent}"))
    assert not siblings.ok and "not permitted" in siblings.output  # other workspaces stay unreadable


@macos
def test_fix_loop_repairs_a_real_bug(monkeypatch):
    fixed = "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n"
    ctx = context(json.dumps({"explanation": "use + instead of -", "files": [{"path": "main.py", "content": fixed}]}))
    base = base_of(ctx)
    (base / "main.py").write_text("def add(a, b):\n    return a - b\n\nassert add(2, 3) == 5, 'add is wrong'\n")
    monkeypatch.setattr(run_tools, "search", AsyncMock(return_value=[]))  # no network in unit tests
    ok, last, notes = asyncio.run(run_tools.fix_loop(ctx, base, "python3 main.py", attempts=2))
    assert ok and "5" in last and notes == ["attempt 1: use + instead of -"]
    assert "AssertionError" in ctx.complete.await_args.args[0][1]["content"]
    log = [h["message"] for h in asyncio.run(wsp.history(base))]
    assert any(m.startswith("voxagent: fix attempt 1") for m in log)  # every fix is an undoable version


@macos
def test_fix_loop_gives_up_and_explains(monkeypatch):
    ctx = context(*[json.dumps({"explanation": f"try {n}", "files": [{"path": "main.py", "content": f"raise SystemExit({n})\n"}]})
                    for n in (1, 2, 3)])
    (base_of(ctx) / "main.py").write_text("raise SystemExit(3)\n")
    monkeypatch.setattr(run_tools, "search", AsyncMock(return_value=[]))
    result = asyncio.run(run_tools.FixAndRun().run(run_tools.FixArgs(command="python3 main.py"), ctx))
    assert result.data["ok"] is False and "How would you like to proceed" in result.summary


@macos
def test_detect_tests_and_run_them():
    ctx = context()
    base = base_of(ctx)
    (base / "test_math.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    assert run_tools.detect_tests(base) == "python3 -m pytest -q"
    (base / "package.json").write_text('{"scripts": {"test": "node -e 1"}}')
    assert run_tools.detect_tests(base) == "npm test --silent"


@macos
def test_server_preview_and_streamed_terminal():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        _preview_flow(client)


def _preview_flow(client):
    headers, _ = signup(client)
    ws = client.get("/workspaces", headers=headers).json()[0]["id"]
    client.put(f"/workspaces/{ws}/file", headers=headers, json={"path": "index.html", "content": "<h1>preview works</h1>"})
    streamed = client.post(f"/workspaces/{ws}/run", headers=headers, json={"command": "echo one; echo two"})
    assert "one\ntwo" in streamed.text and "[exit 0" in streamed.text

    started = client.post(f"/workspaces/{ws}/servers", headers=headers,
                          json={"command": "python3 -m http.server $PORT --bind 127.0.0.1"}).json()
    try:
        server_id = started["id"]
        deadline, page = time.time() + 10, None
        while time.time() < deadline:
            page = client.get(f"/preview/{server_id}/index.html")
            if page.status_code == 200:
                break
            time.sleep(0.3)
        assert page is not None and "preview works" in page.text
        # the page gets an origin of its own, so its scripts cannot use the viewer's VoxAgent session
        assert "sandbox allow-scripts" in page.headers["content-security-policy"]
        assert "allow-same-origin" not in page.headers["content-security-policy"]
        assert client.get("/preview/not-a-real-id/").status_code == 404
        assert client.get(f"/preview/{server_id}/.git/config").status_code == 404  # a shared link never leaks .git
        assert client.get(f"/preview/{server_id}/.vox/x").status_code == 404
    finally:
        client.delete(f"/workspaces/{ws}/servers/{started['id']}", headers=headers)
    assert client.get(f"/preview/{started['id']}/").status_code == 404


def test_ssrf_guard_blocks_internal_addresses(monkeypatch):
    import asyncio.base_events

    async def resolve(self, host, *_a, **_k):
        ip = {"intranet.example": "10.0.0.5", "meta.example": "169.254.169.254"}.get(host, "127.0.0.1")
        return [(0, 0, 0, "", (ip, 0))]

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "getaddrinfo", resolve)
    for url in ("http://intranet.example/", "http://meta.example/latest", "http://127.0.0.1:8000/", "ftp://x.org/"):
        with pytest.raises(web.WebError):
            asyncio.run(web.fetch(url))


def test_html_to_text_drops_scripts():
    title, text = web.html_to_text("<title>Docs</title><script>evil()</script><p>Hello <b>world</b></p>")
    assert title == "Docs" and text == "Hello world"


ECHO_SERVER = r"""import base64, hashlib, os, socket, threading
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
def serve(conn):
    head = conn.recv(4096).decode()
    key = next(l.split(":", 1)[1].strip() for l in head.split("\r\n") if l.lower().startswith("sec-websocket-key"))
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    while True:
        b1, b2 = conn.recv(2)
        size = b2 & 127
        mask = conn.recv(4)
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(conn.recv(size)))
        if b1 & 15 == 8:
            return
        reply = b"echo:" + data
        conn.sendall(bytes([0x81, len(reply)]) + reply)
srv = socket.create_server(("127.0.0.1", int(os.environ["PORT"])))
while True:
    threading.Thread(target=serve, args=(srv.accept()[0],), daemon=True).start()
"""


@macos
def test_preview_proxies_websockets():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers, _ = signup(client)
        ws = client.get("/workspaces", headers=headers).json()[0]["id"]
        client.put(f"/workspaces/{ws}/file", headers=headers, json={"path": "echo.py", "content": ECHO_SERVER})
        started = client.post(f"/workspaces/{ws}/servers", headers=headers,
                              json={"command": "python3 echo.py"}).json()
        try:
            deadline, reply = time.time() + 10, None
            while time.time() < deadline and reply is None:
                try:
                    with client.websocket_connect(f"/preview/{started['id']}/") as sock:
                        sock.send_text("hi")
                        reply = sock.receive_text()
                except Exception:
                    time.sleep(0.3)
            assert reply == "echo:hi"
        finally:
            client.delete(f"/workspaces/{ws}/servers/{started['id']}", headers=headers)


def _site_outline(run: str) -> str:
    return json.dumps({"summary": "Coffee shop site", "design": "warm cream and espresso", "install": "",
                       "run": run, "web": True,
                       "files": [{"path": "index.html", "purpose": "home page"},
                                 {"path": "serve.py", "purpose": "tiny server"},
                                 {"path": ".venv/bin/python", "purpose": "made by install"}]})


SERVE_OK = ("import http.server, os\nos.chdir(os.path.dirname(os.path.abspath(__file__)))\n"
            "http.server.ThreadingHTTPServer(('0.0.0.0', int(os.environ['PORT'])), "
            "http.server.SimpleHTTPRequestHandler).serve_forever()\n")


@macos
def test_create_project_writes_each_file_then_proves_the_page_loads(monkeypatch):
    page = "<!doctype html><title>Bean There</title><h1>Bean There</h1>"
    ctx = context(_site_outline("python3 serve.py"), page, f"```python\n{SERVE_OK}```")  # fences are stripped
    result = asyncio.run(run_tools.CreateProject().run(run_tools.ProjectArgs(description="a coffee shop site"), ctx))
    try:
        assert "the page loads" in result.summary and result.data["files"] == ["index.html", "serve.py"]
        assert result.final and result.data["url"] in result.summary  # read out as written: the model dropped the path
        assert (base_of(ctx) / "index.html").read_text() == page + "\n"
        second = ctx.complete.await_args_list[2].args[0][1]["content"]
        assert "--- index.html ---" in second and "Write: serve.py" in second  # each file sees the ones before it
    finally:
        for server in list(sandbox.SERVERS.values()):
            asyncio.run(sandbox.stop_server(server.id))


@macos
def test_create_project_fixes_a_server_that_crashes(monkeypatch):
    monkeypatch.setattr(run_tools, "search", AsyncMock(return_value=[]))
    broken = "import http.server, os\nraise RuntimeError('port not read')\n"
    fix = f"Read PORT and serve.\n=== serve.py ===\n{SERVE_OK}"
    ctx = context(_site_outline("python3 serve.py"), "<h1>Hi</h1>", broken, fix)
    result = asyncio.run(run_tools.CreateProject().run(run_tools.ProjectArgs(description="a site"), ctx))
    try:
        assert "the page loads" in result.summary
        assert "port not read" in ctx.complete.await_args_list[3].args[0][1]["content"]  # the fix saw the crash log
    finally:
        for server in list(sandbox.SERVERS.values()):
            asyncio.run(sandbox.stop_server(server.id))


@macos
def test_create_project_fixes_a_failing_install(monkeypatch):
    monkeypatch.setattr(run_tools, "search", AsyncMock(return_value=[]))
    outline = json.dumps({"summary": "Tool", "install": "! grep -q sqlite3 requirements.txt", "run": "", "web": False,
                          "files": [{"path": "requirements.txt", "purpose": "deps"}]})
    fix = "sqlite3 ships with Python.\n=== requirements.txt ===\nflask\n"
    ctx = context(outline, "flask\nsqlite3", fix)
    result = asyncio.run(run_tools.CreateProject().run(run_tools.ProjectArgs(description="a tool"), ctx))
    assert "Dependencies installed." in result.summary
    assert (base_of(ctx) / "requirements.txt").read_text() == "flask\n"


def test_fix_replies_split_into_files_and_explanation():
    reply = "Use + instead of -.\n=== src/a.py ===\nx = 1\n=== b.js ===\n```js\nlet y = 2\n```\n"
    files, why = run_tools.parse_fix(reply)
    assert why == "Use + instead of -."
    assert files == [("src/a.py", "x = 1\n"), ("b.js", "let y = 2\n")]
    legacy = json.dumps({"explanation": "old", "files": [{"path": "m.py", "content": "z\n"}]})
    assert run_tools.parse_fix(legacy) == ([("m.py", "z\n")], "old")
    assert run_tools.parse_fix("I could not find the problem.") == ([], "")


@pytest.mark.parametrize(("install", "run", "expected"), [
    ("pip install -r requirements.txt", "python app.py",
     ("[ -d .venv ] || python3 -m venv .venv; .venv/bin/pip install -q -r requirements.txt", ".venv/bin/python app.py")),
    ("pip3 install flask", "python3 app.py",
     ("[ -d .venv ] || python3 -m venv .venv; .venv/bin/pip install -q flask", ".venv/bin/python app.py")),
    ("npm install", "npm start", ("npm install", "npm start")),
    ("", "python3 -m http.server $PORT", ("", "python3 -m http.server $PORT")),
])
def test_project_commands_use_a_virtualenv_for_python(install, run, expected):
    assert run_tools.runnable(install, run) == expected


def test_a_site_with_no_home_page_does_not_count_as_loading(tmp_path):
    import functools
    import http.server
    import threading
    from types import SimpleNamespace

    (tmp_path / "layout.html").write_text("<h1>Brew Haven</h1>")  # what llama3.2 wrote: no index.html
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        server = SimpleNamespace(host="127.0.0.1", port=httpd.server_address[1], running=True)
        ok, note = asyncio.run(run_tools._responds(server))
        assert not ok and "index.html" in note
        (tmp_path / "index.html").write_text("<h1>Brew Haven</h1>")
        assert asyncio.run(run_tools._responds(server))[0]
    finally:
        httpd.shutdown()
