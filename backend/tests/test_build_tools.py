import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.skills import RunContext
from app.skills import build_tools as bt
from app.skills.build_tools import BUILTIN, BuildObject, builtin_name


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No real-model lookup unless a test asks for one: these tests cover the designed path."""
    async def none(name):
        return None
    monkeypatch.setattr(bt, "find_model", none)


def run(name: str, reply: str = "", details: str = ""):
    emit = AsyncMock()

    async def complete(messages, schema=None):
        return reply

    ctx = RunContext("u", "s", "", [], emit, complete)
    result = asyncio.run(BuildObject().run(BuildObject.input_schema(name=name, details=details), ctx))
    return result, emit


def test_builtin_names_match_the_web_client():
    web = (Path(__file__).parents[2] / "web" / "src" / "shapes.ts").read_text()
    block = web[web.index("export const BUILTIN"):web.index("/** mulberry32")]
    names = set(re.findall(r"^\s+'?([a-z ]+)'?: \[", block, re.MULTILINE))
    assert names == set(BUILTIN)


def test_ready_made_objects_and_aliases_need_no_model():
    assert [builtin_name(n) for n in ("a 3D Guitar", "the solar system", "mug", "Earth", "space-ship", "teapot")] == [
        "guitar", "solar system", "cup", "globe", "rocket", ""]
    result, emit = run("3d rocket")
    emit.assert_awaited_once_with("swarm.build", name="rocket", builtin="rocket")
    assert result.summary == "The bots are building a rocket."


def test_designed_object_is_validated_and_clamped():
    parts = [{"kind": "cylinder", "at": [0, 5, 0], "size": [0.2, 99, 0.2]}, {"kind": "cone", "at": [0, 0.8, 0],
             "size": [0.5, 0.4, 0.5], "turn": [0, 0, 720]}] + [{"kind": "sphere", "at": [0, 0, 0], "size": [0.1] * 3}] * 40
    result, emit = run("Eiffel tower", json.dumps({"parts": parts}))
    sent = emit.await_args.kwargs["parts"]
    assert len(sent) == 32  # capped
    assert sent[0]["at"] == [0, 1, 0] and sent[0]["size"] == [0.2, 2, 0.2] and sent[1]["turn"] == [0, 0, 360]
    assert "from 32 parts" in result.summary


def test_a_broken_design_or_blank_name_is_reported_not_sent():
    for reply in ("not json", json.dumps({"parts": [{"kind": "pyramid", "at": [0, 0, 0], "size": [1, 1, 1]}]}),
                  json.dumps({"parts": []})):
        result, emit = run("teapot", reply)
        emit.assert_not_awaited()
        assert "couldn't design a teapot" in result.summary
    result, emit = run("   ")
    assert result.summary == "What should the bots build?"


def stl_binary(triangles):
    import struct
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0) for a, b, c in triangles)
    return b"x" * 80 + struct.pack("<I", len(triangles)) + body


TALL = [((0, 0, 0), (1, 0, 0), (0, 0, 4)), ((1, 0, 0), (1, 1, 0), (1, 0, 4))]  # 4 high along STL's z axis


def test_stl_files_parse_in_binary_and_ascii():
    assert bt.parse_stl(stl_binary(TALL)) == [tuple(tuple(float(v) for v in p) for p in t) for t in TALL]
    ascii_stl = b"solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 0 4\nendloop\nendfacet\n"
    assert bt.parse_stl(ascii_stl) == [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 4.0))]
    assert bt.parse_stl(b"garbage") == []


def test_sampled_points_are_framed_and_stand_upright():
    pts = bt.sample(bt.parse_stl(stl_binary(TALL)), n=400)
    assert len(pts) == 1200 and max(abs(v) for v in pts) <= 1.0
    ys, xs = pts[1::3], pts[0::3]
    assert max(ys) - min(ys) > 3 * (max(xs) - min(xs))  # STL z-up became y-up: the shape stands, not lies down
    assert bt.sample([((0, 0, 0), (0, 0, 0), (0, 0, 0))]) == []  # no surface, no points


def test_model_titles_must_name_the_thing():
    assert bt.relevant("File:EiffelTower fixed.stl", bt.keywords("3d eiffel tower"))
    assert not bt.relevant("File:Eiffel.stl", bt.keywords("eiffel tower"))
    assert not bt.relevant("File:Cartwheel Galaxy Tactile Plate.stl", bt.keywords("bicycle"))
    assert bt.head_noun("human skull") == "skull" and bt.head_noun("statue of liberty") == "statue"


def test_a_real_model_is_sent_as_points_with_credit(monkeypatch):
    async def found(name):
        return [0.0, 1.0, 0.0] * 10, "https://commons.wikimedia.org/wiki/File:0_A.D._giraffe_adult.stl"
    monkeypatch.setattr(bt, "find_model", found)
    result, emit = run("giraffe")
    assert emit.await_args.kwargs["points"][:3] == [0.0, 1.0, 0.0] and "real 3D model" in result.summary
    assert result.final  # spoken as written: the model used to wander off ("instructions to assemble the bicycle")


def test_a_looping_design_is_salvaged_without_repeats():
    part = '{"kind": "cylinder", "at": [0, 0, %s], "size": [0.1, 1, 0.1]}'
    looping = '{"parts": [{"kind": "sphere", "at": [0, 0.5, 0], "size": [0.6, 0.4, 0.4]}, ' + \
        ", ".join(part % (i / 10) for i in range(40)) + ', {"ki'  # cut off mid-part, as num_predict does
    _, emit = run("giraffe", looping)
    assert [p["kind"] for p in emit.await_args.kwargs["parts"]] == ["sphere", "cylinder"]
