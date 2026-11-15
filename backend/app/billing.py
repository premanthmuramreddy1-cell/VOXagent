"""Payments: Stripe Checkout for the paid tier, and a signed webhook that flips `user.tier`.

Off until VOX_STRIPE_SECRET_KEY, VOX_STRIPE_PRICE_ID and VOX_STRIPE_WEBHOOK_SECRET are set.
"""

import hashlib
import hmac
import time

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db import User

TRANSPORT: httpx.AsyncBaseTransport | None = None
TOLERANCE_S = 300


class BillingError(RuntimeError):
    pass


def configured() -> bool:
    cfg = settings()
    return bool(cfg.stripe_secret_key and cfg.stripe_price_id and cfg.stripe_webhook_secret)


async def checkout_url(user: User) -> str:
    if not configured():
        raise BillingError("payments are not configured on this server")
    form = {
        "mode": "subscription", "line_items[0][price]": settings().stripe_price_id, "line_items[0][quantity]": "1",
        "success_url": f"{settings().public_url}/#/account?upgraded=1",
        "cancel_url": f"{settings().public_url}/#/account", "client_reference_id": user.id,
        "customer_email": user.email, "subscription_data[metadata][user_id]": user.id,
    }
    async with httpx.AsyncClient(timeout=20, transport=TRANSPORT) as http:
        response = await http.post("https://api.stripe.com/v1/checkout/sessions", data=form,
                                   auth=(settings().stripe_secret_key, ""))
    if response.status_code != 200:
        raise BillingError(f"Stripe refused the checkout: {response.text[:200]}")
    return str(response.json()["url"])


def verify(payload: bytes, header: str, secret: str, now: float | None = None) -> None:
    """Stripe-Signature: t=<ts>,v1=<hex hmac of "<ts>.<payload>">; rejects stale or forged events."""
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        stamp = int(parts["t"])
    except (KeyError, ValueError):
        raise BillingError("missing signature") from None
    if abs((now or time.time()) - stamp) > TOLERANCE_S:
        raise BillingError("signature is too old")
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    candidates = [v for k, v in (item.split("=", 1) for item in header.split(",") if "=" in item) if k == "v1"]
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise BillingError("bad signature")


def apply_event(db: Session, event: dict) -> str:
    kind, obj = event.get("type", ""), event.get("data", {}).get("object", {})
    if kind == "checkout.session.completed":
        user_id, tier = obj.get("client_reference_id"), "paid"
    elif kind == "customer.subscription.deleted":
        user_id, tier = obj.get("metadata", {}).get("user_id"), "free"
    else:
        return "ignored"
    user = db.get(User, str(user_id)) if user_id else None
    if not user:
        return "unknown user"
    user.tier = tier
    db.commit()
    return f"{user.id} -> {tier}"
