"""Document & writing skill: drafts structured docs, reports, notes, and files into ~/Documents/VoxAgent."""

import asyncio
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.skills import Permission, RunContext, SkillResult, is_owner

DOCS_ROOT = Path("~/Documents/VoxAgent").expanduser().resolve()


class Args(BaseModel):
    title: str = Field(description="Short document title or filename (without extension).")
    prompt: str = Field(description="What to write: topic, structure, audience, or content instructions.")
    format: Literal["md", "txt", "html", "py", "json"] = Field(
        default="md",
        description="Output file format: 'md' (Markdown), 'txt', 'html', 'py', or 'json'.",
    )
    tone: str = Field(default="clear and structured", description="Writing tone or style.")
    open_after: bool = Field(default=True, description="Whether to open the created document on your Mac.")


def _folder(user_id: str) -> Path:
    """The owner's documents go to ~/Documents/VoxAgent; everyone else gets a folder of their own inside it."""
    return DOCS_ROOT if is_owner(user_id) else DOCS_ROOT / "people" / re.sub(r"[^A-Za-z0-9_-]", "", user_id)


def _safe_filename(title: str, ext: str, folder: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip()).strip("._") or "document"
    slug = slug[:80]
    folder.mkdir(parents=True, exist_ok=True)
    candidate = (folder / f"{slug}.{ext}").resolve()
    if not candidate.is_relative_to(folder.resolve()):
        raise ValueError("invalid document path")
    counter = 1
    while candidate.exists():
        candidate = (folder / f"{slug}_{counter}.{ext}").resolve()
        counter += 1
    return candidate


class DocWriter:
    name = "doc_writer"
    description = (
        "Create and write documents, reports, notes, essays, plans, or code files and save them to ~/Documents/VoxAgent. "
        "Use whenever the user asks to write a document, draft a report, create notes, or save written output to a file."
    )
    input_schema = Args
    permission = Permission.CHANGE

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        fmt = args.format
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are an expert writer. Produce a complete, ready-to-use {fmt.upper()} document. "
                    f"Tone: {args.tone}. Output ONLY the file contents with no outer markdown fences unless format is md."
                ),
            },
            {
                "role": "user",
                "content": f"Title: {args.title}\n\nRequest:\n{args.prompt}",
            },
        ]
        content = (await ctx.complete(messages)).strip()
        if not content:
            raise ValueError("the model returned empty document content")

        folder = _folder(ctx.user_id)
        out_path = _safe_filename(args.title, fmt, folder)
        out_path.write_text(content + "\n", encoding="utf-8")

        opened = False
        if args.open_after and is_owner(ctx.user_id):  # it opens on this Mac's screen, which is the owner's
            proc = await asyncio.create_subprocess_exec("open", "--", str(out_path))
            opened = (await proc.wait()) == 0

        word_count = len(content.split())
        summary = (
            f"Created {out_path.name} ({word_count} words) in {folder}"
            + (" and opened it." if opened else ".")
        )
        return SkillResult(
            summary,
            {"path": str(out_path), "format": fmt, "words": word_count, "opened": opened},
        )


skill = DocWriter()
