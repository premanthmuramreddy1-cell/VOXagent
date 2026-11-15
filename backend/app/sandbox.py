"""Run user code in an isolated sandbox, never directly on the main server.

Backends (VOX_SANDBOX):
- docker:   one throwaway container per command, CPU/memory/pids limits, workspace mounted at /work.
- seatbelt: macOS sandbox-exec profile for development without Docker. Writes only inside the
            workspace; cannot read the app's data, database or other workspaces; cannot reach localhost
            (so not the API, the database or other services). Outbound internet stays open for installs.
- off:      running code is refused.
"""

import asyncio
import os
import secrets
import shutil
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

Output = Callable[[str], Awaitable[None]]
APP_ROOT = Path(__file__).resolve().parent.parent  # the backend folder: code, .env and the database


class SandboxError(RuntimeError):
    """The sandbox refused or could not run the command."""


@dataclass
class RunResult:
    code: int
    output: str
    timed_out: bool
    seconds: float

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


@dataclass
class Server:
    id: str
    workspace_id: str
    user_id: str
    command: str
    port: int
    url: str
    log: Path
    proc: asyncio.subprocess.Process | None = None
    container: str = ""
    tunnel: asyncio.subprocess.Process | None = None
    last_access: float = field(default_factory=time.monotonic)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


SERVERS: dict[str, Server] = {}


def backend() -> str:
    mode = settings().sandbox
    if mode == "docker" and not shutil.which("docker"):
        raise SandboxError("the Docker sandbox is configured but Docker is not installed")
    if mode == "seatbelt" and not shutil.which("sandbox-exec"):
        raise SandboxError("the seatbelt sandbox needs macOS sandbox-exec")
    if mode not in ("docker", "seatbelt"):
        raise SandboxError("running code is turned off on this server (VOX_SANDBOX=off)")
    return mode


def _seatbelt_profile(work: Path) -> str:
    """Last matching rule wins in SBPL, so broad denials come first and the workspace is re-allowed last."""
    private = {Path(settings().data_dir).resolve(), APP_ROOT, Path.home() / ".ssh", Path.home() / ".aws",
               Path.home() / "Library" / "Keychains"}
    deny_reads = "".join(f'(deny file-read* (subpath "{p}"))' for p in sorted(private))
    return (
        "(version 1)(allow default)"
        '(deny file-write* (subpath "/"))'
        f"{deny_reads}"
        f'(allow file-read* file-write* (subpath "{work}"))'
        '(allow file-write* (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/"))'
        '(deny network-outbound (remote ip "localhost:*"))'
    )


def _env(work: Path, port: int = 0) -> dict[str, str]:
    """A clean environment: no server secrets leak into user code; caches and temp files stay in the workspace."""
    for sub in ("home", "tmp"):
        (work / ".vox" / sub).mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{work}/.venv/bin:{work}/node_modules/.bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(work / ".vox" / "home"), "TMPDIR": str(work / ".vox" / "tmp"), "LANG": "en_US.UTF-8",
        "TERM": "dumb", "CI": "1", "PYTHONUNBUFFERED": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "npm_config_update_notifier": "false", "npm_config_cache": str(work / ".vox" / "npm"),
        "GOPATH": str(work / ".vox" / "go"), "GOCACHE": str(work / ".vox" / "gocache"),
    }
    if port:
        env["PORT"] = str(port)
    return env


def _command(work: Path, command: str, name: str = "", port: int = 0) -> list[str]:
    if backend() == "docker":
        cfg = settings()
        cmd = ["docker", "run", "-d" if name else "--rm", "--init",
               f"--cpus={cfg.sandbox_cpus}", f"--memory={cfg.sandbox_memory_mb}m", "--pids-limit=256",
               "--cap-drop=ALL", "--security-opt=no-new-privileges", "-v", f"{work}:/work", "-w", "/work",
               "-e", "HOME=/work/.vox/home", "-e", "CI=1"]
        if name:
            cmd += ["--name", name, "-p", f"127.0.0.1:{port}:{port}", "-e", f"PORT={port}"]
        return [*cmd, cfg.sandbox_image, "sh", "-lc", command]
    return ["sandbox-exec", "-p", _seatbelt_profile(work), "/bin/sh", "-c", command]


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except (TimeoutError, RuntimeError):  # RuntimeError: process created on another event loop
            pass


async def run(work: Path, command: str, on_output: Output | None = None, timeout: int = 0) -> RunResult:
    """Run one command in the sandbox, streaming output; kills the whole process group on timeout."""
    if not command.strip():
        raise SandboxError("no command given")
    cfg = settings()
    timeout = timeout or cfg.sandbox_timeout_s
    start, chunks, size = time.monotonic(), [], 0
    proc = await asyncio.create_subprocess_exec(
        *_command(work, command), cwd=work, env=_env(work), start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )

    async def pump() -> None:
        nonlocal size
        assert proc.stdout
        while chunk := await proc.stdout.read(4096):
            text = chunk.decode(errors="replace")
            if size < cfg.sandbox_output_chars:
                chunks.append(text[: cfg.sandbox_output_chars - size])
                size += len(text)
                if on_output:
                    await on_output(text)

    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except TimeoutError:
        timed_out = True
        await _kill(proc)
        if on_output:
            await on_output(f"\n[stopped after {timeout}s]\n")
    except asyncio.CancelledError:
        await _kill(proc)
        raise
    output = "".join(chunks) + ("\n[output truncated]" if size >= cfg.sandbox_output_chars else "")
    return RunResult(proc.returncode if proc.returncode is not None else -1, output, timed_out,
                     round(time.monotonic() - start, 2))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _public_url(server: Server) -> str:
    """Expose the preview publicly through a cloudflared quick tunnel when configured."""
    if settings().tunnel != "cloudflared" or not shutil.which("cloudflared"):
        return ""
    server.tunnel = await asyncio.create_subprocess_exec(
        "cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{server.port}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, start_new_session=True,
    )
    assert server.tunnel.stderr
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            line = (await asyncio.wait_for(server.tunnel.stderr.readline(), 5)).decode(errors="replace")
        except TimeoutError:
            continue
        for word in line.split():
            if word.startswith("https://") and "trycloudflare.com" in word:
                return word.strip("|")
    return ""


async def start_server(work: Path, workspace_id: str, user_id: str, command: str, port: int = 0) -> Server:
    port = port or free_port()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SandboxError(f"port {port} is already in use; pick another")
    ident = secrets.token_urlsafe(12)
    log = work / ".vox" / f"server-{ident}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    name = "vox-" + "".join(c for c in ident.lower() if c.isalnum()) if backend() == "docker" else ""
    with log.open("wb") as handle:
        proc = await asyncio.create_subprocess_exec(
            *_command(work, command, name, port), cwd=work, env=_env(work, port), start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=handle, stderr=asyncio.subprocess.STDOUT,
        )
    server = Server(ident, workspace_id, user_id, command, port, f"{settings().public_url}/preview/{ident}/",
                    log, proc, name)
    SERVERS[ident] = server
    server.url = await _public_url(server) or server.url
    return server


async def stop_server(ident: str) -> bool:
    server = SERVERS.pop(ident, None)
    if not server:
        return False
    for proc in (server.proc, server.tunnel):
        if proc:
            await _kill(proc)
    if server.container:
        stop = await asyncio.create_subprocess_exec("docker", "rm", "-f", server.container,
                                                    stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await stop.wait()
    return True


def read_log(server: Server, chars: int = 6000) -> str:
    return server.log.read_text(errors="replace")[-chars:] if server.log.exists() else ""


async def reap_idle() -> int:
    """Stop servers nobody has opened for a while; their files stay in the workspace."""
    idle = settings().sandbox_idle_s
    stale = [s.id for s in SERVERS.values() if not s.running or time.monotonic() - s.last_access > idle]
    for ident in stale:
        await stop_server(ident)
    return len(stale)
