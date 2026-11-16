"""Fill web forms from the user's saved profile; submitting always needs an explicit yes."""

from pydantic import BaseModel, Field

from app import forms
from app.crypto import load_secret
from app.db import SessionLocal
from app.skills import Permission, RunContext, SkillResult


class FillArgs(BaseModel):
    url: str = Field(description="Public URL of the form page.")


class FillForm:
    name, input_schema, permission = "fill_form", FillArgs, Permission.CHANGE
    description = ("Open a web form and fill it from the user's saved profile (name, address, education...). "
                   "Shows a preview and stops before submitting. Never fills passwords or card numbers.")

    async def run(self, args: FillArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            profile = load_secret(db, ctx.user_id, "profile") or {}
        if not profile:
            raise forms.FormError("your form profile is empty; add your details on the Notes page first")
        result = await forms.fill(ctx.user_id, args.url, profile, ctx.complete)
        await ctx.emit("form.preview", url=result["url"], filled=result["filled"], missing=result["missing"],
                       screenshot=result["screenshot"])
        missing = f" I could not fill: {', '.join(result['missing'][:6])}." if result["missing"] else ""
        return SkillResult(f"I filled {len(result['filled'])} field(s).{missing}",
                           {"filled": result["filled"], "missing": result["missing"]}, then="submit_form")


class Empty(BaseModel):
    pass


class SubmitForm:
    name, input_schema, permission = "submit_form", Empty, Permission.CRITICAL
    description = "Submit the form that was filled and previewed. Always needs an explicit yes."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        result = await forms.submit(ctx.user_id)
        await ctx.emit("form.preview", url=result["url"], filled=[], missing=[], screenshot=result["screenshot"])
        return SkillResult(f"Submitted. The page is now {result['url']}.", {"url": result["url"]})


class CancelForm:
    name, input_schema, permission = "cancel_form", Empty, Permission.READ
    description = "Close the filled form without submitting it."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        closed = await forms.cancel(ctx.user_id)
        return SkillResult("Closed the form without submitting." if closed else "No form was open.", {})


skills = [FillForm(), SubmitForm(), CancelForm()]
