"""One-command deploy of a finished workspace (the deploy skill asks for an explicit yes first).

VOX_DEPLOY_PROVIDER:
- render:<deploy hook URL>  Render builds from the connected Git repo; publish to GitHub first, then this
                           triggers a build. The hook URL is a secret and lives in the environment.
- vercel                   runs `vercel deploy --prod` from the workspace with VERCEL_TOKEN (CLI uploads the
                           source; Vercel builds it remotely, nothing runs on this server).
"""

import asyncio
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from app.config import settings

Emit = Callable[..., Awaitable[None]]


class DeployError(RuntimeError):
    pass


async def deploy(work: Path, emit: Emit) -> dict:
    provider = settings().deploy_provider
    if provider.startswith("render:"):
        hook = provider.split(":", 1)[1]
        if not hook.startswith("https://api.render.com/deploy/"):
            raise DeployError("the Render deploy hook must start with https://api.render.com/deploy/")
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.post(hook)
        if response.status_code >= 300:
            raise DeployError(f"Render refused the deploy ({response.status_code})")
        return {"message": "Render started a new deploy from your repository.", "provider": "render"}
    if provider == "vercel":
        if not shutil.which("vercel") or not os.environ.get("VERCEL_TOKEN"):
            raise DeployError("the vercel CLI and VERCEL_TOKEN are needed for Vercel deploys")
        proc = await asyncio.create_subprocess_exec(
            "vercel", "deploy", "--prod", "--yes", "--token", os.environ["VERCEL_TOKEN"], cwd=work,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 600)
        text = out.decode(errors="replace")
        await emit("terminal.output", text=text[-4000:])
        urls = re.findall(r"https://[\w.-]+\.vercel\.app", text)
        if proc.returncode or not urls:
            raise DeployError(f"Vercel deploy failed: {text[-300:]}")
        return {"message": f"Deployed to {urls[-1]}", "provider": "vercel", "url": urls[-1]}
    raise DeployError("no hosting provider is configured (set VOX_DEPLOY_PROVIDER)")
