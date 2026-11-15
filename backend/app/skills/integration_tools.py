"""Email, calendar, GitHub and deploy. Sending, posting, publishing and deploying always need an explicit yes."""

from typing import Any

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app import deploy, google, personal
from app import workspace as wsp
from app.db import Review, SessionLocal
from app.github import Client
from app.skills import Permission, RunContext, SkillResult


class EmailArgs(BaseModel):
    to: list[EmailStr] = Field(min_length=1, max_length=20)
    subject: str = Field(max_length=200)
    body: str


class DraftEmail:
    name, input_schema, permission = "draft_email", EmailArgs, Permission.READ
    description = "Draft an email and show it to the user for review. Does not send."

    async def run(self, args: EmailArgs, ctx: RunContext) -> SkillResult:
        await ctx.emit("email.draft", to=list(args.to), subject=args.subject, body=args.body)
        return SkillResult(f"Here is the draft to {', '.join(args.to)}: {args.subject}. Say send it to send.",
                           args.model_dump())


class SendEmail:
    name, input_schema, permission = "send_email", EmailArgs, Permission.CRITICAL
    description = "Send an email from the user's connected Gmail account. Always needs an explicit yes."

    async def run(self, args: EmailArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            message_id = await google.send_email(db, ctx.user_id, list(args.to), args.subject, args.body)
        return SkillResult(f"Sent to {', '.join(args.to)}.", {"id": message_id})


class EventArgs(BaseModel):
    title: str
    start: str = Field(description="Local start, ISO like 2026-11-16T15:00.")
    end: str = Field(description="Local end, ISO like 2026-11-16T16:00.")
    description: str = ""
    attendees: list[EmailStr] = Field(default_factory=list)


class CreateEvent:
    name, input_schema, permission = "create_event", EventArgs, Permission.CHANGE
    description = "Create an event in the user's Google Calendar."

    async def run(self, args: EventArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            tz = personal.zone(db, ctx.user_id).key
            event = await google.create_event(db, ctx.user_id, args.title, args.start, args.end, tz,
                                              args.description, list(args.attendees))
        return SkillResult(f"Added {args.title} to your calendar.", event)


class GithubReviewArgs(BaseModel):
    pr_url: str = Field(description="https://github.com/<owner>/<repo>/pull/<n>")
    review_id: str = Field(default="", description="VoxAgent review to post; empty means the latest one.")


class PostReview:
    name, input_schema, permission = "github_post_review", GithubReviewArgs, Permission.CRITICAL
    description = "Post a VoxAgent code review as comments on a GitHub pull request. Always needs an explicit yes."

    async def run(self, args: GithubReviewArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            review = db.get(Review, args.review_id) if args.review_id else db.scalar(
                select(Review).where(Review.user_id == ctx.user_id).order_by(Review.created_at.desc()))
            if not review or review.user_id != ctx.user_id:
                raise ValueError("no review to post; review the pull request first")
            client = Client.for_user(db, ctx.user_id)
        comments = [{"path": i.get("file", ""), "line": i.get("line"),
                     "body": f"**{i.get('severity', 'note')}** ({i.get('category', 'issue')}): {i.get('explanation', '')}"
                             + (f"\n\nSuggested fix: {i['fix']}" if i.get("fix") else "")}
                    for i in review.issues[:50]]
        url = await client.post_review(args.pr_url, f"VoxAgent review: score {review.score}/100", comments)
        return SkillResult(f"Posted {len(comments)} comment(s) on the pull request.", {"url": url})


def _workspace_files(space: Any) -> dict[str, str]:
    base, files = wsp.root(space), {}
    for path in wsp.walk(base):
        try:
            files[wsp.rel(base, path)] = wsp.read_text(path)
        except wsp.WorkspaceError:
            continue  # binary files are skipped
    return files


class PrArgs(BaseModel):
    repo: str = Field(description="owner/name of the GitHub repository.")
    branch: str = Field(description="New branch name, e.g. voxagent/rename-fetch-user.")
    title: str
    body: str = ""


class OpenPullRequest:
    name, input_schema, permission = "github_open_pr", PrArgs, Permission.CRITICAL
    description = ("Commit the project's accepted changes to a new GitHub branch and open a pull request. "
                   "Always needs an explicit yes.")

    async def run(self, args: PrArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            space = await wsp.current(db, ctx.user_id)
            if wsp.pending(db, space):
                raise ValueError("some changes are still waiting for review; apply or reject them first")
            client = Client.for_user(db, ctx.user_id)
        url = await client.open_pr(args.repo, args.branch, args.title, args.body or "Opened by VoxAgent.",
                                   _workspace_files(space))
        return SkillResult(f"Opened the pull request: {url}", {"url": url})


class PublishArgs(BaseModel):
    name: str = Field(pattern=r"^[\w.-]{1,100}$")
    private: bool = True


class PublishRepo:
    name, input_schema, permission = "github_publish", PublishArgs, Permission.CRITICAL
    description = "Push the current project to a new GitHub repository. Always needs an explicit yes."

    async def run(self, args: PublishArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            space = await wsp.current(db, ctx.user_id)
            client = Client.for_user(db, ctx.user_id)
        url = await client.publish(args.name, args.private, _workspace_files(space), f"Initial commit of {space.name}")
        return SkillResult(f"Published to {url}", {"url": url})


class Empty(BaseModel):
    pass


class Deploy:
    name, input_schema, permission = "deploy_project", Empty, Permission.CRITICAL
    description = "Deploy the finished project to the configured hosting provider. Always needs an explicit yes."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            space = await wsp.current(db, ctx.user_id)
        result = await deploy.deploy(wsp.root(space), ctx.emit)
        return SkillResult(result["message"], result)


skills = [DraftEmail(), SendEmail(), CreateEvent(), PostReview(), OpenPullRequest(), PublishRepo(), Deploy()]
