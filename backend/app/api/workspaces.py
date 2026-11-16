"""Workspace REST API for the in-browser editor: files, staged changes, versions, import and export."""

import asyncio
import contextlib
import time

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sandbox
from app import workspace as wsp
from app.config import settings
from app.db import User, Workspace, get_db
from app.security import current_user
from app.skills.code_review import GITHUB

router = APIRouter(prefix="/workspaces")


def _ws_json(ws: Workspace) -> dict:
    return {"id": ws.id, "name": ws.name, "opened_at": ws.opened_at.isoformat()}


async def owned(db: Session, ws_id: str, user: User) -> Workspace:
    ws = db.get(Workspace, ws_id)
    if not ws or ws.user_id != user.id:
        raise HTTPException(404, "workspace not found")
    await wsp.ensure_repo(wsp.root(ws))
    return ws


def _guard(fn):
    """Turn WorkspaceError into a 400 the client can show."""
    try:
        return fn()
    except (wsp.WorkspaceError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class FileBody(BaseModel):
    path: str
    content: str


class Ids(BaseModel):
    ids: list[str] = Field(default_factory=list)


class GithubImport(BaseModel):
    url: str


@router.get("")
async def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    await wsp.current(db, user.id)  # make sure there is at least one
    rows = db.scalars(select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.opened_at.desc()))
    return [_ws_json(w) for w in rows]


@router.post("")
async def create_workspace(body: NewWorkspace, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
    return _ws_json(await wsp.create(db, user.id, body.name))


@router.post("/{ws_id}/open")
async def open_workspace(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    wsp.open_ws(db, ws)
    return _ws_json(ws)


@router.get("/{ws_id}/files")
async def tree(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    return {"files": wsp.list_files(wsp.root(ws)), "pending": len(wsp.pending(db, ws))}


@router.get("/{ws_id}/file")
async def read_file(ws_id: str, path: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    return {"path": path, "content": _guard(lambda: wsp.read_text(wsp.safe_path(wsp.root(ws), path)))}


@router.put("/{ws_id}/file")
async def save_file(ws_id: str, body: FileBody, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    """The user typed this in the editor, so it is written straight away (and is still undoable)."""
    ws = await owned(db, ws_id, user)
    if len(body.content.encode()) > settings().max_upload_bytes:
        raise HTTPException(413, "file is too large")
    target = _guard(lambda: wsp.safe_path(wsp.root(ws), body.path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"commit": await wsp.commit(wsp.root(ws), f"edit {body.path}")}


@router.get("/{ws_id}/changes")
async def changes(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    ws = await owned(db, ws_id, user)
    return [wsp.change_json(c) for c in wsp.pending(db, ws)]


@router.post("/{ws_id}/changes/apply")
async def apply(ws_id: str, body: Ids, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Clicking Accept in the diff view is the user's explicit approval for exactly these changes."""
    ws = await owned(db, ws_id, user)
    try:
        return await wsp.apply_changes(db, ws, body.ids or None)
    except wsp.WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/{ws_id}/changes/reject")
async def reject(ws_id: str, body: Ids, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    return {"rejected": wsp.reject_changes(db, ws, body.ids or None)}


@router.post("/{ws_id}/undo")
async def undo(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    try:
        return {"undone": await wsp.undo(wsp.root(ws))}
    except wsp.WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/{ws_id}/history")
async def history(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return await wsp.history(wsp.root(await owned(db, ws_id, user)), 50)


@router.get("/{ws_id}/index")
async def index(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return wsp.build_index(wsp.root(await owned(db, ws_id, user)))


@router.post("/{ws_id}/import")
async def import_zip(ws_id: str, file: UploadFile, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    blob = await file.read(settings().max_workspace_bytes + 1)
    count = _guard(lambda: wsp.import_zip(wsp.root(ws), blob))
    return {"files": count, "commit": await wsp.commit(wsp.root(ws), f"import {file.filename}")}


@router.post("/{ws_id}/import/github")
async def import_github(ws_id: str, body: GithubImport, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    match = GITHUB.match(body.url.strip())
    if not match or ".." in body.url or match["path"] or match["pr"]:
        raise HTTPException(400, "give a public https://github.com/<owner>/<repo> URL")
    url = f"https://codeload.github.com/{match['owner']}/{match['repo']}/zip/HEAD"
    cap, blob = settings().max_workspace_bytes, b""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http, http.stream("GET", url) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                blob += chunk
                if len(blob) > cap:
                    raise HTTPException(413, "that repository is too large")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not download the repository: {exc}") from None
    count = _guard(lambda: wsp.import_zip(wsp.root(ws), blob))
    return {"files": count, "commit": await wsp.commit(wsp.root(ws), f"import {match['owner']}/{match['repo']}")}


@router.get("/{ws_id}/export")
async def export(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    ws = await owned(db, ws_id, user)
    return Response(wsp.export_zip(wsp.root(ws)), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{ws.name}.zip"'})


# ---------------------------------------------------------------- running code (phase 3)

class RunBody(BaseModel):
    command: str = Field(default="", max_length=2000)


class ServerBody(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    port: int = Field(default=0, ge=0, le=65535)


def _stream(work, command: str) -> StreamingResponse:
    """Run in the sandbox and stream output as it arrives; the final line reports the exit code."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def push(text: str) -> None:
        await queue.put(text)

    async def worker() -> None:
        try:
            await queue.put(f"$ {command}\n")
            result = await sandbox.run(work, command, push)
            await wsp.commit(work, f"ran {command[:60]}")
            await queue.put(f"\n[exit {result.code} · {result.seconds}s]\n")
        except sandbox.SandboxError as exc:
            await queue.put(f"[sandbox] {exc}\n")
        finally:
            await queue.put(None)

    async def body():
        task = asyncio.create_task(worker())
        try:
            while (chunk := await queue.get()) is not None:
                yield chunk
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(body(), media_type="text/plain; charset=utf-8")


@router.post("/{ws_id}/run")
async def run_command(ws_id: str, body: RunBody, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> StreamingResponse:
    """The user typed this command in the terminal panel, so it runs (inside the sandbox) without a prompt."""
    ws = await owned(db, ws_id, user)
    if not body.command.strip():
        raise HTTPException(400, "type a command")
    return _stream(wsp.root(ws), body.command)


@router.post("/{ws_id}/tests")
async def run_tests(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> StreamingResponse:
    from app.skills.run_tools import detect_tests

    ws = await owned(db, ws_id, user)
    return _stream(wsp.root(ws), _guard(lambda: detect_tests(wsp.root(ws))))


def _server_json(server: sandbox.Server) -> dict:
    return {"id": server.id, "command": server.command, "port": server.port, "url": server.url,
            "running": server.running}


@router.get("/{ws_id}/servers")
async def servers(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    ws = await owned(db, ws_id, user)
    return [_server_json(s) for s in sandbox.SERVERS.values() if s.workspace_id == ws.id]


@router.post("/{ws_id}/servers")
async def start_server(ws_id: str, body: ServerBody, user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    try:
        return _server_json(await sandbox.start_server(wsp.root(ws), ws.id, user.id, body.command, body.port))
    except sandbox.SandboxError as exc:
        raise HTTPException(400, str(exc)) from None


def _my_server(ws: Workspace, server_id: str) -> sandbox.Server:
    server = sandbox.SERVERS.get(server_id)
    if not server or server.workspace_id != ws.id:
        raise HTTPException(404, "server not found")
    return server


@router.delete("/{ws_id}/servers/{server_id}")
async def stop_server(ws_id: str, server_id: str, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    return {"stopped": await sandbox.stop_server(_my_server(ws, server_id).id)}


@router.get("/{ws_id}/servers/{server_id}/logs")
async def server_logs(ws_id: str, server_id: str, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    ws = await owned(db, ws_id, user)
    return {"log": sandbox.read_log(_my_server(ws, server_id))}


# ---------------------------------------------------------------- preview URLs

preview = APIRouter()
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade", "content-encoding",
               "content-length", "host"}
# Previews are served on VoxAgent's own origin. Without this, a preview's scripts could call /auth/refresh with the
# viewer's session cookie and take over their account; the sandbox gives the page an origin of its own.
PREVIEW_SANDBOX = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"


def hidden(path: str) -> bool:
    """.git, .venv, .env, .vox: a preview link is shareable, and a plain file server would hand these out."""
    return any(part.startswith(".") for part in path.split("/"))


@preview.api_route("/preview/{server_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def preview_proxy(server_id: str, path: str, request: Request) -> Response:
    """Shareable preview: anyone with the unguessable link reaches the app until its server stops."""
    server = sandbox.SERVERS.get(server_id)
    if not server or not server.running:
        raise HTTPException(404, "this preview has stopped")
    if hidden(path):
        raise HTTPException(404, "not found")
    server.last_access = time.monotonic()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS | {"cookie", "authorization"}}
    url = f"http://{server.host}:{server.port}/{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            upstream = await http.request(request.method, url, params=request.query_params, headers=headers,
                                          content=await request.body())
    except httpx.HTTPError:
        raise HTTPException(502, "the app is not answering yet; check its logs") from None
    out = {k.lower(): v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS | {"set-cookie"}}
    out["content-security-policy"] = ", ".join(filter(None, [out.get("content-security-policy"), PREVIEW_SANDBOX]))
    return Response(upstream.content, upstream.status_code, out)


@preview.websocket("/preview/{server_id}/{path:path}")
async def preview_socket(websocket: WebSocket, server_id: str, path: str) -> None:
    """Live-reload and app sockets: pipe frames both ways between the browser and the sandboxed app."""
    server = sandbox.SERVERS.get(server_id)
    if not server or not server.running or hidden(path):
        await websocket.close(4404)
        return
    await websocket.accept()
    server.last_access = time.monotonic()
    query = f"?{websocket.url.query}" if websocket.url.query else ""
    try:
        async with websockets.connect(f"ws://{server.host}:{server.port}/{path}{query}", max_size=8_000_000) as upstream:

            async def downstream() -> None:
                async for message in upstream:
                    await (websocket.send_bytes(message) if isinstance(message, bytes) else websocket.send_text(message))

            async def upstream_pump() -> None:
                while True:
                    event = await websocket.receive()
                    if event["type"] == "websocket.disconnect":
                        return
                    await upstream.send(event["bytes"] if event.get("bytes") is not None else event.get("text") or "")

            tasks = [asyncio.create_task(downstream()), asyncio.create_task(upstream_pump())]
            _, rest = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in rest:
                task.cancel()
    except (OSError, websockets.WebSocketException):
        pass
    with contextlib.suppress(RuntimeError):
        await websocket.close()
