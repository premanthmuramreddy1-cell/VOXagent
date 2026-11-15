import asyncio
import io
import zipfile

import pytest

from app import workspace as wsp
from app.db import SessionLocal, User, new_id
from app.workspace import WorkspaceError


def make_user() -> str:
    with SessionLocal() as db:
        user = User(email=f"ws{new_id()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        return user.id


@pytest.fixture
def ws():
    user_id = make_user()
    db = SessionLocal()
    space = asyncio.run(wsp.create(db, user_id, "demo"))
    yield db, space, wsp.root(space)
    db.close()


def test_new_workspace_is_a_git_repo_with_notes(ws):
    _, _, base = ws
    assert (base / ".git").is_dir() and (base / "NOTES.md").is_file()
    assert [h["message"] for h in asyncio.run(wsp.history(base))] == ["add project notes", "workspace created"]


def test_paths_cannot_escape_the_workspace(ws):
    _, _, base = ws
    for bad in ("../x", "/../../etc/passwd", ".git/config", "a/../../b", ""):
        with pytest.raises(WorkspaceError):
            wsp.safe_path(base, bad)
    assert wsp.safe_path(base, "/src/app.py") == base / "src" / "app.py"


def test_write_is_staged_until_applied_then_undone(ws):
    db, space, base = ws
    change = wsp.stage_write(db, space, "app.py", "print('hi')\n")
    assert not (base / "app.py").exists() and "+print('hi')" in change.diff
    result = asyncio.run(wsp.apply_changes(db, space))
    assert result["applied"] == 1 and result["commit"]
    assert (base / "app.py").read_text() == "print('hi')\n"
    assert asyncio.run(wsp.undo(base)).startswith("voxagent: write app.py")
    assert not (base / "app.py").exists()


def test_multi_edit_validates_everything_before_staging(ws):
    db, space, _ = ws
    wsp.stage_write(db, space, "a.py", "x = 1\ny = 1\n")
    asyncio.run(wsp.apply_changes(db, space))
    with pytest.raises(WorkspaceError, match="appears 2 times"):
        wsp.stage_edits(db, space, [{"path": "a.py", "find": "1", "replace": "2"}])
    with pytest.raises(WorkspaceError, match="could not find"):
        wsp.stage_edits(db, space, [{"path": "a.py", "find": "x = 1", "replace": "x = 3"},
                                    {"path": "a.py", "find": "zzz", "replace": "q"}])
    assert wsp.pending(db, space) == []  # nothing staged when any edit fails
    changes = wsp.stage_edits(db, space, [{"path": "a.py", "find": "x = 1", "replace": "x = 3"},
                                          {"path": "a.py", "find": "y = 1", "replace": "y = 4"}])
    assert len(changes) == 1 and changes[0].content == "x = 3\ny = 4\n"


def test_rename_symbol_everywhere_and_reject(ws):
    db, space, _ = ws
    wsp.stage_write(db, space, "u.py", "def getUser():\n    return getUserName()\n")
    wsp.stage_write(db, space, "v.js", "getUser(); getUser2()\n")
    asyncio.run(wsp.apply_changes(db, space))
    changes = wsp.stage_rename_symbol(db, space, "getUser", "fetchUser")
    by_path = {c.path: c.content for c in changes}
    assert by_path == {"u.py": "def fetchUser():\n    return getUserName()\n", "v.js": "fetchUser(); getUser2()\n"}
    assert wsp.reject_changes(db, space, [changes[0].id]) == 1 and len(wsp.pending(db, space)) == 1


def test_delete_and_rename_apply(ws):
    db, space, base = ws
    wsp.stage_write(db, space, "old.txt", "hello")
    asyncio.run(wsp.apply_changes(db, space))
    wsp.stage_rename(db, space, "old.txt", "docs/new.txt")
    asyncio.run(wsp.apply_changes(db, space))
    assert (base / "docs" / "new.txt").read_text() == "hello"
    wsp.stage_delete(db, space, "docs/new.txt")
    asyncio.run(wsp.apply_changes(db, space))
    assert wsp.list_files(base) == ["NOTES.md"]


def test_zip_import_strips_top_folder_and_refuses_zip_slip(ws):
    _, _, base = ws
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("repo-main/src/main.py", "def main():\n    pass\n")
        archive.writestr("repo-main/node_modules/x/index.js", "skip me")
    assert wsp.import_zip(base, buffer.getvalue()) == 1
    assert (base / "src" / "main.py").is_file()

    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../../escape.txt", "nope")
    with pytest.raises(WorkspaceError):
        wsp.import_zip(base, evil.getvalue())
    names = zipfile.ZipFile(io.BytesIO(wsp.export_zip(base))).namelist()
    assert "src/main.py" in names and not any(n.startswith(".git") for n in names)


def test_index_finds_symbols_imports_and_answers_where(ws):
    _, _, base = ws
    (base / "auth.py").write_text("import jwt\n\ndef login(user):\n    return jwt\n\nclass Session:\n    pass\n")
    (base / "server.js").write_text("const express = require('express')\napp.post('/login', handleLogin)\n"
                                    "export async function handleLogin(req) {}\n")
    (base / "package.json").write_text('{"dependencies": {"express": "^4"}}')
    index = wsp.build_index(base)
    names = {(s["name"], s["path"]) for s in index["symbols"]}
    assert {("login", "auth.py"), ("Session", "auth.py"), ("handleLogin", "server.js"), ("/login", "server.js")} <= names
    assert index["imports"]["auth.py"] == ["jwt"] and index["dependencies"]["npm"] == ["express"]
    hits = wsp.lookup(base, "where is login handled?")
    assert hits[0]["path"] in {"auth.py", "server.js"}


def test_search_and_binary_files(ws):
    _, _, base = ws
    (base / "a.txt").write_text("needle here\nno\n")
    (base / "b.bin").write_bytes(b"\0\1needle")
    assert wsp.search(base, "NEEDLE") == [{"path": "a.txt", "line": 1, "text": "needle here"}]
    with pytest.raises(WorkspaceError, match="binary"):
        wsp.read_text(base / "b.bin")
