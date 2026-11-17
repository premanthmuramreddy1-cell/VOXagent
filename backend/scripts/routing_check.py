"""Live routing check: real prompts through the real model chain, skills stubbed, so we see which tool each
request reaches without side effects.   uv run python -m scripts.routing_check
"""

import asyncio
import os
import sys
import time

from app.agent import Agent, Turn
from app.providers import providers
from app.skills import SkillResult, discover

MS_FORM = "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=EXAMPLE-FORM-ID"  # any form link: tools are stubbed
COFFEE = [{"role": "user", "content": "build a website for a coffee shop"},
          {"role": "assistant", "content": "I built the coffee shop website."}]
PACE = float(os.environ.get("ROUTING_PACE", "0"))  # seconds between prompts
CASES = [  # (prompt, history, skills that count as right)
    (f"fill this form {MS_FORM}", [], {"fill_form"}),
    (f"answer the questions in this quiz and submit it {MS_FORM}", [], {"fill_form"}),
    ("build a website for a coffee shop", [], {"create_project"}),
    ("show preview", COFFEE, {"start_server"}),
    ("where is the link", COFFEE, {"start_server"}),
    ("set the volume to 30", [], {"system_control"}),
    ("open spotify", [], {"system_control"}),
    ("how much battery do I have", [], {"system_control"}),
    ("pause the music", [], {"system_control"}),
    ("open youtube.com", [], {"system_control"}),
    ("make a 3d guitar", [], {"build_object"}),
    ("create a 3d model of a dragon", [], {"build_object"}),
    ("build the eiffel tower in 3d", [], {"build_object"}),
    ("show me a car in 3d", [], {"build_object"}),
    # more variety: typos, Hinglish, follow-ups, sign-in, and chat that must not touch any tool (empty set)
    ("fil this gogle form for me https://forms.gle/abc123XYZ", [], {"fill_form"}),
    ("submit it", [{"role": "user", "content": f"fill this form {MS_FORM}"},
                   {"role": "assistant", "content": "I answered 2 of 2 questions. Shall I submit?"}], {"submit_form"}),
    ("sign in for forms so my name shows on responses", [], {"form_sign_in"}),
    ("make a portfolio website for a photographer", [], {"create_project"}),
    ("open the site you made", COFFEE, {"start_server"}),
    ("mute my laptop", [], {"system_control"}),
    ("play next song", [], {"system_control"}),
    ("quit safari", [], {"system_control"}),
    ("open my downloads folder", [], {"system_control"}),
    ("volume kam karo", [], {"system_control"}),
    ("ek 3d rocket banao", [], {"build_object"}),
    ("show me the solar system", [], {"build_object"}),
    ("can the bots make a horse", [], {"build_object"}),
    ("remind me to call mom at 6pm", [], {"add_reminder"}),
    ("whats the latest news", [], {"world_briefing", "web_search"}),
    ("what is the capital of france", [], set()),
    ("tell me a joke", [], set()),
    ("how does photosynthesis work, in one line", [], set()),
    ("thanks that's all", [], set()),
]


async def one(agent: Agent, prompt: str, history: list[dict]) -> tuple[list[str], str]:
    called: list[str] = []

    async def emit(*_a, **_k) -> None: ...

    async def confirm(_request) -> str:
        return "yes"

    answer, _ = await agent.run(Turn("routing-check", "routing-check", prompt, emit, confirm, [], history))
    return called, answer


async def main() -> int:
    skills = discover()
    called: list[str] = []
    for skill in skills.values():
        async def run(args, ctx, name=skill.name):
            called.append(name)
            return SkillResult(f"{name} done (test stub).", {})
        object.__setattr__(skill, "run", run) if hasattr(skill, "__dict__") else None
    agent = Agent(providers().llm, skills)
    misses, total = 0, 0.0
    for prompt, history, good in CASES:
        called.clear()
        await asyncio.sleep(PACE)  # free tiers allow a few thousand tokens a minute: test at a person's pace
        start = time.perf_counter()
        try:
            answer, _ = await agent.run(Turn("routing-check", "routing-check", prompt, _noop, _yes, [], history))
        except Exception as exc:
            answer = f"ERROR {exc}"
        ok = bool(good & set(called)) if good else not called  # chat must not run any tool
        misses += not ok
        took = time.perf_counter() - start
        total += took
        print(f"{'PASS' if ok else 'MISS'} | {took:4.1f}s | {prompt[:50]:50} | called={called} | {answer[:80]!r}",
              flush=True)
    print(f"{len(CASES) - misses}/{len(CASES)} routed right, {total / len(CASES):.1f}s per request on average")
    return 1 if misses else 0


async def _noop(*_a, **_k) -> None: ...


async def _yes(_request) -> str:
    return "yes"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
