import json
from unittest.mock import AsyncMock

import pytest

from app.skills import Permission, RunContext
from app.skills.shopping import Args, generate_retailer_cart_url, skill


@pytest.fixture
def mock_ctx():
    ctx = RunContext(
        user_id="u1",
        session_id="s1",
        user_text="buy me an ergonomic mouse",
        upload_ids=[],
        emit=AsyncMock(),
        complete=AsyncMock(),
    )
    ctx.complete.return_value = json.dumps({
        "title": "Logitech MX Master 3S",
        "price": 99.99,
        "currency": "USD",
        "retailer": "Amazon",
        "specs": "8K DPI sensor, Quiet clicks",
        "rating": 4.8,
    })
    return ctx


@pytest.mark.anyio
async def test_shopping_prepares_cart_and_stops_before_checkout(mock_ctx):
    res = await skill.run(Args(query="ergonomic mouse", quantity=2), mock_ctx)

    assert "stopped right at the checkout screen" in res.summary
    assert res.data["status"] == "STOPPED_BEFORE_CHECKOUT"
    assert res.data["item"] == "Logitech MX Master 3S"
    assert res.data["quantity"] == 2
    assert res.data["subtotal"] == 199.98
    assert res.data["requires_human_payment"] is True
    assert "amazon.com" in res.data["checkout_url"]

    # Verify event emitted to UI
    mock_ctx.emit.assert_called_once_with(
        "shopping.checkout_ready",
        status="STOPPED_BEFORE_CHECKOUT",
        item="Logitech MX Master 3S",
        specs="8K DPI sensor, Quiet clicks",
        unit_price=99.99,
        quantity=2,
        subtotal=199.98,
        est_tax=16.00,
        est_shipping=0.00,
        total_estimated_cost=215.98,
        retailer="Amazon",
        checkout_url=res.data["checkout_url"],
        requires_human_payment=True,
    )


def test_shopping_permission_is_critical():
    assert skill.permission == Permission.CRITICAL


def test_generate_retailer_cart_urls():
    amazon_url = generate_retailer_cart_url("Amazon", "MacBook Air", "macbook")
    assert "amazon.com/s?k=MacBook+Air" in amazon_url

    bestbuy_url = generate_retailer_cart_url("BestBuy", "iPad", "ipad")
    assert "bestbuy.com/site/searchpage.jsp?st=iPad" in bestbuy_url

    google_url = generate_retailer_cart_url("other", "Shoes", "shoes")
    assert "google.com/search?tbm=shop&q=Shoes" in google_url


@pytest.mark.anyio
async def test_shopping_fallback_when_llm_fails():
    ctx = RunContext(
        user_id="u1",
        session_id="s1",
        user_text="buy headphones",
        upload_ids=[],
        emit=AsyncMock(),
        complete=AsyncMock(side_effect=ValueError("bad json")),
    )
    res = await skill.run(Args(query="headphones", max_price=50.0), ctx)
    assert res.data["status"] == "STOPPED_BEFORE_CHECKOUT"
    assert res.data["unit_price"] == 50.0
    assert "stopped right at the checkout screen" in res.summary
