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
        assert client.get("/preview/not-a-real-id/").status_code == 404
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
