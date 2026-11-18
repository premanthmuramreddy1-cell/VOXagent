import io
import zipfile

from tests.conftest import signup


def test_a_workspace_named_in_telugu_keeps_its_name_and_exports(client):
    headers, _ = signup(client)
    made = client.post("/workspaces", headers=headers, json={"name": 'నా ప్రాజెక్ట్ "1"/'}).json()
    assert made["name"] == "నా ప్రాజెక్ట్ 1"  # vowel signs are part of the letters; quotes and slashes still go
    exported = client.get(f"/workspaces/{made['id']}/export", headers=headers)
    assert exported.status_code == 200 and zipfile.ZipFile(io.BytesIO(exported.content)).namelist() == ["NOTES.md"]
    assert exported.headers["content-disposition"].isascii()  # headers cannot carry the name raw


def test_editor_flow_over_rest(client):
    headers, _ = signup(client)
    spaces = client.get("/workspaces", headers=headers).json()
    assert len(spaces) == 1
    ws = spaces[0]["id"]

    saved = client.put(f"/workspaces/{ws}/file", headers=headers, json={"path": "src/a.py", "content": "x = 1\n"})
    assert saved.json()["commit"]
    assert client.get(f"/workspaces/{ws}/file", headers=headers, params={"path": "src/a.py"}).json()["content"] == "x = 1\n"
    assert "src/a.py" in client.get(f"/workspaces/{ws}/files", headers=headers).json()["files"]

    assert client.post(f"/workspaces/{ws}/undo", headers=headers).json() == {"undone": "edit src/a.py"}
    assert client.get(f"/workspaces/{ws}/file", headers=headers, params={"path": "src/a.py"}).status_code == 400
    assert client.get(f"/workspaces/{ws}/file", headers=headers, params={"path": "../../etc/passwd"}).status_code == 400


def test_import_export_and_isolation(client):
    headers, _ = signup(client)
    other, _ = signup(client)
    ws = client.post("/workspaces", headers=headers, json={"name": "imported"}).json()["id"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("proj/main.go", "package main\nfunc main() {}\n")
    r = client.post(f"/workspaces/{ws}/import", headers=headers, files={"file": ("p.zip", buffer.getvalue())})
    assert r.json()["files"] == 1
    index = client.get(f"/workspaces/{ws}/index", headers=headers).json()
    assert {"name": "main", "kind": "function", "path": "main.go", "line": 2} in index["symbols"]
    exported = zipfile.ZipFile(io.BytesIO(client.get(f"/workspaces/{ws}/export", headers=headers).content))
    assert "main.go" in exported.namelist()
    assert client.get(f"/workspaces/{ws}/files", headers=other).status_code == 404  # other users cannot see it


def test_staged_changes_accept_one_reject_rest(client):
    from app import workspace as wsp
    from app.db import SessionLocal, Workspace

    headers, _ = signup(client)
    ws = client.get("/workspaces", headers=headers).json()[0]["id"]
    with SessionLocal() as db:
        space = db.get(Workspace, ws)
        keep = wsp.stage_write(db, space, "keep.txt", "yes").id
        wsp.stage_write(db, space, "drop.txt", "no")
    listed = client.get(f"/workspaces/{ws}/changes", headers=headers).json()
    assert {c["path"] for c in listed} == {"keep.txt", "drop.txt"}
    assert client.post(f"/workspaces/{ws}/changes/apply", headers=headers, json={"ids": [keep]}).json()["applied"] == 1
    assert client.post(f"/workspaces/{ws}/changes/reject", headers=headers, json={}).json() == {"rejected": 1}
    assert client.get(f"/workspaces/{ws}/files", headers=headers).json() == {"files": ["NOTES.md", "keep.txt"], "pending": 0}


def test_an_oversized_zip_is_called_too_large_not_broken(client, monkeypatch):
    from app.config import settings

    headers, _ = signup(client)
    ws = client.post("/workspaces", headers=headers, json={"name": "big"}).json()["id"]
    monkeypatch.setattr(settings(), "max_workspace_bytes", 50)
    r = client.post(f"/workspaces/{ws}/import", headers=headers, files={"file": ("p.zip", b"PK" + b"x" * 100)})
    assert r.status_code == 413


def test_files_can_be_renamed_and_deleted_and_undone(client):
    headers, _ = signup(client)
    other, _ = signup(client)
    ws = client.get("/workspaces", headers=headers).json()[0]["id"]
    files = lambda: client.get(f"/workspaces/{ws}/files", headers=headers).json()["files"]
    client.put(f"/workspaces/{ws}/file", headers=headers, json={"path": "a.py", "content": "x = 1\n"})
    client.put(f"/workspaces/{ws}/file", headers=headers, json={"path": "b.py", "content": ""})

    moved = client.post(f"/workspaces/{ws}/file/move", headers=headers, json={"path": "a.py", "to": "src/app.py"})
    assert moved.status_code == 200 and "src/app.py" in files() and "a.py" not in files()
    assert client.get(f"/workspaces/{ws}/file", headers=headers, params={"path": "src/app.py"}).json()["content"] == "x = 1\n"
    taken = client.post(f"/workspaces/{ws}/file/move", headers=headers, json={"path": "b.py", "to": "src/app.py"})
    assert taken.status_code == 409  # never overwrite another file
    escape = client.post(f"/workspaces/{ws}/file/move", headers=headers, json={"path": "b.py", "to": "../x.py"})
    assert escape.status_code == 400
    assert client.post(f"/workspaces/{ws}/file/move", headers=other, json={"path": "b.py", "to": "c.py"}).status_code == 404

    assert client.delete(f"/workspaces/{ws}/file", headers=other, params={"path": "b.py"}).status_code == 404
    assert client.delete(f"/workspaces/{ws}/file", headers=headers, params={"path": "b.py"}).status_code == 200
    assert "b.py" not in files()
    assert client.delete(f"/workspaces/{ws}/file", headers=headers, params={"path": "b.py"}).status_code == 400
    assert client.post(f"/workspaces/{ws}/undo", headers=headers).json() == {"undone": "delete b.py"}
    assert "b.py" in files()
