"""Find where to buy something when the user has no link yet: real search results, real links.

Prices are only ever quoted when they appear in a search result; nothing is estimated or invented. Ordering
happens in order_from_link once the user picks a link.
"""

import re

from pydantic import BaseModel, Field

from app import web
from app.skills import Permission, RunContext, SkillResult

PRICE = re.compile(r"(?:[₹$€£]|rs\.?\s?|inr\s?|usd\s?)\s?\d[\d,]*(?:\.\d{1,2})?", re.IGNORECASE)


class Args(BaseModel):
    query: str = Field(description="What to buy, e.g. 'ergonomic mechanical keyboard' or 'oat milk cold brew'.")
    max_price: float | None = Field(default=None, gt=0, description="Optional budget; results priced above it are dropped.")
    retailer: str = Field(default="", description="Optional store to prefer, e.g. 'Amazon' or 'Swiggy'.")


def price_in(text: str) -> tuple[str, float | None]:
    """The first price written in the text, as shown and as a number; ('', None) when there is none."""
    match = PRICE.search(text)
    if not match:
        return "", None
    shown = match.group(0).strip()
    return shown, float(re.sub(r"[^\d.]", "", shown.replace(",", "")).strip("."))


class ShoppingSkill:
    name, input_schema, permission = "shopping", Args, Permission.READ
    description = ("Find where to buy something when the user gave no link: searches the web and lists real store "
                   "links, with a price only when the result shows one. Never orders; to order, call "
                   "order_from_link with the link the user picks.")

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        query = args.query.strip()
        if not query:
            raise ValueError("say what you want to buy")
        hits = await web.search(f"buy {query} {args.retailer}".strip(), limit=8)
        options = []
        for hit in hits:
            shown, amount = price_in(f"{hit['title']} {hit['snippet']}")
            if args.max_price is not None and amount is not None and amount > args.max_price:
                continue
            options.append({**hit, "price": shown})
        if not options:
            return SkillResult(f"I found no store links for {query!r}" +
                               (" within your budget." if hits else "."), {"options": []})
        listing = "\n".join(f"- {o['title']} ({o['url']})" + (f", shows {o['price']}" if o["price"] else "")
                            for o in options[:5])
        return SkillResult(f"Places to buy {query!r} (prices only as shown in the results; tell me which link "
                           f"to order from):\n{listing}", {"options": options[:5]})


skill = ShoppingSkill()
