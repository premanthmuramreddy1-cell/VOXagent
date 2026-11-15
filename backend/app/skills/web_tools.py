"""Web tools: search the internet, read a page, check a package's latest version. Results cite their links."""

from typing import Literal

from pydantic import BaseModel, Field

from app import web
from app.skills import Permission, RunContext, SkillResult


class SearchArgs(BaseModel):
    query: str = Field(description="What to search for: an error message, docs topic, library, news.")


class WebSearch:
    name, input_schema, permission = "web_search", SearchArgs, Permission.READ
    description = ("Search the internet for current information (docs, error messages, library versions, news). "
                   "Returns titles, links and snippets to cite.")

    async def run(self, args: SearchArgs, ctx: RunContext) -> SkillResult:
        hits = await web.search(args.query)
        listing = "\n".join(f"- {h['title']} ({h['url']}): {h['snippet']}" for h in hits)
        return SkillResult(f"Search results for {args.query!r} (cite these links):\n{listing}" if hits
                           else "No results found.", {"results": hits})


class FetchArgs(BaseModel):
    url: str = Field(description="A public http(s) URL: a docs page, article or GitHub file.")


class WebFetch:
    name, input_schema, permission = "web_fetch", FetchArgs, Permission.READ
    description = "Open a public web page or GitHub file and read its text."

    async def run(self, args: FetchArgs, ctx: RunContext) -> SkillResult:
        url = args.url.replace("github.com/", "raw.githubusercontent.com/", 1).replace("/blob/", "/", 1) \
            if "github.com/" in args.url and "/blob/" in args.url else args.url
        page = await web.fetch(url)
        return SkillResult(f"{page['title']} ({page['url']}):\n{page['text'][:8000]}",
                           {"url": page["url"], "title": page["title"]})


class VersionArgs(BaseModel):
    manager: Literal["pip", "npm", "cargo", "go"]
    name: str


class PackageInfo:
    name, input_schema, permission = "package_version", VersionArgs, Permission.READ
    description = "Look up the latest published version of a library on PyPI, npm, crates.io or the Go proxy."

    async def run(self, args: VersionArgs, ctx: RunContext) -> SkillResult:
        version = await web.latest_version(args.manager, args.name)
        return SkillResult(f"The latest {args.manager} release of {args.name} is {version}.",
                           {"manager": args.manager, "name": args.name, "version": version})


skills = [WebSearch(), WebFetch(), PackageInfo()]
