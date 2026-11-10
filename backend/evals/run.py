"""Measure code review quality against files with known issues.

    uv run python -m evals.run            # static analysis only (deterministic)
    uv run python -m evals.run --llm      # also the LLM pass (needs Ollama running)
    uv run python -m evals.run --llm --save   # record the result as the new baseline
"""

import asyncio
import json
import sys
from pathlib import Path

from app.skills import RunContext
from app.skills.code_review import llm_issues, merge, static_analysis

HERE = Path(__file__).parent
CASES = HERE / "cases"
BASELINE = HERE / "baseline.json"


def hit(expected: dict, issues: list[dict]) -> bool:
    return any(
        i["file"] == expected["file"] and i["category"] == expected["category"]
        and abs(i["line"] - expected["line"]) <= 1
        for i in issues
    )


async def evaluate(use_llm: bool) -> dict:
    files = {p.name: p.read_text() for p in sorted(CASES.iterdir()) if p.suffix in {".py", ".js"}}
    expected = json.loads((CASES / "expected.json").read_text())
    issues, _ = await static_analysis(files)
    if use_llm:
        from app.agent import Agent
        from app.providers import providers

        agent = Agent(providers().llm, {})

        async def noop(*_a, **_k) -> None: ...

        ctx = RunContext("eval", "eval", "", [], noop, lambda m, s=None: agent.complete("eval", m, s))
        issues = merge(issues, await llm_issues(files, ctx, [], limit=len(files)))
    static = [e for e in expected if not e["needs_llm"]]
    result: dict = {"static_recall": sum(hit(e, issues) for e in static) / len(static)}
    if use_llm:
        deep = [e for e in expected if e["needs_llm"]]
        result["llm_recall"] = sum(hit(e, issues) for e in deep) / len(deep)
        result["issues_reported"] = len(issues)
    result["missed"] = [f"{e['file']}:{e['line']}" for e in expected if (use_llm or not e["needs_llm"]) and not hit(e, issues)]
    return result


if __name__ == "__main__":
    outcome = asyncio.run(evaluate("--llm" in sys.argv))
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    print(json.dumps({"result": outcome, "baseline": baseline}, indent=2))
    if "--save" in sys.argv:
        BASELINE.write_text(json.dumps({**baseline, **{k: v for k, v in outcome.items() if k.endswith("recall")}}, indent=2))
    regressed = [k for k, v in baseline.items() if v is not None and outcome.get(k, v) < v]
    sys.exit(f"regressed: {regressed}" if regressed else 0)
