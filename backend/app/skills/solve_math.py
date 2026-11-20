"""Solve maths symbolically: equations and systems, algebra, calculus, limits, series and matrices.

The work runs in app.skills.math_worker, a separate process with a hard timeout, so a hard integral or a hostile
input can never stall or reach the server.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.skills import Permission, RunContext, SkillResult

TIMEOUT = 20.0  # seconds
ROOT = Path(__file__).resolve().parents[2]  # backend/, so `-m app.skills.math_worker` resolves

Operation = Literal["solve", "simplify", "factor", "expand", "differentiate", "integrate", "limit", "series",
                    "evaluate", "determinant", "inverse", "eigenvalues", "rank"]


class Args(BaseModel):
    operation: Operation = Field(description=(
        "solve: equations or a system ('x^2 - 5x + 6 = 0', '2x + y = 7; x - y = 2'); simplify / factor / expand; "
        "differentiate; integrate (indefinite, or definite with lower and upper); limit (at point); series (around "
        "point); evaluate: exact value; determinant / inverse / eigenvalues / rank of a matrix like [[1,2],[3,4]]."))
    expression: str = Field(description="The maths in plain notation: ^ for powers, sqrt(), sin(), log(), pi, E, oo.")
    variable: str = Field(default="", description="The variable to solve for, differentiate or integrate by, e.g. x.")
    lower: str = Field(default="", description="Lower bound of a definite integral.")
    upper: str = Field(default="", description="Upper bound of a definite integral.")
    point: str = Field(default="", description="The point for a limit or series, e.g. 0 or oo.")
    order: int = Field(default=0, ge=0, le=20, description="Derivative order, or number of series terms.")


async def compute(job: dict) -> dict:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-E", "-s", "-m", "app.skills.math_worker", cwd=ROOT,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(json.dumps(job).encode()), timeout=TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError("that took too long to work out: try a simpler form or ask for a numeric answer") from None
    try:
        result = json.loads(out.decode() or "{}")
    except ValueError:
        result = {}
    if not result or "error" in result:
        raise ValueError(f"could not work that out: {result.get('error', 'the solver stopped')}")
    return result


class SolveMath:
    name = "solve_math"
    description = (
        "Solve maths exactly, like a computer algebra system: equations and systems of equations, factorising, "
        "simplifying, derivatives, integrals, limits, series and matrices. Checks its solutions by substituting "
        "them back. Use for algebra and calculus homework, 'solve x^2-5x+6=0', 'differentiate x^3 sin(x)', "
        "'integrate e^x from 0 to 1', 'find the inverse of this matrix'. For plain arithmetic use calculate."
    )
    input_schema = Args
    permission = Permission.READ

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        result = await compute(args.model_dump())
        line = f"{args.operation} {args.expression}: {result['result']}"
        if result.get("note"):
            line += f" ({result['note']})"
        if result.get("numeric") and result["numeric"] != result["result"]:
            line += f"\nNumerically: {result['numeric']}"
        if "checked" in result:
            line += "\nChecked by substituting back: " + ("every solution works." if result["checked"] else
                                                           "could not confirm, so double-check it.")
        return SkillResult(line, result)


skill = SolveMath()
