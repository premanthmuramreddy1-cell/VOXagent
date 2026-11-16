import asyncio
from unittest.mock import AsyncMock

from app import web
from app.skills import Permission, RunContext
from app.skills.shopping import ShoppingSkill, price_in

HITS = [
    {"title": "Keychron K2 – ₹7,499", "url": "https://shop.example/k2", "snippet": "Wireless mechanical keyboard"},
    {"title": "Logitech MX Mechanical", "url": "https://store.example/mx", "snippet": "Now $149.99 with free shipping"},
    {"title": "Keyboard buying guide", "url": "https://blog.example/guide", "snippet": "How to pick a keyboard"},
]


def run(monkeypatch, hits, **args):
    seen = {}

    async def fake_search(query, limit=6):
        seen["query"] = query
        return hits

    monkeypatch.setattr(web, "search", fake_search)
    skill = ShoppingSkill()
    ctx = RunContext("u", "s", "", [], AsyncMock(), AsyncMock(return_value=""))
    return asyncio.run(skill.run(skill.input_schema(**args), ctx)), seen["query"]


def test_shopping_is_read_only_and_never_orders():
    assert ShoppingSkill.permission is Permission.READ


def test_prices_come_only_from_the_results_text():
    assert price_in("Keychron K2 – ₹7,499") == ("₹7,499", 7499.0)
    assert price_in("Now $149.99 with free shipping") == ("$149.99", 149.99)
    assert price_in("How to pick a keyboard") == ("", None)


def test_shopping_lists_real_links_and_never_invents_a_price(monkeypatch):
    result, query = run(monkeypatch, HITS, query="mechanical keyboard", retailer="Amazon")
    assert query == "buy mechanical keyboard Amazon"
    assert [o["url"] for o in result.data["options"]] == [h["url"] for h in HITS]
    assert [o["price"] for o in result.data["options"]] == ["₹7,499", "$149.99", ""]
    assert result.summary.endswith("(https://blog.example/guide)")  # no price for a result that shows none


def test_budget_drops_priced_results_above_it_but_keeps_unpriced(monkeypatch):
    result, _ = run(monkeypatch, HITS, query="mechanical keyboard", max_price=200)
    assert [o["url"] for o in result.data["options"]] == ["https://store.example/mx", "https://blog.example/guide"]


def test_no_results_says_so(monkeypatch):
    result, _ = run(monkeypatch, [], query="unobtainium")
    assert result.data["options"] == [] and "no store links" in result.summary
