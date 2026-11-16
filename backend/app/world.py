"""World briefing, carried over from J.A.R.V.I.S.: headlines in sections (Google News RSS) and live market quotes
(Yahoo chart API), fetched together. Made local to the user: the news edition and which markets come first follow
their time zone. Both sources are fixed public hosts, so no user URL ever reaches them."""

import asyncio
import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

UA = "Mozilla/5.0 (VoxAgent briefing)"
CACHE_S = 120  # markets move: keep it short
_cache: dict[str, tuple[float, object]] = {}

MARKETS = {  # symbol: (name, region)
    "^NSEI": ("NIFTY 50", "IN"), "^BSESN": ("SENSEX", "IN"), "USDINR=X": ("USD/INR", "IN"),
    "^GSPC": ("S&P 500", "US"), "^IXIC": ("NASDAQ", "US"), "^DJI": ("Dow Jones", "US"),
    "^FTSE": ("FTSE 100", "GB"), "^N225": ("Nikkei 225", "JP"),
    "BTC-USD": ("Bitcoin", ""), "GC=F": ("Gold", ""), "CL=F": ("Crude oil", ""),
}
SECTIONS = {"top": "", "world": "world", "business": "business economy", "tech": "technology",
            "science": "science", "sports": "sports"}
EDITIONS = {"IN": ("en-IN", "IN", "IN:en"), "GB": ("en-GB", "GB", "GB:en"), "US": ("en-US", "US", "US:en")}


def region(tz: str) -> str:
    """Where the user is, from their IANA zone: IN, GB, US, JP, or '' (world edition, US markets first)."""
    if tz in ("Asia/Kolkata", "Asia/Calcutta"):
        return "IN"
    if tz == "Europe/London":
        return "GB"
    if tz == "Asia/Tokyo":
        return "JP"
    return "US" if tz.startswith("America/") else ""


async def _get(url: str) -> str:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return str(hit[1])
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": UA}, follow_redirects=True) as http:
        response = await http.get(url)
        response.raise_for_status()
    if len(_cache) >= 64:  # topics come from users: keep the cache small
        _cache.clear()
    _cache[url] = (time.monotonic(), response.text)
    return response.text


def parse_rss(xml: str, limit: int = 6) -> list[dict]:
    items = []
    for item in ET.fromstring(xml).iter("item"):
        source = (item.findtext("source") or "").strip()
        title = (item.findtext("title") or "").strip().removesuffix(f" - {source}" if source else "\0")
        if title:
            items.append({"title": title, "source": source, "url": (item.findtext("link") or "").strip()})
        if len(items) == limit:
            break
    return items


async def headlines(topic: str, where: str) -> list[dict]:
    hl, gl, ceid = EDITIONS.get(where, EDITIONS["US"])
    edition = f"hl={hl}&gl={gl}&ceid={ceid}"
    url = (f"https://news.google.com/rss/search?q={quote(topic)}&{edition}" if topic
           else f"https://news.google.com/rss?{edition}")
    return parse_rss(await _get(url))


def parse_quote(symbol: str, payload: dict) -> dict:
    meta = payload["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
    name, where = MARKETS.get(symbol, (symbol, ""))
    return {"symbol": symbol, "name": name, "region": where, "price": price, "currency": meta.get("currency", ""),
            "change_pct": round((price - prev) / prev * 100, 2) if prev else 0.0}


async def quote_of(symbol: str) -> dict:
    raw = await _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range=1d&interval=1d")
    return parse_quote(symbol, json.loads(raw))


def market_order(where: str) -> list[str]:
    """The user's own markets first, then the US, then the rest."""
    rank = {where: 0, "US": 1}
    return sorted(MARKETS, key=lambda s: rank.get(MARKETS[s][1], 2))


async def _settle(jobs: dict) -> dict:
    """Run every fetch at once; a source that fails is left out instead of failing the briefing."""
    done = await asyncio.gather(*jobs.values(), return_exceptions=True)
    return {key: value for key, value in zip(jobs, done, strict=True) if not isinstance(value, BaseException)}


async def briefing(tz: str, focus: str = "all", topic: str = "") -> dict:
    where, topic = region(tz), topic[:80]
    sections = {"topic": topic} if topic else {**SECTIONS, **({"india": "india"} if where == "IN" else {})}
    news = await _settle({k: headlines(q, where) for k, q in sections.items()}) if focus != "markets" else {}
    quotes = await _settle({s: quote_of(s) for s in market_order(where)}) if focus != "news" and not topic else {}
    return {"region": where, "news": news, "markets": [quotes[s] for s in market_order(where) if s in quotes]}


def spoken(data: dict, headline_count: int = 3, market_count: int = 4) -> str:
    """A short read-out: a few headlines and the user's key markets. The full set goes to the screen."""
    parts = []
    lead: list[dict] = next((items for key in ("topic", "top", "world") if (items := data["news"].get(key))), [])
    if lead:
        parts.append("Top stories: " + ". ".join(i["title"].rstrip(".") for i in lead[:headline_count]) + ".")
    moves = []
    for m in data["markets"][:market_count]:
        direction = "up" if m["change_pct"] > 0 else "down" if m["change_pct"] < 0 else "flat"
        pct = f" {abs(m['change_pct']):.1f}%" if direction != "flat" else ""
        moves.append(f"{m['name']} {m['price']:,.2f}, {direction}{pct}")
    if moves:
        parts.append("Markets: " + "; ".join(moves) + ".")
    return " ".join(parts) or "I couldn't reach the news or market sources right now."
