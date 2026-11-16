"""Order items, food, drinks, tools or subscriptions from a link the user shares.

order_from_link walks the page to checkout and stops at the final button; confirm_order clicks it only after
an explicit yes. Nothing is ever paid with typed card details: those pages are handed back to the user.
"""

from pydantic import BaseModel, Field

from app import orders
from app.crypto import load_secret
from app.db import SessionLocal
from app.skills import Permission, RunContext, SkillResult

STOPPED = {
    "needs_payment": "It needs card, password or OTP details, which I never type. Open the link to finish there",
    "blocked": "I could not go further",
    "stuck": "I could not reach the checkout",
}


class OrderArgs(BaseModel):
    url: str = Field(description="The link the user shared: a product, store, menu, cart or subscription page.")
    request: str = Field(description="Exactly what to order, with quantity, size, options and delivery notes, "
                                     "e.g. '2 iced lattes, oat milk, large'.")


class OrderFromLink:
    name, input_schema, permission = "order_from_link", OrderArgs, Permission.CHANGE
    description = ("Order something from a link the user shared (shop item, food or drinks menu, tool, "
                   "subscription): picks the item and options, adds it to the cart, fills checkout details from "
                   "the saved profile, and stops before the final order or pay button with the total. "
                   "Needs a link; if the user gave none, search the web first and ask which link to use.")

    async def run(self, args: OrderArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            profile = load_secret(db, ctx.user_id, "profile") or {}
        result = await orders.prepare(ctx.user_id, args.url, args.request, profile, ctx.complete, ctx.emit)
        await ctx.emit("order.preview", **{k: result[k] for k in ("status", "note", "url", "steps", "total",
                                                                  "final_button", "screenshot")})
        total = f" The total shown is {result['total']}." if result["total"] else ""
        if result["status"] == "ready":
            text = (f"Your order is ready at the last step, '{result['final_button']}'.{total} "
                    "Check the preview, then say yes, place the order.")
        else:
            reason = f" ({result['note']})" if result["note"] else ""
            text = f"{STOPPED[result['status']]}{reason}.{total} Nothing was ordered."
        return SkillResult(text, {k: v for k, v in result.items() if k != "screenshot"})


class Empty(BaseModel):
    pass


class ConfirmOrder:
    name, input_schema, permission = "confirm_order", Empty, Permission.CRITICAL
    description = "Place the order that order_from_link prepared, by clicking its final button. Needs an explicit yes."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        result = await orders.confirm(ctx.user_id)
        await ctx.emit("order.preview", status="placed" if result["confirmed"] else "submitted", note="",
                       url=result["url"], steps=[], total=result["total"], final_button="",
                       screenshot=result["screenshot"])
        if result["needs_payment"]:
            text = "The store now asks for payment details, which I never type. Open the link to pay there."
        elif result["confirmed"]:
            text = "The order is placed; the store shows a confirmation."
        else:
            text = "I clicked the final button, but the page shows no confirmation. Check the preview or your email."
        return SkillResult(text, {k: v for k, v in result.items() if k != "screenshot"})


class CancelOrder:
    name, input_schema, permission = "cancel_order", Empty, Permission.READ
    description = "Drop the order that is waiting for confirmation, without placing it."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        dropped = await orders.cancel(ctx.user_id)
        return SkillResult("Dropped the order; nothing was placed." if dropped else "No order was waiting.", {})


skills = [OrderFromLink(), ConfirmOrder(), CancelOrder()]
