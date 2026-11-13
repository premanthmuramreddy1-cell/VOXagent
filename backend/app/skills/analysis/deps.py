"""Dependency vulnerability audit via OSV.dev. Only package names and versions leave the machine."""

import asyncio
import json
import re
import tomllib
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

import httpx

from app.skills.analysis.base import make_issue

OSV = "https://api.osv.dev/v1"
MAX_PACKAGES = 200
MAX_VULNS = 25
NOT_RUN = "dependency vulnerability audit (OSV.dev unreachable)"
SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}
REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")
EXACT = re.compile(r"^[\^~]?(\d+\.\d+\.\d+)$")
# (name, version, line) per pinned dependency; unpinned lines are reported separately
Dep = tuple[str, str, int]


def _find_line(content: str, pattern: str) -> int:
    rx = re.compile(pattern, re.IGNORECASE)
    return next((i for i, ln in enumerate(content.splitlines(), 1) if rx.search(ln)), 1)


def _parse_req_line(raw: str) -> tuple[str, str] | None:
    """Return (name, pinned_version or "") for a requirement spec, None when it is not a package line."""
    line = raw.split(" #")[0].split(";")[0].strip()
    if not line or line.startswith(("-", "#")) or "://" in line:
        return None
    m = REQ.match(line)
    if not m:
        return None
    pin = re.fullmatch(r"==\s*([^\s,=<>!~]+)", m.group(2).strip())
    return m.group(1), pin.group(1) if pin else ""


def _requirements(content: str) -> tuple[list[Dep], int]:
    deps: list[Dep] = []
    first_unpinned = 0
    for i, raw in enumerate(content.splitlines(), 1):
        parsed = _parse_req_line(raw)
        if not parsed:
            continue
        if parsed[1]:
            deps.append((parsed[0], parsed[1], i))
        elif not first_unpinned:
            first_unpinned = i
    return deps, first_unpinned


def _package_json(content: str) -> list[Dep]:
    data = json.loads(content)
    out: list[Dep] = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            m = EXACT.match(str(spec))
            if m:
                out.append((name, m.group(1), _find_line(content, rf'"{re.escape(name)}"\s*:')))
    return out


def _go_mod(content: str) -> list[Dep]:
    out: list[Dep] = []
    in_block = False
    for i, raw in enumerate(content.splitlines(), 1):
        line = raw.split("//")[0].strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):].strip()
        elif not in_block:
            continue
        m = re.match(r"^(\S+)\s+v(\d\S*)$", line)
        if m:
            out.append((m.group(1), m.group(2), i))
    return out


def _cargo(content: str) -> list[Dep]:
    out: list[Dep] = []
    for name, spec in (tomllib.loads(content).get("dependencies") or {}).items():
        m = EXACT.match(spec) if isinstance(spec, str) else None
        if m:
            out.append((name, m.group(1), _find_line(content, rf"^\s*{re.escape(name)}\s*=")))
    return out


def _pyproject(content: str) -> list[Dep]:
    out: list[Dep] = []
    for spec in tomllib.loads(content).get("project", {}).get("dependencies", []):
        parsed = _parse_req_line(spec)
        if parsed and parsed[1]:
            out.append((parsed[0], parsed[1], _find_line(content, rf"(?<![\w.-]){re.escape(parsed[0])}(?![\w.-])")))
    return out


def _parser(base: str) -> tuple[str, Callable[[str], list[Dep]]] | None:
    if base == "package.json":
        return "npm", _package_json
    if base == "go.mod":
        return "Go", _go_mod
    if base == "Cargo.toml":
        return "crates.io", _cargo
    if base == "pyproject.toml":
        return "PyPI", _pyproject
    if base.startswith("requirements") and base.endswith(".txt"):
        return "PyPI", lambda c: _requirements(c)[0]
    return None


def _severity(vuln: dict[str, Any]) -> str:
    return SEVERITY.get(str((vuln.get("database_specific") or {}).get("severity", "")).upper(), "medium")


def _fixed(vuln: dict[str, Any], name: str) -> str:
    fixes = [(a.get("package") or {}).get("name", "").lower() == name.lower() and ev["fixed"]
             for a in vuln.get("affected", []) for r in a.get("ranges", []) for ev in r.get("events", [])
             if "fixed" in ev]
    pkg_fixes = [f for f in fixes if f]
    if pkg_fixes:
        return pkg_fixes[0]
    return next((ev["fixed"] for a in vuln.get("affected", []) for r in a.get("ranges", [])
                 for ev in r.get("events", []) if "fixed" in ev), "")


async def _osv(client: httpx.AsyncClient, pkgs: list[tuple[str, str, str]]
               ) -> tuple[dict[tuple[str, str, str], list[str]], dict[str, dict[str, Any]]]:
    """Return ({package: vuln ids}, {vuln id: details}) for (ecosystem, name, version) packages; raises on failure."""
    queries = [{"package": {"name": n, "ecosystem": e}, "version": v} for e, n, v in pkgs]
    r = await client.post(f"{OSV}/querybatch", json={"queries": queries}, timeout=10)
    r.raise_for_status()
    results = r.json()["results"]
    hits = {p: [v["id"] for v in res.get("vulns", [])] for p, res in zip(pkgs, results, strict=True)}
    ids = list(dict.fromkeys(i for vs in hits.values() for i in vs))[:MAX_VULNS]

    async def one(vid: str) -> dict[str, Any]:
        resp = await client.get(f"{OSV}/vulns/{vid}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    return hits, dict(zip(ids, await asyncio.gather(*(one(i) for i in ids)), strict=True))


async def audit(files: dict[str, str], client: httpx.AsyncClient | None = None) -> tuple[list[dict], list[str]]:
    issues: list[dict] = []
    found: list[tuple[str, str, str, str, int]] = []  # eco, name, version, file, line
    for path, content in files.items():
        base = PurePosixPath(path).name
        parser = _parser(base)
        if not parser:
            continue
        eco, parse = parser
        try:
            found += [(eco, n, v, path, ln) for n, v, ln in parse(content)]
        except (ValueError, tomllib.TOMLDecodeError, AttributeError):
            continue  # malformed manifest: nothing to audit
        if base.startswith("requirements") and (line := _requirements(content)[1]):
            issues.append(make_issue(
                "low", "security", path, line, "Dependency is not pinned to an exact version, so builds can pull "
                "in a release with a newly published vulnerability.",
                "pin exact versions or use a lockfile", "osv", cwe="CWE-1395"))
    found = found[:MAX_PACKAGES]
    if not found:
        return issues, []

    unique = list(dict.fromkeys((e, n, v) for e, n, v, _, _ in found))
    own = client is None
    http = client or httpx.AsyncClient(timeout=10)
    try:
        hits, vulns = await _osv(http, unique)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return issues, [NOT_RUN]
    finally:
        if own:
            await http.aclose()
    for eco, name, ver, path, line in found:
        for vid in hits.get((eco, name, ver), []):
            if vid not in vulns:
                continue
            v = vulns[vid]
            label = next((a for a in v.get("aliases", []) if a.startswith("CVE-")), vid)
            fixed = _fixed(v, name)
            issues.append(make_issue(
                _severity(v), "security", path, line,
                f"{name} {ver} is affected by {label}: {v.get('summary') or v.get('details', '')[:200] or vid}",
                f"upgrade {name} to >= {fixed}" if fixed else "no fixed version published yet", "osv", cwe="CWE-1395"))
    return issues, []
