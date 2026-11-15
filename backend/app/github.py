"""GitHub integration: connect (OAuth or a personal access token), comment on pull requests,
push workspace files to a new branch and open a pull request, publish a workspace as a new repository.

Pushes use the Git Data API (blobs, tree, commit, ref), so no git credentials ever touch the sandbox.
"""

import re
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import load_secret, save_secret
from app.google import read_state, state_token

API = "https://api.github.com"
PR_URL = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)")
REPO = re.compile(r"^(?:https://github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")
TRANSPORT: httpx.AsyncBaseTransport | None = None
MAX_FILES = 300


class GithubError(RuntimeError):
    pass


def connect_url(user_id: str) -> str:
    if not settings().github_client_id:
        raise GithubError("GitHub OAuth is not configured; paste a personal access token on the Account page instead")
    return "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": settings().github_client_id, "scope": "repo",
        "redirect_uri": f"{settings().public_url}/integrations/github/callback",
        "state": state_token(user_id, "github"),
    })


async def finish_connect(db: Session, code: str, state: str) -> str:
    user_id = read_state(state, "github")
    async with httpx.AsyncClient(timeout=20, transport=TRANSPORT) as http:
        response = await http.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"},
                                   data={"client_id": settings().github_client_id,
                                         "client_secret": settings().github_client_secret, "code": code})
    token = response.json().get("access_token") if response.status_code == 200 else None
    if not token:
        raise GithubError("GitHub did not return a token")
    save_secret(db, user_id, "github", {"token": token})
    return user_id


async def save_token(db: Session, user_id: str, token: str) -> str:
    """Check a pasted token against GitHub before storing it; returns the GitHub login."""
    async with httpx.AsyncClient(timeout=20, transport=TRANSPORT) as http:
        response = await http.get(f"{API}/user", headers=_headers(token))
    if response.status_code != 200:
        raise GithubError("GitHub did not accept that token")
    save_secret(db, user_id, "github", {"token": token})
    return str(response.json().get("login", ""))


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


class Client:
    def __init__(self, token: str):
        self.token = token

    @classmethod
    def for_user(cls, db: Session, user_id: str) -> "Client":
        stored = load_secret(db, user_id, "github")
        if not stored:
            raise GithubError("connect GitHub first (Account page)")
        return cls(stored["token"])

    async def call(self, method: str, path: str, json: dict | None = None, ok: tuple = (200, 201)) -> dict:
        async with httpx.AsyncClient(timeout=30, transport=TRANSPORT) as http:
            response = await http.request(method, f"{API}{path}", headers=_headers(self.token), json=json)
        if response.status_code not in ok:
            raise GithubError(f"GitHub API {response.status_code}: {response.text[:200]}")
        return response.json() if response.content else {}

    async def post_review(self, pr_url: str, summary: str, comments: list[dict]) -> str:
        match = PR_URL.match(pr_url)
        if not match:
            raise GithubError("give a https://github.com/<owner>/<repo>/pull/<n> URL")
        path = f"/repos/{match['owner']}/{match['repo']}/pulls/{match['number']}/reviews"
        inline = [{"path": c["path"], "line": int(c["line"]), "side": "RIGHT", "body": c["body"]}
                  for c in comments if c.get("path") and c.get("line")]
        try:
            review = await self.call("POST", path, {"event": "COMMENT", "body": summary, "comments": inline})
        except GithubError as exc:  # lines outside the diff are rejected: fall back to one summary comment
            if "422" not in str(exc):
                raise
            listing = "\n".join(f"- `{c.get('path', '')}:{c.get('line', '')}` {c['body']}" for c in comments)
            review = await self.call("POST", path, {"event": "COMMENT", "body": f"{summary}\n\n{listing}"})
        return str(review.get("html_url", pr_url))

    async def _commit_files(self, owner: str, repo: str, base_sha: str, files: dict[str, str], message: str) -> str:
        base = await self.call("GET", f"/repos/{owner}/{repo}/git/commits/{base_sha}")
        tree = await self.call("POST", f"/repos/{owner}/{repo}/git/trees", {
            "base_tree": base["tree"]["sha"],
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()],
        })
        commit = await self.call("POST", f"/repos/{owner}/{repo}/git/commits",
                                 {"message": message, "tree": tree["sha"], "parents": [base_sha]})
        return str(commit["sha"])

    async def open_pr(self, repo_ref: str, branch: str, title: str, body: str, files: dict[str, str]) -> str:
        match = REPO.match(repo_ref.strip())
        if not match:
            raise GithubError("give the repository as owner/name")
        if len(files) > MAX_FILES:
            raise GithubError(f"too many files for one pull request ({len(files)} > {MAX_FILES})")
        owner, repo = match["owner"], match["repo"]
        info = await self.call("GET", f"/repos/{owner}/{repo}")
        default = info["default_branch"]
        head = await self.call("GET", f"/repos/{owner}/{repo}/git/ref/heads/{default}")
        sha = await self._commit_files(owner, repo, head["object"]["sha"], files, title)
        await self.call("POST", f"/repos/{owner}/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        pr = await self.call("POST", f"/repos/{owner}/{repo}/pulls",
                             {"title": title, "body": body, "head": branch, "base": default})
        return str(pr["html_url"])

    async def publish(self, name: str, private: bool, files: dict[str, str], message: str) -> str:
        repo = await self.call("POST", "/user/repos", {"name": name, "private": private, "auto_init": True})
        owner, default = repo["owner"]["login"], repo.get("default_branch", "main")
        head = await self.call("GET", f"/repos/{owner}/{name}/git/ref/heads/{default}")
        sha = await self._commit_files(owner, name, head["object"]["sha"], files, message)
        await self.call("PATCH", f"/repos/{owner}/{name}/git/refs/heads/{default}", {"sha": sha})
        return str(repo["html_url"])
