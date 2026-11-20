"""Calculate skill: exact, checkable maths so the model never does arithmetic in its head.

A small safe evaluator (no eval, no names, no attribute access). Integers and fractions stay exact; functions
give floats. It handles percentages, powers, roots, trig, logs, combinatorics and basic statistics.
"""

import ast
import math
import operator
import re
import statistics
from collections.abc import Callable
from fractions import Fraction
from typing import Any

from pydantic import BaseModel, Field

from app.skills import Permission, RunContext, SkillResult

MAX_CHARS = 400
MAX_EXPONENT = 10_000
MAX_DIGITS = 4_000

BINARY: dict[type, Callable[..., Any]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
          ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
UNARY: dict[type, Callable[..., Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")  # "3,870" is 3870; "comb(10,3)" keeps its comma
Number = float | Fraction


def _percent_of(p: float, x: float) -> float:
    return p / 100 * x


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt, "cbrt": math.cbrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sind": lambda d: math.sin(math.radians(d)), "cosd": lambda d: math.cos(math.radians(d)),
    "tand": lambda d: math.tan(math.radians(d)),
    "ln": math.log, "log": math.log10, "log2": math.log2, "exp": math.exp,
    "factorial": math.factorial, "gcd": math.gcd, "lcm": math.lcm, "comb": math.comb, "perm": math.perm,
    "min": min, "max": max, "sum": lambda *a: sum(a), "mean": lambda *a: statistics.fmean(a),
    "median": lambda *a: statistics.median(a), "stdev": lambda *a: statistics.stdev(a),
    "pstdev": lambda *a: statistics.pstdev(a), "percent_of": _percent_of,
}


def _clean(value: object) -> Number:
    if isinstance(value, bool) or not isinstance(value, int | float | Fraction):
        raise TypeError("only numbers are allowed")
    return value


def _eval(node: ast.AST) -> Number:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        return Fraction(str(_clean(node.value)))  # every number stays exact until a function needs a float
    if isinstance(node, ast.Name):
        if node.id not in CONSTANTS:
            raise ValueError(f"unknown name '{node.id}'")
        return CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(left, float) or isinstance(right, float):  # after a function or constant: plain floats
            left, right = float(left), float(right)
            if isinstance(node.op, ast.Pow) and (abs(right) > MAX_EXPONENT or (left < 0 and not right.is_integer())):
                raise ValueError("that power is too large or not real")
            if isinstance(node.op, ast.Mod | ast.FloorDiv | ast.Div) and right == 0:
                raise ValueError("division by zero")
            return float(BINARY[type(node.op)](left, right))
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT or (right.denominator != 1 and left < 0):
                raise ValueError("that power is too large or not real")
            if right.denominator != 1:
                return float(left) ** float(right)  # a fractional power has no exact form
            right = int(right)
        if isinstance(node.op, ast.Mod | ast.FloorDiv | ast.Div) and right == 0:
            raise ValueError("division by zero")
        result = BINARY[type(node.op)](left, right)
        if isinstance(result, Fraction) and result.numerator.bit_length() > MAX_DIGITS * 3.33:
            raise ValueError("that number is too large")
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
        args = [(int(a) if a.denominator == 1 else float(a)) if isinstance(a, Fraction) else a
                for a in (_eval(x) for x in node.args)]
        if node.func.id == "factorial" and (not args or args[0] > 5_000):
            raise ValueError("that factorial is too large")
        out = _clean(FUNCTIONS[node.func.id](*args))
        return out if isinstance(out, float) else Fraction(out)
    raise ValueError("that is not a calculation I can evaluate")


def evaluate(expression: str) -> Number:
    text = expression.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    text = THOUSANDS.sub("", text)
    text = text.replace("%", "/100")  # "15% of 80" is written 15/100*80; a bare "15%" is 0.15
    if not text or len(text) > MAX_CHARS:
        raise ValueError("give one expression under 400 characters")
    try:
        return _eval(ast.parse(text, mode="eval"))
    except SyntaxError as error:
        raise ValueError(f"could not read the expression: {error.msg}") from error
    except (OverflowError, ZeroDivisionError, TypeError) as error:
        raise ValueError(f"cannot compute that: {error}") from error


def show(value: Number) -> str:
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return str(value.numerator)
        rest = value.denominator
        for factor in (2, 5):
            while rest % factor == 0:
                rest //= factor
        if rest == 1:  # a terminating decimal: show it plainly
            return f"{float(value):.12g}"
        return f"{value.numerator}/{value.denominator} ≈ {float(value):.10g}"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else f"{value:.12g}"
    return str(value)


class Args(BaseModel):
    expression: str = Field(description=(
        "One arithmetic expression, e.g. '(1250*1.18 - 300)/4', '15% * 80', 'sqrt(144)+2^10', 'comb(10,3)', "
        "'mean(4,8,15,16,23,42)', 'sind(30)'. Numbers only: no variables."))


class Calculate:
    name = "calculate"
    description = (
        "Do exact arithmetic and maths: sums, percentages, discounts, tips, loans, unit conversions as ratios, powers, "
        "roots, trigonometry, logarithms, combinations, averages and standard deviation. Use it for ANY calculation "
        "with more than one step, big numbers or decimals instead of working it out yourself, and to double-check a result. "
        "Like 'what is 18% of 2450', 'split 3,870 between 7 people', 'compound interest', 'how many ways to pick 3 from 10'."
    )
    input_schema = Args
    permission = Permission.READ

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        value = evaluate(args.expression)
        result = show(value)
        return SkillResult(f"{args.expression.strip()} = {result}", {"expression": args.expression.strip(), "result": result})


skill = Calculate()
