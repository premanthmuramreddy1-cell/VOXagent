"""Fill web forms from the user's saved profile; submitting always needs an explicit yes."""

from pydantic import BaseModel, Field

from app import forms
from app.crypto import delete_secret, load_secret, save_secret
from app.db import SessionLocal, User
from app.skills import Permission, RunContext, SkillResult, is_owner


class FillArgs(BaseModel):
    url: str = Field(description="Public URL of the form page.")


class FillForm:
    name, input_schema, permission = "fill_form", FillArgs, Permission.CHANGE
    description = ("Open any web form, survey or quiz link (Microsoft Forms, Google Forms, sign-up and application "
                   "pages), read every question and answer it: personal fields from the user's saved profile, "
                   "choices and quiz questions from what the user asked and general knowledge. Shows a preview, then "
                   "submits after the user says yes. Never fills passwords or card numbers.")

    async def run(self, args: FillArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            profile = load_secret(db, ctx.user_id, "profile") or {}
            signed_in = load_secret(db, ctx.user_id, "browser_session")
            user = db.get(User, ctx.user_id)
        # the account's own name and email answer "Name" and "Email" questions when the profile has none, so a
        # response is not anonymous just because the profile page was never filled in
        account = {"full_name": user.name, "email": user.email} if user else {}
        profile = {**{k: v for k, v in account.items() if v}, **profile}
        result = await forms.fill(ctx.user_id, args.url, profile, ctx.complete, ctx.user_text, signed_in)
        await ctx.emit("form.preview", url=result["url"], filled=result["filled"], missing=result["missing"],
                       screenshot=result["screenshot"])
        if not result["filled"]:
            await forms.cancel(ctx.user_id)
            found = (f"I found {result['questions']} question(s) but could not answer any"
                     if result["questions"] else "I could not find any questions on that page")
            return SkillResult(f"{found}, so nothing was submitted. Tell me the answers and I will fill them in.",
                               {"filled": [], "missing": result["missing"]})
        answered = "; ".join(f"{f['field'][:60]}: {', '.join(f['answer']) if isinstance(f['answer'], list) else f['answer']}"
                             for f in result["filled"][:8])
        missing = f" I left out: {', '.join(m[:60] for m in result['missing'][:6])}." if result["missing"] else ""
        named = any(f["key"] in ("full_name", "first_name", "email") for f in result["filled"])
        anonymous = (" This form does not record who answers, so your response will show as anonymous. Microsoft "
                     "only shows a name when the form asks for it or requires sign-in: add a Name question and I will "
                     "fill it in.") if result["anonymous"] and not named else ""
        return SkillResult(f"I answered {len(result['filled'])} of {result['questions']} question(s): {answered}."
                           f"{missing}{anonymous}",
                           {"filled": result["filled"], "missing": result["missing"]}, then="submit_form")


class Empty(BaseModel):
    pass


class SubmitForm:
    name, input_schema, permission = "submit_form", Empty, Permission.CRITICAL
    description = ("Submit the form that was filled and previewed ('submit it', 'send the form'). Always needs an "
                   "explicit yes.")

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        result = await forms.submit(ctx.user_id)
        await ctx.emit("form.preview", url=result["url"], filled=[], missing=[], screenshot=result["screenshot"])
        if not result["confirmed"]:
            return SkillResult("I pressed submit, but the page did not confirm it. Check the screenshot.",
                               {"url": result["url"], "confirmed": False})
        said = f" The form says: {result['message']}" if result["message"] else ""
        return SkillResult(f"Submitted.{said}", {"url": result["url"], "confirmed": True})


class CancelForm:
    name, input_schema, permission = "cancel_form", Empty, Permission.READ
    description = "Close the filled form without submitting it."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        closed = await forms.cancel(ctx.user_id)
        return SkillResult("Closed the form without submitting." if closed else "No form was open.", {})


class SignInArgs(BaseModel):
    url: str = Field(default="https://login.microsoftonline.com",
                     description="Where to sign in: Microsoft by default, https://accounts.google.com for Google "
                                 "Forms, or the form link itself.")


class FormSignIn:
    name, input_schema, permission = "form_sign_in", SignInArgs, Permission.CHANGE
    owner_only = True
    description = ("Open a browser window on this computer so the user signs in to Microsoft or Google themselves, "
                   "once. Forms are then answered as the signed-in user, so sign-in-only forms open and forms that "
                   "record names show theirs: 'sign in for forms', 'log in to Microsoft for my forms'. Only when the "
                   "user asks to sign in or log in, never on your own. Never types a password.")

    async def run(self, args: SignInArgs, ctx: RunContext) -> SkillResult:
        if not is_owner(ctx.user_id):  # the window opens on the owner's screen, where nobody else can type
            return SkillResult("Signing in opens a window on the host computer, so only its owner can do it. Your "
                               "forms are still filled with your own account name and email.", {"saved": False})
        await ctx.emit("form.sign_in", url=args.url)
        state = await forms.sign_in(args.url)
        if not state.get("cookies"):
            return SkillResult("No sign-in was saved: the window closed before you logged in.", {"saved": False})
        with SessionLocal() as db:
            save_secret(db, ctx.user_id, "browser_session", state)
        return SkillResult("Saved your sign-in. The forms I fill from now on are answered as you.", {"saved": True})


class FormSignOut:
    name, input_schema, permission = "form_sign_out", Empty, Permission.READ
    description = "Forget the sign-in saved for filling forms ('sign out of forms')."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            removed = delete_secret(db, ctx.user_id, "browser_session")
        return SkillResult("Forgot your forms sign-in." if removed else "No forms sign-in was saved.", {})


skills = [FillForm(), SubmitForm(), CancelForm(), FormSignIn(), FormSignOut()]
