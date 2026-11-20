import asyncio
import base64
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import brain
from app.config import settings
from app.providers import _openai_messages
from app.skills import RunContext
from app.skills import see
from app.skills.see import Args, skill
from tests.fakes import FakeSTT

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def attach(user: str, upload: str, name: str, blob: bytes) -> None:
    folder = Path(settings().data_dir) / "uploads" / user / upload
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(blob)


def context(user: str, upload: str, answer: str = "A red dot.") -> RunContext:
    return RunContext(user, "s1", "what is this", [upload], AsyncMock(), AsyncMock(return_value=answer))


def test_an_image_is_sent_to_the_model_as_a_picture():
    attach("u-img", "a" * 32, "photo.png", PNG)
    ctx = context("u-img", "a" * 32)
    result = asyncio.run(skill.run(Args(question="what colour is it?"), ctx))

    message = ctx.complete.await_args.args[0][1]
    assert message["images"] == [base64.b64encode(PNG).decode()]
    assert "what colour is it?" in message["content"] and "photo.png" in message["content"]
    assert result.summary == "A red dot." and result.data["kind"] == "image"


def test_audio_is_transcribed_and_the_words_reach_the_model(monkeypatch):
    attach("u-aud", "b" * 32, "memo.mp3", b"fake")
    monkeypatch.setattr(see, "_ffmpeg", AsyncMock(return_value=b"\x00\x00" * 100))
    monkeypatch.setattr(see.prov, "providers", lambda: SimpleNamespace(stt=[FakeSTT("buy milk at six")]))
    ctx = context("u-aud", "b" * 32, "They said to buy milk at six.")
    result = asyncio.run(skill.run(Args(question="what did they say?"), ctx))

    message = ctx.complete.await_args.args[0][1]
    assert "buy milk at six" in message["content"] and "images" not in message
    assert result.data == {"kind": "audio recording", "file": "memo.mp3", "frames": 0, "transcribed": True}


@ffmpeg
def test_a_video_is_sampled_into_frames(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    attach("u-vid", "c" * 32, "clip.mp4", clip.read_bytes())
    monkeypatch.setattr(see.prov, "providers", lambda: SimpleNamespace(stt=[]))
    ctx = context("u-vid", "c" * 32, "A test pattern.")
    result = asyncio.run(skill.run(Args(), ctx))

    message = ctx.complete.await_args.args[0][1]
    assert len(message["images"]) == see.FRAMES and result.data["frames"] == see.FRAMES
    assert base64.b64decode(message["images"][0])[:2] == b"\xff\xd8"  # JPEG frames


def test_a_file_that_is_not_media_is_refused():
    attach("u-txt", "d" * 32, "notes.txt", b"hello")
    with pytest.raises(ValueError, match="not an image"):
        asyncio.run(skill.run(Args(), context("u-txt", "d" * 32)))


def test_no_attachment_says_how_to_attach():
    with pytest.raises(ValueError, match="paperclip"):
        asyncio.run(skill.run(Args(), RunContext("u-none", "s1", "look", [], AsyncMock(), AsyncMock())))


def test_openai_providers_receive_pictures_as_image_parts():
    b64 = base64.b64encode(PNG).decode()
    out = _openai_messages([{"role": "system", "content": "look"},
                            {"role": "user", "content": "what is it?", "images": [b64]}])
    parts = out[1]["content"]
    assert parts[0] == {"type": "text", "text": "what is it?"}
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{b64}"


@pytest.mark.parametrize("said", ["no, I meant Hyderabad in Pakistan", "that's wrong, it is 42",
                                  "stop adding emojis", "next time use kilometres"])
def test_corrections_are_learned_from(said):
    assert brain.PREFERENCE_CUE.search(said)


def test_a_large_video_upload_is_allowed_but_a_large_text_file_is_not(client, auth, monkeypatch):
    monkeypatch.setattr(settings(), "max_upload_bytes", 10)
    big = b"x" * 100
    assert client.post("/uploads", files={"file": ("clip.mp4", big)}, headers=auth).status_code == 200
    assert client.post("/uploads", files={"file": ("notes.txt", big)}, headers=auth).status_code == 413
