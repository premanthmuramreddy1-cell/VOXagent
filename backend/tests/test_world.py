import asyncio
import json

import httpx

from app import world

RSS = """<rss><channel>
<item><title>Monsoon reaches Kerala early - The Hindu</title><source>The Hindu</source><link>https://n/1</link></item>
<item><title>Chip exports hit a record - Reuters</title><source>Reuters</source><link>https://n/2</link></item>
<item><title>Final goes to extra time</title><link>https://n/3</link></item>
</channel></rss>"""


def chart(price, prev):
    return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": price, "chartPreviousClose": prev,
                                                       "currency": "INR"}}]}})


def test_rss_titles_lose_the_source_suffix():
    items = world.parse_rss(RSS)
    assert [i["title"] for i in items] == ["Monsoon reaches Kerala early", "Chip exports hit a record",
                                          "Final goes to extra time"]
    assert items[0]["source"] == "The Hindu"


def test_region_and_market_order_follow_the_user():
    assert world.region("Asia/Kolkata") == "IN" and world.region("America/Chicago") == "US"
    assert world.region("UTC") == ""
    assert world.market_order("IN")[:3] == ["^NSEI", "^BSESN", "USDINR=X"]
    assert world.market_order("GB")[0] == "^FTSE" and world.market_order("GB")[1] == "^GSPC"


def test_briefing_is_local_skips_a_failed_source_and_reads_out_short(monkeypatch):
    urls = []

    async def fake_get(url):
        urls.append(url)
        if "finance" in url:
            if "BSESN" in url:
                raise httpx.ConnectError("down")
            return chart(24310.5, 24213.4)
        return RSS

    monkeypatch.setattr(world, "_get", fake_get)
    data = asyncio.run(world.briefing("Asia/Kolkata"))
    assert "india" in data["news"] and all("gl=IN" in u for u in urls if "news.google" in u)
    assert "^BSESN" not in [m["symbol"] for m in data["markets"]]  # failed source left out, rest still there
    assert data["markets"][0]["name"] == "NIFTY 50" and data["markets"][0]["change_pct"] == 0.4
    text = world.spoken(data)
    assert text.startswith("Top stories: Monsoon reaches Kerala early. Chip exports hit a record.")
    assert "NIFTY 50 24,310.50, up 0.4%" in text


def test_topic_asks_only_for_that_news_and_nothing_reachable_says_so(monkeypatch):
    urls = []

    async def fake_get(url):
        urls.append(url)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(world, "_get", fake_get)
    data = asyncio.run(world.briefing("UTC", topic="solar power"))
    assert urls == ["https://news.google.com/rss/search?q=solar%20power&hl=en-US&gl=US&ceid=US:en"]
    assert world.spoken(data) == "I couldn't reach the news or market sources right now."
