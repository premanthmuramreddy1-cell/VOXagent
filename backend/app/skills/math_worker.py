"""Symbolic maths, run in its own process: `python -m app.skills.math_worker` reads one JSON job on stdin.

SymPy's parser evaluates Python, so the input is locked down three ways: a strict token whitelist before parsing,
a parser namespace holding only whitelisted SymPy names (no builtins), and this separate process with CPU and
memory limits that the caller also kills after a timeout.
"""

import json
import re
import sys

ALLOWED_CHARS = re.compile(r"^[0-9A-Za-z_+\-*/^().,=\[\]\s]*$")
NAMES = {
    # functions
    "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "asinh",
    "acosh", "atanh", "exp", "log", "ln", "sqrt", "cbrt", "root", "Abs", "abs", "factorial", "binomial", "gamma",
    "floor", "ceiling", "sign", "Min", "Max", "gcd", "lcm", "re", "im", "conjugate", "Sum", "Product",
    "Matrix", "Rational", "Eq", "Piecewise", "erf", "Heaviside", "DiracDelta",
    # constants
    "pi", "E", "I", "oo", "inf", "infinity",
}
GREEK = {"alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda_", "mu", "nu", "rho", "sigma", "tau", "phi",
         "omega", "lam"}
MAX_CHARS = 600


def check(text: str) -> None:
    if len(text) > MAX_CHARS:
        raise ValueError("expression is too long")
    if not ALLOWED_CHARS.match(text) or "__" in text:
        raise ValueError("only numbers, variables, operators and maths functions are allowed")
    for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text):
        if word not in NAMES and word not in GREEK and not re.fullmatch(r"[A-Za-z][0-9]?|[A-Za-z]_[0-9]+", word):
            raise ValueError(f"unknown name '{word}': use single-letter variables and standard maths functions")


def _limits() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
    except (ImportError, ValueError, OSError):  # not every platform allows it; the caller's timeout still applies
        pass


def run(job: dict) -> dict:
    import sympy as sp  # type: ignore[import-untyped]
    from sympy.parsing.sympy_parser import (  # type: ignore[import-untyped]convert_xor, implicit_multiplication_application, parse_expr,
                                            standard_transformations)

    names = {n: getattr(sp, n) for n in NAMES if hasattr(sp, n)}
    names |= {"e": sp.E, "ln": sp.log, "abs": sp.Abs, "inf": sp.oo, "infinity": sp.oo, "cbrt": sp.cbrt, "lam": sp.Symbol("lam"),
              "lambda_": sp.Symbol("lambda")}
    for helper in ("Integer", "Float", "Rational", "Symbol", "Function"):  # the parser's own number and name wrappers
        names[helper] = getattr(sp, helper)
    transforms = (*standard_transformations, implicit_multiplication_application, convert_xor)

    def parse(text: str) -> object:
        check(text)
        return parse_expr(text, local_dict={}, global_dict={"__builtins__": {}, **names},
                          transformations=transforms, evaluate=True)

    def equation(text: str) -> object:
        if text.count("=") > 1:
            raise ValueError("one '=' per equation")
        if "=" in text:
            left, right = text.split("=")
            return sp.Eq(parse(left), parse(right))
        return sp.Eq(parse(text), 0)

    op = job["operation"]
    text = str(job["expression"]).strip()
    var = sp.Symbol(job["variable"]) if job.get("variable") else None
    out: dict = {"operation": op}

    if op == "solve":
        eqs = [equation(part) for part in text.split(";") if part.strip()]
        symbols = sorted(set().union(*(e.free_symbols for e in eqs)), key=str)  # type: ignore[attr-defined]
        unknowns = [var] if var and len(eqs) == 1 else symbols
        solutions = sp.solve(eqs, unknowns, dict=True)
        checked = all(sp.simplify(e.lhs.subs(s) - e.rhs.subs(s)) == 0 for s in solutions for e in eqs)  # type: ignore[attr-defined]
        out["result"] = "; ".join(", ".join(f"{k} = {v}" for k, v in s.items()) for s in solutions) or "no solution"
        out["numeric"] = "; ".join(", ".join(f"{k} ≈ {sp.N(v, 10)}" for k, v in s.items()) for s in solutions)
        out["latex"] = ", ".join(sp.latex(s) for s in solutions)
        out["checked"] = bool(solutions) and checked
        return out

    if op in ("determinant", "inverse", "eigenvalues", "rank"):
        matrix = sp.Matrix(parse(text))
        value = {"determinant": matrix.det, "inverse": matrix.inv, "rank": matrix.rank,
                 "eigenvalues": matrix.eigenvals}[op]()
        out["result"], out["latex"] = str(value), sp.latex(value)
        if op == "eigenvalues":  # {2: 1, 3: 2} reads as "2, 3 (twice)"
            out["result"] = ", ".join(f"{k}" + (f" (repeated {m} times)" if m > 1 else "") for k, m in value.items())
        return out

    expr = parse(text)
    free = sorted(getattr(expr, "free_symbols", set()), key=str)
    var = var or (free[0] if free else sp.Symbol("x"))
    if op == "simplify":
        value = sp.simplify(expr)
    elif op == "factor":
        value = sp.factor(expr)
    elif op == "expand":
        value = sp.expand(expr)
    elif op == "differentiate":
        value = sp.diff(expr, var, int(job.get("order") or 1))
    elif op == "integrate":
        if job.get("lower") not in (None, "") and job.get("upper") not in (None, ""):
            value = sp.integrate(expr, (var, parse(str(job["lower"])), parse(str(job["upper"]))))
        else:
            value = sp.integrate(expr, var)
            out["note"] = "plus a constant C"
    elif op == "limit":
        value = sp.limit(expr, var, parse(str(job.get("point") or "0")))
    elif op == "series":
        value = sp.series(expr, var, parse(str(job.get("point") or "0")), int(job.get("order") or 6))
    elif op == "evaluate":
        value = expr
    else:
        raise ValueError(f"unknown operation {op}")
    out["result"], out["latex"] = str(value), sp.latex(value)
    if getattr(value, "is_number", False) and not getattr(value, "is_Integer", False):
        out["numeric"] = str(sp.N(value, 15))
    return out


def main() -> None:
    _limits()
    try:
        print(json.dumps(run(json.loads(sys.stdin.read()))))
    except Exception as error:  # every failure goes back as a message, never a traceback
        print(json.dumps({"error": str(error)[:300] or type(error).__name__}))


if __name__ == "__main__":
    main()
