import asyncio
import json

import httpx

from app.skills.analysis.deps import audit

VULN = {
    "id": "GHSA-xxxx", "aliases": ["CVE-2023-0001"], "summary": "Request smuggling",
    "database_specific": {"severity": "HIGH"},
    "affected": [{"package": {"name": "flask"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.3.2"}]}]}],
}


def run(files, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await audit(files, c)
    return asyncio.run(go())


def osv(vulnerable=("flask",)):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/querybatch"):
            qs = json.loads(req.content)["queries"]
            return httpx.Response(200, json={"results": [
                {"vulns": [{"id": "GHSA-xxxx"}]} if q["package"]["name"] in vulnerable else {} for q in qs]})
        return httpx.Response(200, json=VULN)
    return handler


def test_vulnerable_pinned_package_flagged_at_line_with_fix():
    issues, notrun = run({"app/requirements.txt": "# deps\nrequests==2.31.0\nflask==2.0.0\n"}, osv())
    assert notrun == [] and len(issues) == 1
    i = issues[0]
    assert (i["file"], i["line"], i["severity"], i["source"], i["cwe"]) == ("app/requirements.txt", 3, "high", "osv", "CWE-1395")
    assert "CVE-2023-0001" in i["explanation"] and "flask 2.0.0" in i["explanation"]
    assert i["fix"] == "upgrade flask to >= 2.3.2"


def test_clean_package_no_issue():
    assert run({"requirements.txt": "requests==2.31.0\n"}, osv(())) == ([], [])


def test_osv_500_and_timeout_fail_soft_keep_offline_issue():
    files = {"requirements.txt": "-r base.txt\nrequests\nflask==2.0.0\n"}

    def boom(req):
        return httpx.Response(500)

    def slow(req):
        raise httpx.ConnectTimeout("t")

    for h in (boom, slow):
        issues, notrun = run(files, h)
        assert notrun == ["dependency vulnerability audit (OSV.dev unreachable)"]
        assert [(i["severity"], i["line"], i["fix"]) for i in issues] == [("low", 2, "pin exact versions or use a lockfile")]


def test_bad_json_fails_soft():
    issues, notrun = run({"requirements.txt": "flask==2.0.0\n"}, lambda r: httpx.Response(200, content=b"nope"))
    assert issues == [] and len(notrun) == 1


def seen(files):
    """Packages sent to OSV for the given manifests."""
    sent = []

    def handler(req):
        sent.extend((q["package"]["ecosystem"], q["package"]["name"], q["version"])
                    for q in json.loads(req.content)["queries"])
        return httpx.Response(200, json={"results": [{} for _ in sent]})

    run(files, handler)
    return sent


def test_manifest_parsers():
    files = {
        "requirements-dev.txt": "pytest==8.0.0  # c\n-e .\ngit+https://x/y.git\nfoo[extra]==1.0; python_version<'3'\nbar>=1\n",
        "web/package.json": '{"dependencies": {"a": "1.2.3", "b": "^2.0.0", "c": "*"}, "devDependencies": {"d": "~3.1.4"}}',
        "go.mod": "module m\n\ngo 1.22\n\nrequire (\n\tgithub.com/x/y v1.2.3 // indirect\n)\nrequire github.com/p/q v0.1.0\n",
        "Cargo.toml": '[dependencies]\nserde = "1.0.1"\ntokio = { version = "1" }\n',
        "pyproject.toml": '[project]\nname = "x"\ndependencies = ["httpx==0.27.0", "rich>=13"]\n',
    }
    assert sorted(seen(files)) == sorted([
        ("PyPI", "pytest", "8.0.0"), ("PyPI", "foo", "1.0"),
        ("npm", "a", "1.2.3"), ("npm", "b", "2.0.0"), ("npm", "d", "3.1.4"),
        ("Go", "github.com/x/y", "1.2.3"), ("Go", "github.com/p/q", "0.1.0"),
        ("crates.io", "serde", "1.0.1"), ("PyPI", "httpx", "0.27.0")])


def test_package_json_line_and_no_manifest():
    def h(req):
        if req.url.path.endswith("/querybatch"):
            return httpx.Response(200, json={"results": [{"vulns": [{"id": "GHSA-xxxx"}]}]})
        return httpx.Response(200, json={**VULN, "affected": [], "database_specific": {}})

    issues, _ = run({"package.json": '{\n "dependencies": {\n  "lodash": "4.17.0"\n }\n}'}, h)
    assert issues[0]["line"] == 3 and issues[0]["severity"] == "medium"
    assert issues[0]["fix"] == "no fixed version published yet"
    assert run({"main.py": "x=1"}, osv()) == ([], [])
