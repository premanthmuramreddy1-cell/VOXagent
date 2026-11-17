"""Per-user project workspaces: a real folder on disk, versioned with git.

Every applied change is one git commit, so "undo that" is `git revert HEAD`.
Edits are staged as PendingChange rows first; nothing touches disk until the user accepts.
"""

import ast
import asyncio
import difflib
import io
import json
import os
import re
import unicodedata
import zipfile
import zlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import PendingChange, Workspace, now

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".vox"}
NOTES = "NOTES.md"
MAX_LIST = 2000
MAX_READ = 200_000
MAX_SEARCH_HITS = 100
GIT_ENV = {
    "GIT_AUTHOR_NAME": "VoxAgent", "GIT_AUTHOR_EMAIL": "agent@voxagent.local",
    "GIT_COMMITTER_NAME": "VoxAgent", "GIT_COMMITTER_EMAIL": "agent@voxagent.local",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
}


class WorkspaceError(ValueError):
    """A request the workspace refuses: bad path, missing file, ambiguous edit."""


# ---------------------------------------------------------------- location and paths

def root(ws: Workspace) -> Path:
    return Path(settings().data_dir).resolve() / "workspaces" / ws.user_id / ws.id


def safe_path(base: Path, rel: str) -> Path:
    """Resolve a user-supplied relative path, refusing anything outside the workspace or inside .git."""
    rel = rel.strip().replace("\\", "/").lstrip("/")
    if not rel or "\0" in rel:
        raise WorkspaceError("a file path is required")
    # The OS refuses longer names with an OSError, which would surface as a server error instead of an answer.
    if len(rel.encode()) > 1024 or any(len(part.encode()) > 255 for part in rel.split("/")):
        raise WorkspaceError("that file path is too long")
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or path == base:
        raise WorkspaceError(f"{rel} is outside the workspace")
    if ".git" in (part.lower() for part in path.relative_to(base).parts):  # macOS opens .GIT as .git
        raise WorkspaceError("the .git folder is managed by VoxAgent")
    return path


def rel(base: Path, path: Path) -> str:
    return path.relative_to(base).as_posix()


# ---------------------------------------------------------------- git

async def git(base: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=base, env={**os.environ, **GIT_ENV},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def ensure_repo(base: Path) -> None:
    if not (base / ".git").exists():
        base.mkdir(parents=True, exist_ok=True)
        await git(base, "init", "-q", "-b", "main")
        await git(base, "commit", "-q", "--allow-empty", "-m", "workspace created")
    exclude = base / ".git" / "info" / "exclude"
    if not exclude.exists() or ".vox/" not in exclude.read_text():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        # installed packages, caches and sandbox state are rebuilt, never versioned
        exclude.write_text("".join(f"{d}/\n" for d in sorted(SKIP_DIRS - {".git"})))


async def commit(base: Path, message: str) -> str:
    """Commit everything; returns the short sha, or "" when nothing changed."""
    await git(base, "add", "-A")
    code, _ = await git(base, "diff", "--cached", "--quiet")
    if code == 0:
        return ""
    await git(base, "commit", "-q", "-m", message[:200])
    return (await git(base, "rev-parse", "--short", "HEAD"))[1]


async def history(base: Path, limit: int = 15) -> list[dict]:
    code, out = await git(base, "log", f"-{limit}", "--pretty=format:%h\t%cI\t%s")
    if code:
        return []
    rows = [line.split("\t", 2) for line in out.splitlines() if line.count("\t") >= 2]
    return [{"sha": sha, "when": when, "message": msg} for sha, when, msg in rows]


async def undo(base: Path) -> str:
    """Revert the latest change. Reverts are themselves commits, so undo can be undone."""
    log = await history(base, 2)
    if len(log) < 2:
        raise WorkspaceError("there is nothing to undo yet")
    code, out = await git(base, "revert", "--no-edit", "HEAD")
    if code:
        await git(base, "revert", "--abort")
        raise WorkspaceError(f"could not undo: {out[-300:]}")
    return log[0]["message"]


# ---------------------------------------------------------------- workspace rows

async def create(db: Session, user_id: str, name: str) -> Workspace:
    # letters, digits and the marks that complete them: Indic vowel signs are marks, which \w leaves out
    keep = (c for c in name if c.isalnum() or c in " ._-" or unicodedata.category(c)[0] == "M")
    name = "".join(keep).strip()[:60] or "project"
    ws = Workspace(user_id=user_id, name=name)
    db.add(ws)
    db.commit()
    base = root(ws)
    await ensure_repo(base)
    (base / NOTES).write_text(f"# {name}\n\n## Decisions\n\n## Structure\n\n## To-dos\n", encoding="utf-8")
    await commit(base, "add project notes")
    return ws


async def current(db: Session, user_id: str) -> Workspace:
    """The workspace the user opened last; a fresh one is created on first use."""
    ws = db.scalar(select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.opened_at.desc()))
    if ws is None:
        return await create(db, user_id, "my-project")
    await ensure_repo(root(ws))
    return ws


def open_ws(db: Session, ws: Workspace) -> None:
    ws.opened_at = now()
    db.commit()


# ---------------------------------------------------------------- reading

def walk(base: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if not path.is_symlink():
                found.append(path)
            if len(found) >= MAX_LIST:
                return found
    return found


def list_files(base: Path, folder: str = "") -> list[str]:
    start = safe_path(base, folder) if folder.strip(" /.") else base
    return [rel(base, p) for p in walk(base) if p.is_relative_to(start)]


def read_text(path: Path) -> str:
    if not path.is_file():
        raise WorkspaceError(f"{path.name} does not exist")
    blob = path.read_bytes()[: MAX_READ + 1]
    if b"\0" in blob[:8000]:
        raise WorkspaceError(f"{path.name} is a binary file")
    return blob.decode("utf-8", errors="replace")


def search(base: Path, query: str, regex: bool = False) -> list[dict]:
    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error as exc:
        raise WorkspaceError(f"bad pattern: {exc}") from None
    hits: list[dict] = []
    for path in walk(base):
        try:
            text = read_text(path)
        except WorkspaceError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append({"path": rel(base, path), "line": number, "text": line.strip()[:200]})
                if len(hits) >= MAX_SEARCH_HITS:
                    return hits
    return hits


# ---------------------------------------------------------------- staged changes

def unified(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}")
    return "".join(lines)


def pending(db: Session, ws: Workspace) -> list[PendingChange]:
    return list(db.scalars(
        select(PendingChange).where(PendingChange.workspace_id == ws.id).order_by(PendingChange.created_at)
    ))


def _staged_content(db: Session, ws: Workspace, path: str) -> str | None:
    """What `path` will contain once pending changes apply: staged text, or the file on disk."""
    for change in reversed(pending(db, ws)):
        if change.kind == "write" and change.path == path:
            return change.content
        if change.kind == "delete" and change.path == path:
            return None
    target = safe_path(root(ws), path)
    return read_text(target) if target.exists() else None


def _drop_staged(db: Session, ws: Workspace, path: str) -> None:
    for change in pending(db, ws):
        if change.path == path and change.kind in ("write", "delete"):
            db.delete(change)


def _disk(ws: Workspace, path: str) -> str:
    target = safe_path(root(ws), path)
    return read_text(target) if target.exists() else ""


def stage_write(db: Session, ws: Workspace, path: str, content: str) -> PendingChange:
    path = rel(root(ws), safe_path(root(ws), path))
    if len(content.encode()) > settings().max_upload_bytes:
        raise WorkspaceError(f"{path} is too large")
    _drop_staged(db, ws, path)
    change = PendingChange(workspace_id=ws.id, kind="write", path=path, content=content,
                           diff=unified(path, _disk(ws, path), content))
    db.add(change)
    db.commit()
    return change


def apply_edit(text: str, find: str, replace: str, replace_all: bool = False) -> str:
    if not find:
        raise WorkspaceError("the text to find is empty")
    count = text.count(find)
    if count == 0:
        raise WorkspaceError(f"could not find {find[:60]!r}")
    if count > 1 and not replace_all:
        raise WorkspaceError(f"{find[:60]!r} appears {count} times; give more context or replace all")
    return text.replace(find, replace)


def stage_edits(db: Session, ws: Workspace, edits: list[dict]) -> list[PendingChange]:
    """Apply many exact find-and-replace edits across files. All validate before any is staged."""
    results: dict[str, str] = {}
    for edit in edits:
        path = rel(root(ws), safe_path(root(ws), str(edit["path"])))
        text = results.get(path)
        if text is None:
            text = _staged_content(db, ws, path)
            if text is None:
                raise WorkspaceError(f"{path} does not exist")
        results[path] = apply_edit(text, str(edit["find"]), str(edit["replace"]), bool(edit.get("replace_all")))
    return [stage_write(db, ws, path, text) for path, text in results.items()]


def stage_rename_symbol(db: Session, ws: Workspace, old: str, new: str) -> list[PendingChange]:
    """Rename an identifier everywhere it appears as a whole word."""
    if not re.fullmatch(r"[A-Za-z_]\w*", old) or not re.fullmatch(r"[A-Za-z_]\w*", new):
        raise WorkspaceError("both names must be identifiers")
    word = re.compile(rf"\b{re.escape(old)}\b")
    changes = []
    for path in walk(root(ws)):
        name = rel(root(ws), path)
        try:
            text = _staged_content(db, ws, name)
        except WorkspaceError:
            continue
        if text and word.search(text):
            changes.append(stage_write(db, ws, name, word.sub(new, text)))
    if not changes:
        raise WorkspaceError(f"{old} does not appear in the project")
    return changes


def stage_delete(db: Session, ws: Workspace, path: str) -> PendingChange:
    target = safe_path(root(ws), path)
    path = rel(root(ws), target)
    if not target.exists():
        raise WorkspaceError(f"{path} does not exist")
    _drop_staged(db, ws, path)
    old = read_text(target) if target.is_file() else ""
    change = PendingChange(workspace_id=ws.id, kind="delete", path=path, diff=unified(path, old, ""))
    db.add(change)
    db.commit()
    return change


def stage_rename(db: Session, ws: Workspace, path: str, new_path: str) -> PendingChange:
    source, dest = safe_path(root(ws), path), safe_path(root(ws), new_path)
    if not source.exists():
        raise WorkspaceError(f"{path} does not exist")
    if dest.exists():
        raise WorkspaceError(f"{new_path} already exists")
    change = PendingChange(workspace_id=ws.id, kind="rename", path=rel(root(ws), source),
                           new_path=rel(root(ws), dest), diff=f"rename {path} -> {new_path}\n")
    db.add(change)
    db.commit()
    return change


def change_json(change: PendingChange) -> dict:
    return {"id": change.id, "kind": change.kind, "path": change.path, "new_path": change.new_path,
            "diff": change.diff}


async def apply_changes(db: Session, ws: Workspace, ids: list[str] | None = None) -> dict:
    """Write accepted changes to disk and commit them as one undoable step."""
    chosen = [c for c in pending(db, ws) if ids is None or c.id in ids]
    if not chosen:
        raise WorkspaceError("there are no staged changes to apply")
    base, done, failure = root(ws), [], ""
    for change in chosen:
        try:
            _apply_one(base, change)
        except (OSError, WorkspaceError) as exc:
            failure = f"could not {change.kind} {change.path}: {getattr(exc, 'strerror', None) or exc}"
            break
        db.delete(change)
        done.append(change)
    db.commit()
    sha = None
    if done:
        summary = ", ".join(sorted({f"{c.kind} {c.path}" for c in done}))[:180]
        sha = await commit(base, f"voxagent: {summary}")
    if failure:  # the applied ones stay applied and committed; the rest stay staged to retry
        raise WorkspaceError(f"{failure} ({len(done)} applied, {len(chosen) - len(done)} still staged)")
    return {"applied": len(done), "commit": sha, "remaining": len(pending(db, ws))}


def _apply_one(base: Path, change: PendingChange) -> None:
    target = safe_path(base, change.path)
    if change.kind == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content or "", encoding="utf-8")
    elif change.kind == "delete" and target.exists():
        if target.is_dir():
            for path in sorted(target.rglob("*"), reverse=True):
                path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()
            target.rmdir()
        else:
            target.unlink()
    elif change.kind == "rename":
        dest = safe_path(base, change.new_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        target.rename(dest)


def reject_changes(db: Session, ws: Workspace, ids: list[str] | None = None) -> int:
    chosen = [c for c in pending(db, ws) if ids is None or c.id in ids]
    for change in chosen:
        db.delete(change)
    db.commit()
    return len(chosen)


# ---------------------------------------------------------------- import / export

def import_zip(base: Path, blob: bytes) -> int:
    limit, total, count = settings().max_workspace_bytes, 0, 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise WorkspaceError("that is not a zip file, or the upload was cut off") from None
    names = [i for i in archive.infolist() if not i.is_dir()]
    files: list[tuple[Path, bytes]] = []
    tops = {i.filename.split("/", 1)[0] for i in names}
    nested = all("/" in i.filename for i in names)
    prefix = f"{tops.pop()}/" if len(tops) == 1 and nested else ""  # strip one top folder (GitHub zips have one)
    for info in names:
        name = info.filename[len(prefix):]
        parts = Path(name).parts
        if not name or any(p in SKIP_DIRS for p in parts) or (info.external_attr >> 16) & 0o170000 == 0o120000:
            continue
        total += info.file_size
        if total > limit:
            raise WorkspaceError(f"the project is larger than {limit // 1_000_000} MB")
        target = safe_path(base, name)  # refuses zip-slip paths like ../../etc
        try:
            files.append((target, archive.read(info)))
        except (zipfile.BadZipFile, zlib.error, EOFError):
            raise WorkspaceError(f"the zip file is damaged at {name}") from None
    # written only once every file checked out, so a refused import leaves the workspace untouched
    for target, data in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        count += 1
    return count


def export_zip(base: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in walk(base):
            archive.write(path, rel(base, path))
    return buffer.getvalue()


# ---------------------------------------------------------------- project index

SYMBOL_PATTERNS = {
    (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"): [
        (r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)", "function"),
        (r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)", "class"),
        (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", "function"),
        (r"^\s*(?:app|router)\.(?:get|post|put|patch|delete|use)\(\s*['\"]([^'\"]+)", "route"),
    ],
    (".java", ".kt", ".cs"): [
        (r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:final\s+)?(?:class|interface|enum)\s+(\w+)",
         "class"),
        (r"^\s*(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\], ]+\s+(\w+)\s*\(", "function"),
    ],
    (".go",): [(r"^func\s+(?:\([^)]*\)\s*)?(\w+)", "function"), (r"^type\s+(\w+)\s+struct", "class")],
    (".rs",): [(r"^\s*(?:pub\s+)?fn\s+(\w+)", "function"), (r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)", "class")],
    (".c", ".h", ".cpp", ".hpp", ".cc"): [
        (r"^[A-Za-z_][\w\s\*&:<>]*?\b(\w+)\s*\([^;]*\)\s*\{?\s*$", "function"),
        (r"^\s*(?:class|struct)\s+(\w+)", "class"),
    ],
    (".rb",): [(r"^\s*def\s+(?:self\.)?(\w+)", "function"), (r"^\s*class\s+(\w+)", "class")],
    (".php",): [(r"^\s*(?:public\s+|private\s+|static\s+)*function\s+(\w+)", "function"), (r"^\s*class\s+(\w+)", "class")],
}
IMPORT_RE = re.compile(r"""^\s*(?:import\s+(?:.+?\s+from\s+)?['"]([^'"]+)['"]|(?:const|let|var)\s+.+?require\(['"]([^'"]+)['"]\)|from\s+([\w.]+)\s+import|import\s+([\w.]+))""")


def _python_symbols(text: str) -> list[tuple[str, str, int]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append((node.name, "function", node.lineno))
        elif isinstance(node, ast.ClassDef):
            found.append((node.name, "class", node.lineno))
    return found


def _symbols(name: str, text: str) -> list[tuple[str, str, int]]:
    if name.endswith(".py"):
        return _python_symbols(text)
    for suffixes, patterns in SYMBOL_PATTERNS.items():
        if name.endswith(suffixes):
            found = []
            for number, line in enumerate(text.splitlines(), 1):
                for pattern, kind in patterns:
                    if (match := re.match(pattern, line)) and match.group(1) not in ("if", "for", "while", "switch"):
                        found.append((match.group(1), kind, number))
            return found
    return []


def _dependencies(base: Path) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    package = base / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text())
            deps["npm"] = sorted({*data.get("dependencies", {}), *data.get("devDependencies", {})})
        except ValueError:
            pass
    requirements = base / "requirements.txt"
    if requirements.is_file():
        deps["pip"] = [re.split(r"[<>=!~\[ ;]", line)[0] for line in requirements.read_text().splitlines()
                       if line.strip() and not line.startswith(("#", "-"))]
    pyproject = base / "pyproject.toml"
    if pyproject.is_file():
        found = re.findall(r'^\s*"([A-Za-z0-9_.-]+)', pyproject.read_text(), re.MULTILINE)
        deps.setdefault("pip", []).extend(found)
    gomod = base / "go.mod"
    if gomod.is_file():
        deps["go"] = re.findall(r"^\s*([\w./-]+\.[\w./-]+)\s+v", gomod.read_text(), re.MULTILINE)
    return deps


def build_index(base: Path) -> dict:
    symbols, imports = [], {}
    files = walk(base)
    for path in files:
        name = rel(base, path)
        try:
            text = read_text(path)
        except WorkspaceError:
            continue
        symbols += [{"name": n, "kind": k, "path": name, "line": line} for n, k, line in _symbols(name, text)]
        found = sorted({next(g for g in m.groups() if g) for line in text.splitlines()[:200]
                        if (m := IMPORT_RE.match(line))})
        if found:
            imports[name] = found
    return {"files": [rel(base, p) for p in files], "symbols": symbols, "imports": imports,
            "dependencies": _dependencies(base)}


def lookup(base: Path, question: str, limit: int = 12) -> list[dict]:
    """Find the places most relevant to a question: matching symbols first, then text hits."""
    words = [w for w in re.findall(r"[A-Za-z_]\w{2,}", question.lower())
             if w not in {"where", "what", "which", "the", "handled", "does", "defined", "file", "code", "how"}]
    if not words:
        return []
    index = build_index(base)
    scored = []
    for sym in index["symbols"]:
        hay = f"{sym['name']} {sym['path']}".lower()
        score = sum(2 for w in words if w in hay)
        if score:
            scored.append((score, sym))
    hits = [s for _, s in sorted(scored, key=lambda pair: -pair[0])][:limit]
    for word in words[:3]:
        for hit in search(base, word)[:limit]:
            if len(hits) >= limit * 2:
                break
            hits.append({"name": hit["text"], "kind": "text", "path": hit["path"], "line": hit["line"]})
    return hits
