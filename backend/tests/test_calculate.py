import asyncio
from unittest.mock import AsyncMock

import pytest

from app.skills import RunContext
from app.skills.calculate import Args, evaluate, show, skill


def run(expression: str) -> str:
    ctx = RunContext("u1", "s1", "calc", [], AsyncMock(), AsyncMock())
    return asyncio.run(skill.run(Args(expression=expression), ctx)).data["result"]


@pytest.mark.parametrize("expression,expected", [
    ("2+3*4", "14"),
    ("(1250*1.18 - 300)/4", "293.75"),
    ("2^10", "1024"),
    ("15% * 80", "12"),
    ("1/3 + 1/6", "0.5"),
    ("1/3", "1/3 ≈ 0.3333333333"),
    ("0.1 + 0.2", "0.3"),  # exact decimals, not 0.30000000000000004
    ("sqrt(144) + 2", "14"),
    ("comb(10,3)", "120"),
    ("mean(4,8,15,16,23,42)", "18"),
    ("sind(30)", "0.5"),
    ("3,870 / 7", "3870/7 ≈ 552.8571429"),
])
def test_calculate_gets_it_right(expression, expected):
    assert run(expression) == expected


@pytest.mark.parametrize("bad", [
    "__import__('os').system('id')", "open('x')", "a + 1", "9**99999", "1/0", "factorial(99999)", "x" * 500, "", "[1,2]",
    "(-8)**0.5", "'a'*3", "True + 1",
])
def test_calculate_refuses_anything_that_is_not_a_calculation(bad):
    with pytest.raises(ValueError):
        evaluate(bad)


def test_show_formats_whole_floats_without_a_tail():
    assert show(4.0) == "4" and show(2 ** 0.5) == "1.41421356237"
