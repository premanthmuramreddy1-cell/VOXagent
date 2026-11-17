import asyncio
from unittest.mock import AsyncMock

import pytest

from app.skills import RunContext, explain
from app.skills.explain import Args, skill


def context(answer: str, uploads: list[str] | None = None) -> RunContext:
    return RunContext("u1", "s1", "explain this", uploads or [], AsyncMock(), AsyncMock(return_value=answer))


def test_explain_asks_the_model_at_the_requested_level():
    ctx = context("A loop repeats code.")
    result = asyncio.run(skill.run(Args(subject="for i in range(3): print(i)", level="beginner"), ctx))

    system = ctx.complete.await_args.args[0][0]["content"]
    assert "beginner" in system and "no jargon" in system
    assert result.summary == "A loop repeats code." and result.data["level"] == "beginner"


def test_explain_reads_an_uploaded_file(monkeypatch):
    monkeypatch.setattr(explain, "_from_upload", lambda user, upload: {"app.py": "def f(): pass"})
    ctx = context("It defines f.", uploads=["a" * 32])
    asyncio.run(skill.run(Args(subject="what does this do", level="expert"), ctx))

    user = ctx.complete.await_args.args[0][1]["content"]
    assert "app.py" in user and "def f(): pass" in user


def test_explain_uses_the_attachment_when_the_model_passes_its_filename_as_the_id(monkeypatch):
    # live: the model sent upload_id="calc.py", which is not an id, and the turn failed with "unknown upload"
    seen = []
    monkeypatch.setattr(explain, "_from_upload", lambda user, upload: seen.append(upload) or {"calc.py": "x"})
    asyncio.run(skill.run(Args(subject="what does add return", upload_id="calc.py"), context("ok", ["a" * 32])))
    assert seen == ["a" * 32]


def test_explain_caps_very_long_input():
    ctx = context("ok")
    asyncio.run(skill.run(Args(subject="x" * 100_000), ctx))
    assert len(ctx.complete.await_args.args[0][1]["content"]) < 20_000


def test_explain_rejects_empty_input():
    with pytest.raises(ValueError):
        asyncio.run(skill.run(Args(subject="  "), context("ok")))
