"""Live internet access: search, fetch a page as text, and look up the latest version of a package.

Fetching is guarded against SSRF: every hop (including redirects) is resolved first, private, loopback,
link-local and reserved addresses are refused, and the connection is pinned to the checked IP so DNS
cannot be re-pointed between the check and the request. Page text is data for the agent, never instructions.
"""

import asyncio
import ipaddress
import re
import socket
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urljoin, urlparse

import httpx

from app.config import settings

UA = "VoxAgent/1.0 (+https://github.com/voxagent)"
MAX_REDIRECTS = 5


class WebError(ValueError):
    """The URL was refused or the page could not be read."""


def _blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast


async def _resolve(host: str) -> str:
    """Return one public IP for host, or raise if any of its addresses is internal."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise WebError(f"could not resolve {host}") from None
    ips = [str(info[4][0]) for info in infos]
    if not ips or any(_blocked(ip) for ip in ips):
        raise WebError(f"{host} points to a private or internal address, which is not allowed")
    return ips[0]


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "pre", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _Text()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return parser.title.strip(), text


async def fetch(url: str, max_chars: int = 20_000) -> dict:
    """GET a public URL and return {url, title, text}. Follows up to 5 redirects, each re-checked."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise WebError("only http and https URLs can be opened")
        if parts.username or parts.password:
            raise WebError("URLs with credentials are not allowed")
        ip = await _resolve(parts.hostname)
        host_ip = f"[{ip}]" if ":" in ip else ip
        pinned = parts._replace(netloc=f"{host_ip}:{parts.port}" if parts.port else host_ip).geturl()
        headers = {"Host": parts.netloc.split("@")[-1], "User-Agent": UA, "Accept": "text/html,text/plain,*/*"}
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as http:
            try:
                async with http.stream("GET", pinned, headers=headers,
                                       extensions={"sni_hostname": parts.hostname}) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise WebError(f"{url} answered {response.status_code}")
                    blob = b""
                    async for chunk in response.aiter_bytes():
                        blob += chunk
                        if len(blob) > settings().web_fetch_bytes:
                            break
                    kind = response.headers.get("content-type", "")
            except httpx.HTTPError as exc:
                raise WebError(f"could not open {url}: {exc}") from None
        body = blob.decode(errors="replace")
        title, text = html_to_text(body) if "html" in kind or body.lstrip().startswith("<") else ("", body)
        return {"url": url, "title": title, "text": text[:max_chars]}
    raise WebError("too many redirects")


def _ddg_link(href: str) -> str:
    if "uddg=" in href:
        return parse_qs(urlparse(href).query).get("uddg", [href])[0]
    return href if href.startswith("http") else "https:" + href


async def search(query: str, limit: int = 6) -> list[dict]:
    """Search the web. Returns [{title, url, snippet}]."""
    engine = settings().web_search
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}, follow_redirects=True) as http:
        if engine.startswith("searxng:"):
            response = await http.get(f"{engine[8:].rstrip('/')}/search", params={"q": query, "format": "json"})
            response.raise_for_status()
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                    for r in response.json().get("results", [])[:limit]]
        response = await http.post("https://html.duckduckgo.com/html/", data={"q": query})
        response.raise_for_status()
    html = response.text
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    strip = lambda s: unescape(re.sub(r"<[^>]+>", "", s)).strip()
    return [{"title": strip(title), "url": _ddg_link(unescape(href)), "snippet": strip(snippets[i]) if i < len(snippets) else ""}
            for i, (href, title) in enumerate(links[:limit])]


async def latest_version(manager: str, name: str) -> str:
    """Latest published version of a package, from the registry itself."""
    urls = {
        "pip": f"https://pypi.org/pypi/{quote(name)}/json",
        "npm": f"https://registry.npmjs.org/{quote(name, safe='@')}/latest",
        "cargo": f"https://crates.io/api/v1/crates/{quote(name)}",
        "go": f"https://proxy.golang.org/{quote(name.lower())}/@latest",
    }
    if manager not in urls or not re.fullmatch(r"[@\w./-]+", name):
        raise WebError(f"cannot look up {manager} package {name!r}")
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as http:
        response = await http.get(urls[manager])
    if response.status_code == 404:
        raise WebError(f"{name} is not a published {manager} package")
    response.raise_for_status()
    data = response.json()
    return str({"pip": lambda: data["info"]["version"], "npm": lambda: data["version"],
                "cargo": lambda: data["crate"]["max_stable_version"], "go": lambda: data["Version"]}[manager]())
