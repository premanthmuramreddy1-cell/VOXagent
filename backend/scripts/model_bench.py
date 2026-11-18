"""Score free models on small coding tasks against hidden tests, to choose VOX_LLM_CODE.
   uv run python -m scripts.model_bench [vendor:model ...]
"""

import asyncio
import re
import sys
import time

from app.providers import CloudLLM

TASKS = [
    ("merge_intervals", ("Write a Python function merge_intervals(items: list[list[int]]) -> list[list[int]] that merges "
     "overlapping or touching closed intervals and returns them sorted. Input may be unsorted and may be empty."),
     [([[1, 3], [2, 6], [8, 10], [15, 18]], [[1, 6], [8, 10], [15, 18]]), ([], []), ([[5, 7], [1, 5]], [[1, 7]]),
      ([[1, 4], [2, 3]], [[1, 4]]), ([[3, 3]], [[3, 3]])]),
    ("parse_duration", ("Write a Python function parse_duration(text: str) -> int returning total seconds for strings like "
     "'1h30m', '45s', '2h 5s', '1d2h' (d=day, h, m, s; spaces allowed; case-insensitive). Raise ValueError on empty or "
     "invalid input such as '', 'abc', '5x', '1h1h' (a unit may appear once)."),
     [("1h30m", 5400), ("45s", 45), ("2h 5s", 7205), ("1D2h", 93600), ("", ValueError), ("abc", ValueError),
      ("5x", ValueError), ("1h1h", ValueError)]),
    ("LRUCache", ("Write a Python class LRUCache with __init__(self, capacity: int), get(self, key) returning the value "
     "or -1, and put(self, key, value). Evict the least recently used key when over capacity; get counts as a use; "
     "capacity 0 stores nothing."), None),
    # harder: these separate models that all pass the three above
    ("calc", ("Write a Python function calc(expr: str) -> float evaluating + - * / and parentheses with normal "
     "precedence, left associativity, unary minus (e.g. '-(2+3)', '2*-3', '--4'), spaces and decimals. Do not use "
     "eval, exec or ast. Raise ValueError for malformed input ('', '2+', '(1', '1)', '2 3') and ZeroDivisionError "
     "for division by zero."),
     [("1+2*3", 7), ("(1+2)*3", 9), ("8/4/2", 1), ("2-3-4", -5), ("-(2+3)", -5), ("2*-3", -6), ("--4", 4),
      (" 1.5 * 2 ", 3), ("2^3", ValueError), ("", ValueError), ("2+", ValueError), ("(1", ValueError),
      ("1)", ValueError), ("2 3", ValueError), ("1/0", ZeroDivisionError)]),
    ("free_slots", ("Write a Python function free_slots(busy: list[tuple[int, int]], start: int, end: int, "
     "length: int) -> list[tuple[int, int]] returning every free gap of at least `length` minutes inside "
     "[start, end), as sorted (from, to) pairs. busy holds half-open (from, to) meetings in any order; they may "
     "overlap, touch, or lie partly or fully outside [start, end)."),
     [(([(60, 120), (90, 150), (200, 210)], 0, 300, 30), [(0, 60), (150, 200), (210, 300)]),
      (([], 0, 100, 100), [(0, 100)]), (([], 0, 100, 101), []), (([(0, 50), (50, 100)], 0, 100, 1), []),
      (([(-50, 10), (290, 400)], 0, 300, 10), [(10, 290)]), (([(40, 60)], 0, 100, 41), [])]),
]

DEFAULT = ["groq:openai/gpt-oss-120b", "nvidia:moonshotai/kimi-k3.1-pro", "nvidia:minimaxai/minimax-m3.1-flash",
           "openrouter:nvidia/nemotron-3.5-super-vl:free", "cloud:gemini-3.8-flash"]  # glm-5.3-flash: 0/14, timed out


def lru_ok(cls) -> bool:
    c = cls(2)
    c.put(1, 1)
    c.put(2, 2)
    first = c.get(1) == 1
    c.put(3, 3)
    evicted = c.get(2) == -1
    c.put(4, 4)
    order = c.get(1) == -1 and c.get(3) == 3 and c.get(4) == 4
    zero = cls(0)
    zero.put(1, 1)
    one = cls(1)
    one.put(1, 1)
    one.put(1, 9)
    return first and evicted and order and zero.get(1) == -1 and one.get(1) == 9


def score(name: str, code: str, cases) -> tuple[int, int]:
    ns: dict = {}
    exec(code, ns)  # noqa: S102 -- benchmark only: runs model-written code locally, never in the app
    if cases is None:
        try:
            return int(lru_ok(ns[name])), 1
        except Exception:
            return 0, 1
    ok = 0
    for arg, want in cases:
        try:
            got = ns[name](*arg) if isinstance(arg, tuple) and name == "free_slots" else ns[name](arg)
            ok += (got == want) if not isinstance(want, type) else 0
        except Exception as exc:
            ok += isinstance(want, type) and isinstance(exc, want)
    return ok, len(cases)


async def one(llm: CloudLLM, task) -> tuple[int, int, float]:
    name, prompt, cases = task
    messages = [{"role": "system", "content": "You are an expert Python engineer. Reply with one ```python block only."},
                {"role": "user", "content": prompt}]
    start, text = time.monotonic(), ""
    try:
        async for event in llm.stream(messages):
            text += event.text
        block = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
        ok, total = score(name, block.group(1) if block else text, cases)
    except Exception:
        ok, total = 0, len(cases or [1])
    return ok, total, time.monotonic() - start


async def bench(spec: str) -> None:
    vendor, _, model = spec.partition(":")
    results = await asyncio.gather(*(one(CloudLLM(model, vendor=vendor), t) for t in TASKS))
    ok, total = sum(r[0] for r in results), sum(r[1] for r in results)
    print(f"{spec[:56]:56} {ok:2}/{total}  avg {sum(r[2] for r in results) / len(results):5.1f}s", flush=True)


async def main(specs: list[str]) -> None:
    await asyncio.gather(*(bench(s) for s in specs))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT))
