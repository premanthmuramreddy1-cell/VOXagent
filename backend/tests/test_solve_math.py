import asyncio
from unittest.mock import AsyncMock

import pytest

from app.skills import RunContext
from app.skills.solve_math import Args, skill


def solve(**fields) -> tuple[str, dict]:
    ctx = RunContext("u1", "s1", "maths", [], AsyncMock(), AsyncMock())
    result = asyncio.run(skill.run(Args(**fields), ctx))
    return result.summary, result.data


@pytest.mark.parametrize("fields,expected", [
    ({"operation": "solve", "expression": "x^2 - 5x + 6 = 0"}, "x = 2; x = 3"),
    ({"operation": "solve", "expression": "2x + y = 7; x - y = 2"}, "x = 3, y = 1"),
    ({"operation": "solve", "expression": "x^2 + 1 = 0"}, "x = -I; x = I"),
    ({"operation": "differentiate", "expression": "x^3 sin(x)"}, "x**3*cos(x) + 3*x**2*sin(x)"),
    ({"operation": "integrate", "expression": "e^x", "lower": "0", "upper": "1"}, "-1 + E"),
    ({"operation": "integrate", "expression": "2x"}, "x**2"),
    ({"operation": "limit", "expression": "sin(x)/x", "point": "0"}, "1"),
    ({"operation": "series", "expression": "cos(x)", "order": 6}, "1 - x**2/2 + x**4/24 + O(x**6)"),
    ({"operation": "factor", "expression": "x^4 - 1"}, "(x - 1)*(x + 1)*(x**2 + 1)"),
    ({"operation": "inverse", "expression": "[[1,2],[3,4]]"}, "Matrix([[-2, 1], [3/2, -1/2]])"),
    ({"operation": "eigenvalues", "expression": "[[2,0],[0,3]]"}, "2, 3"),
])
def test_solve_math_gets_it_right(fields, expected):
    _, data = solve(**fields)
    assert data["result"] == expected


def test_solutions_are_checked_by_substituting_back():
    summary, data = solve(operation="solve", expression="x^2 - 5x + 6 = 0")
    assert data["checked"] is True and "every solution works" in summary


def test_an_indefinite_integral_mentions_the_constant():
    summary, _ = solve(operation="integrate", expression="cos(x)")
    assert "constant C" in summary


@pytest.mark.parametrize("bad", ["__import__('os').system('id')", "exec(1)", "x.__class__", "open('/etc/passwd')",
                                 "lambda: 1", "getattr(x, 'y')", "'a'"])
def test_anything_that_is_not_maths_is_refused(bad):
    with pytest.raises(ValueError, match="could not work that out"):
        solve(operation="evaluate", expression=bad)


def test_a_runaway_problem_is_stopped(monkeypatch):
    from app.skills import solve_math

    monkeypatch.setattr(solve_math, "TIMEOUT", 0.01)
    with pytest.raises(ValueError, match="too long"):
        solve(operation="integrate", expression="x^2")
