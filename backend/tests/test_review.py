import asyncio
import io
import zipfile

import pytest

from app import providers as prov
from app.config import settings
from app.db import record
from app.skills import RunContext
from app.skills.code_review import Args, _from_github, gather, scan_secrets, score
from evals.run import evaluate
from tests.conftest import signup
from tests.fakes import FakeLLM, FakeSTT, FakeTTS

BAD = "def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n"


def llm_issue(line, severity="high"):
    return {"severity": severity, "category": "bug", "line": line, "explanation": "File handle is never closed.",
            "why": "It leaks file descriptors.", "fix": "Use a with block."}


@pytest.fixture
def fake(monkeypatch):
    def use(*script):
        llm = FakeLLM(*script)
        monkeypatch.setattr(prov, "providers", lambda: prov.Providers([llm], [FakeSTT()], [FakeTTS()]))
        return llm

    return use


def test_static_analysis_finds_every_known_issue_in_the_eval_set():
    result = asyncio.run(evaluate(use_llm=False))
    assert result["static_recall"] == 1.0, result["missed"]


def test_review_combines_static_and_llm_findings(client, auth, fake):
    llm = fake({"issues": [llm_issue(3), llm_issue(99), llm_issue(4)]})
    review = client.post("/reviews", headers=auth, json={"code": BAD, "filename": "load.py"}).json()
    found = {(i["source"], i["line"]) for i in review["issues"]}
    assert ("ruff", 4) in found and ("llm", 3) in found
    assert ("llm", 99) not in found  # a line that does not exist is dropped
    assert ("llm", 4) not in found  # static analysis already owns that line
    assert review["score"] == 100 - 12 - 5 and review["breakdown"]["error-handling"] == 95
    assert all(i["why"] and i["fix"] for i in review["issues"])
    assert "dependency vulnerability audit" in review["not_run"][0]
    prompt = llm.calls[0]["messages"]
    assert "<data>\n1: def load(path):" in prompt[1]["content"] and "untrusted data" in prompt[0]["content"]


def test_review_survives_a_model_that_breaks_the_schema(client, auth, fake):
    fake("this is not json")
    review = client.post("/reviews", headers=auth, json={"code": BAD}).json()
    assert [i["source"] for i in review["issues"]] == ["ruff"]
    assert "unusable answer" in review["not_run"][-1]


def test_review_export_share_and_ownership(client, auth, fake):
    fake({"issues": []})
    review = client.post("/reviews", headers=auth, json={"code": BAD}).json()
    markdown = client.get(f"/reviews/{review['id']}/export", headers=auth)
    assert "**Quality score: 95/100**" in markdown.text and "Suggested fix" in markdown.text
    token = client.post(f"/reviews/{review['id']}/share", headers=auth).json()["token"]
    assert client.get(f"/shared/{token}").json()["id"] == review["id"]  # no login needed
    assert client.get("/shared/wrong-token").status_code == 404
    stranger = signup(client)[0]
    assert client.get(f"/reviews/{review['id']}", headers=stranger).status_code == 404
    assert client.get("/reviews", headers=auth).json()[0]["id"] == review["id"]


def test_review_of_uploaded_zip_ignores_hostile_paths(client, auth, fake):
    fake({"issues": []}, {"issues": []})
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("../../escape.py", BAD)
        archive.writestr("src/app.py", "print(undefined_name)\n")
        archive.writestr("logo.png", "not code")
    upload = client.post("/uploads", headers=auth, files={"file": ("../proj.zip", blob.getvalue())}).json()
    assert upload["filename"] == "proj.zip"
    review = client.post("/reviews", headers=auth, json={"upload_id": upload["id"]}).json()
    assert {i["file"] for i in review["issues"]} == {"escape.py", "src/app.py"}


def test_review_input_limits_and_errors(client, auth, fake, monkeypatch):
    fake()
    assert client.post("/reviews", headers=auth, json={}).status_code == 400
    assert client.post("/reviews", headers=auth, json={"upload_id": "../../etc"}).status_code == 400
    monkeypatch.setattr(settings(), "max_upload_bytes", 10)
    assert client.post("/uploads", headers=auth, files={"file": ("big.py", b"x" * 11)}).status_code == 413
    monkeypatch.setattr(settings(), "free_daily_reviews", 0)
    limited = client.post("/reviews", headers=auth, json={"code": BAD})
    assert limited.status_code == 400 and "daily free limit" in limited.json()["detail"]


def test_review_keeps_static_findings_when_the_llm_is_down(client, auth, fake):
    fake(RuntimeError("down"))
    review = client.post("/reviews", headers=auth, json={"code": BAD}).json()
    assert [i["source"] for i in review["issues"]] == ["ruff"]
    assert "LLM review (the language model is unavailable)" in review["not_run"]


def test_review_refuses_code_the_model_made_up():
    async def nothing(*_a, **_k): ...

    ctx = RunContext("u1", "s1", "what is recursion?", [], nothing, nothing)
    with pytest.raises(ValueError, match="no code to review"):
        asyncio.run(gather(Args(code="def fact(n): return n * fact(n - 1)"), ctx))
    ctx.user_text = "review this: def f(x):  return eval(x)"
    assert asyncio.run(gather(Args(code="def f(x): return eval(x)"), ctx))[1] == {"snippet.py": "def f(x): return eval(x)"}


@pytest.mark.parametrize("url", [
    "https://evil.example.com/a/b",
    "https://github.com.evil.example.com/a/b",
    "http://github.com/a/b",
    "https://github.com/a/b/blob/main/../../etc/passwd?x=http://169.254.169.254/",
    "http://127.0.0.1:8000/admin",
])
def test_only_github_urls_are_fetched(url):
    with pytest.raises(ValueError):
        asyncio.run(_from_github(url))


def test_secret_scanner_and_scoring():
    hits = scan_secrets({"a.py": 'x = 1\napi_key = "abcd1234efgh"\n', "b.py": "password = None\n"})
    assert [(h["file"], h["line"], h["severity"]) for h in hits] == [("a.py", 2, "critical")]
    total, breakdown = score(hits * 5)
    assert total == 0 and breakdown["security"] == 0 and breakdown["bug"] == 100


def test_usage_admin_and_metrics(client, auth, fake):
    fake({"issues": []})
    client.post("/reviews", headers=auth, json={"code": BAD})
    usage = client.get("/usage/me", headers=auth).json()
    assert usage["last_24h"]["review"]["count"] == 1 and usage["daily_limits"]["review"] == 10
    assert client.get("/admin/overview", headers=auth).status_code == 403
    admin = {"Authorization": "Bearer " + client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "password123"}).json().get("access_token", "")}
    if client.get("/me", headers=admin).status_code != 200:
        admin = signup(client, "admin@example.com")[0]
    overview = client.get("/admin/overview", headers=admin).json()
    assert overview["usage"]["review"]["count"] >= 1 and overview["users"]
    record("stt", "u1", latency_ms=120)
    assert client.get("/metrics", headers=admin).json()["stt"] == {"count": 1, "p50": 120, "p95": 120}
    assert client.get("/skills", headers=auth).json()[0]["name"] == "code_review"
