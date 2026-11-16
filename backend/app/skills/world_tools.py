"""World briefing (see app/world.py): headlines and markets, local to the user, spoken exactly as fetched."""

from typing import Literal

from pydantic import BaseModel, Field

from app import personal, world
from app.db import SessionLocal
from app.skills import Permission, RunContext, SkillResult


class Args(BaseModel):
    focus: Literal["all", "news", "markets"] = Field("all", description="'all' for general questions like 'what's "
                                                                        "going on in the world'; 'markets' only for "
                                                                        "market questions; 'news' only when the user "
                                                                        "asked for headlines alone.")
    topic: str = Field("", description="A news topic when the user asked about one ('AI', 'cricket'); else empty.")


class WorldBriefing:
    name, input_schema, permission = "world_briefing", Args, Permission.READ
    description = ("Current news and markets: today's headlines by section, news on any topic ('any news about "
                   "cricket'), or live prices of indices, currency, gold, oil and bitcoin ('how is the stock market "
                   "doing'). Use it for every question about recent events, news, scores or prices, even a "
                   "follow-up: news and prices from memory are out of date.")

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            tz = personal.zone(db, ctx.user_id).key
        data = await world.briefing(tz, args.focus, args.topic.strip())
        await ctx.emit("world.briefing", **data)
        return SkillResult(world.spoken(data), data, final=True)


skills = [WorldBriefing()]
