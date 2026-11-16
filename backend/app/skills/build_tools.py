"""Build a 3D object out of the nanobot swarm ('make a 3d guitar', 'show me a rocket', 'import a bottle'), carried
over from J.A.R.V.I.S. Common objects are built in on the client. Anything else is first looked up as a real 3D
model (an STL scan or print file on Wikimedia Commons, freely licensed) and sampled into points here; only when none
fits does the model design it from a handful of simple parts. The model only ever returns numbers that are checked
and clamped below, never code."""

import json
import math
import random
import re
import struct
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.skills import Permission, RunContext, SkillResult
from app.web import UA

# Kept in step with BUILTIN in web/src/shapes.ts (a test checks both lists match).
BUILTIN = ("bottle", "cup", "guitar", "rocket", "plane", "car", "house", "tree", "robot", "atom", "solar system",
           "heart", "chair", "globe")
ALIASES = {"planets": "solar system", "solar": "solar system", "mug": "cup", "glass": "cup", "airplane": "plane",
           "aeroplane": "plane", "jet": "plane", "home": "house", "building": "house", "earth": "globe",
           "world": "globe", "love": "heart", "spaceship": "rocket", "bot": "robot", "automobile": "car"}
MAX_PARTS = 32
COMMONS = "https://commons.wikimedia.org/w/api.php"
MAX_MODEL_BYTES = 30 * 1024 * 1024
POINTS = 5000  # sent to the client, which resamples them to the swarm's size
NOISE = {"3d", "model", "a", "an", "the", "of", "me", "make", "show", "build", "import", "please"}


class Part(BaseModel):
    kind: Literal["sphere", "box", "cylinder", "cone", "torus"]
    at: list[float] = Field(min_length=3, max_length=3)
    size: list[float] = Field(min_length=3, max_length=3)
    turn: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)


DESIGN_SCHEMA = {
    "type": "object",
    "properties": {"parts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["sphere", "box", "cylinder", "cone", "torus"]},
            "at": {"type": "array", "items": {"type": "number"}},
            "size": {"type": "array", "items": {"type": "number"}},
            "turn": {"type": "array", "items": {"type": "number"}},
        },
        "required": ["kind", "at", "size"],
    }}},
    "required": ["parts"],
}

DESIGNER = """You design a recognisable 3D shape for: {name}{details}
Build it from 6 to 12 simple parts, each one different. Reply with JSON only: {{"parts": [...]}}. Each part:
- kind: sphere, box, cylinder (axis along y), cone (tip pointing +y) or torus (ring in the x-z plane).
- at: [x, y, z] centre. y is up. The whole object fits inside -1..1 on every axis.
- size: [x, y, z] full width, height and depth (for torus: x = ring diameter, y = tube thickness).
- turn: [x, y, z] rotation in degrees, optional.
Use the parts a person would recognise first: the overall silhouette, then 2-4 telling details."""


def builtin_name(name: str) -> str:
    """'a 3D Guitar', 'the solar system', 'mug' -> the built-in it matches, else ''."""
    words = [w for w in name.lower().replace("3d", " ").replace("-", " ").split() if w not in ("a", "an", "the", "of")]
    key = " ".join(words).strip()
    if key in BUILTIN:
        return key
    for spelled in (key, key.replace(" ", "")):  # 'space ship' and 'spaceship' are the same thing
        if spelled in ALIASES:
            return ALIASES[spelled]
    return next((b for b in BUILTIN if b in key), "") or next((v for k, v in ALIASES.items() if k in words), "")


def parse_stl(data: bytes) -> list[tuple[tuple[float, ...], ...]]:
    """Triangles of a binary or ASCII STL file, as ((x, y, z), (x, y, z), (x, y, z))."""
    if len(data) >= 84 and 84 + 50 * struct.unpack_from("<I", data, 80)[0] == len(data):
        return [(t[3:6], t[6:9], t[9:12]) for t in struct.iter_unpack("<12fH", data[84:])]
    nums = [float(v) for v in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data) for v in v]
    return [tuple(tuple(nums[i + k:i + k + 3]) for k in (0, 3, 6)) for i in range(0, len(nums) - 8, 9)]


def sample(triangles, n: int = POINTS, seed: int = 3) -> list[float]:
    """n points spread over the surface by area, centred, largest side scaled to 2, STL's z-up turned to y-up."""
    def area(t):
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        u, v = (bx - ax, by - ay, bz - az), (cx - ax, cy - ay, cz - az)
        return math.dist((0, 0, 0), (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]))

    good = [t for t in triangles if all(math.isfinite(c) for p in t for c in p)]
    weights = [area(t) for t in good]
    if not good or not sum(weights):
        return []
    r = random.Random(seed)
    points = []
    for (a, b, c) in r.choices(good, weights, k=n):
        s, t = r.random(), r.random()
        if s + t > 1:
            s, t = 1 - s, 1 - t
        points.append([a[k] + s * (b[k] - a[k]) + t * (c[k] - a[k]) for k in range(3)])
    lo = [min(p[k] for p in points) for k in range(3)]
    hi = [max(p[k] for p in points) for k in range(3)]
    span = max(h - l for h, l in zip(hi, lo, strict=True)) or 1.0
    mid = [(h + l) / 2 for h, l in zip(hi, lo, strict=True)]
    return [round(v, 3) for x, y, z in points for v in (2 * (x - mid[0]) / span, 2 * (z - mid[2]) / span,
                                                         -2 * (y - mid[1]) / span)]


def keywords(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", name.lower()) if w not in NOISE and len(w) > 2]


def head_noun(name: str) -> str:
    """'human skull' -> 'skull', 'statue of liberty' -> 'statue'. shortcut: English word order only."""
    words = name.lower().split(" of ")[0].split()
    return next((w for w in reversed(words) if w not in NOISE and len(w) > 2), "")


def relevant(title: str, words: list[str]) -> bool:
    """Every word appears in the file title ('Eiffel' for 'eiffel tower' is not enough)."""
    flat = title.lower()
    return bool(words) and all(w[:5] in flat for w in words)


async def find_model(name: str) -> tuple[list[float], str] | None:
    """A real 3D model of `name` from Wikimedia Commons, sampled into points, with its page for credit."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6", "gsrlimit": "10",
              "gsrsearch": f"{name} filetype:3d", "prop": "imageinfo", "iiprop": "url|size"}
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as http:
        try:
            pages = (await http.get(COMMONS, params=params)).json().get("query", {}).get("pages", {})
            ranked = sorted(pages.values(), key=lambda p: p.get("index", 99))
            whole = keywords(name)
            # A title naming everything asked for first; else one naming the thing itself (a skull for "human skull").
            passes = [whole] + ([[head_noun(name)]] if len(whole) > 1 and head_noun(name) else [])
            for page in [page for words in passes for page in ranked if relevant(page.get("title", ""), words)]:
                info = (page.get("imageinfo") or [{}])[0]
                url = str(info.get("url", ""))
                if not (url.startswith("https://upload.wikimedia.org/")
                        and 0 < info.get("size", 0) <= MAX_MODEL_BYTES):
                    continue
                points = sample(parse_stl((await http.get(url)).content))
                if points:
                    return points, str(info.get("descriptionurl") or page.get("title"))
        except (httpx.HTTPError, ValueError, struct.error):
            return None
    return None


def salvage(raw: str) -> list[Part]:
    """The parts a small model managed to write before it looped or was cut off, without exact repeats."""
    parts, seen, decoder = [], set(), json.JSONDecoder()
    for found in re.finditer(r'\{\s*"kind"', raw):
        try:
            part = Part.model_validate(decoder.raw_decode(raw, found.start())[0])
        except (ValueError, ValidationError):
            continue
        key = (part.kind, *(round(v, 1) for v in part.size))
        if key not in seen:
            seen.add(key)
            parts.append(part)
    return parts


def clean(parts: list[Part]) -> list[dict]:
    """Clamp every number so a bad design cannot throw the swarm off-screen or collapse it to nothing."""
    def clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(v))) if math.isfinite(v) else 0.0

    return [{"kind": p.kind, "at": [clamp(v, -1, 1) for v in p.at], "size": [clamp(v, 0.02, 2) for v in p.size],
             "turn": [clamp(v, -360, 360) for v in p.turn]} for p in parts[:MAX_PARTS]]


class BuildArgs(BaseModel):
    name: str = Field(description="The object to build, e.g. 'guitar', 'rocket', 'coffee cup', 'Eiffel tower'.")
    details: str = Field("", description="Anything the user said about how it should look; else empty.")


class BuildObject:
    name, input_schema, permission = "build_object", BuildArgs, Permission.READ
    description = ("Make any 3D model of any object, animal, vehicle, building or place with the on-screen bots "
                   "(nanobots): 'make a 3d guitar', 'create a 3d model of a dragon', 'show me a car in 3d', "
                   "'can the bots make a violin', 'show me a rocket in 3d', 'import a bottle', 'import tree', "
                   "'show me the solar system'. 'Import' followed by an everyday object always means this, never a "
                   "code import. Never for software: that is create_project. Real 3D scans are used "
                   "when one exists; anything else is designed on the spot.")

    async def run(self, args: BuildArgs, ctx: RunContext) -> SkillResult:
        label = " ".join(args.name.split())[:60]
        if not label:
            return SkillResult("What should the bots build?", {})
        if found := builtin_name(label):
            await ctx.emit("swarm.build", name=found, builtin=found)
            return SkillResult(f"The bots are building a {found}.", {"name": found, "builtin": found}, final=True)
        if model := await find_model(label):
            points, credit = model
            await ctx.emit("swarm.build", name=label, points=points, credit=credit)
            return SkillResult(f"The bots are building a {label} from a real 3D model ({credit}).",
                               {"name": label, "credit": credit}, final=True)
        details = f" ({args.details[:200]})" if args.details.strip() else ""
        raw = await ctx.complete([{"role": "system", "content": DESIGNER.format(name=label, details=details)},
                                  {"role": "user", "content": f"Design: {label}"}], DESIGN_SCHEMA)
        try:
            parts = clean([Part.model_validate(p) for p in json.loads(raw)["parts"]])
        except (ValueError, KeyError, TypeError, ValidationError):
            parts = clean(salvage(raw))
        if len(parts) < 2:
            return SkillResult(f"I couldn't design a {label}. Try a simpler object or describe its shape.", {})
        await ctx.emit("swarm.build", name=label, parts=parts)
        return SkillResult(f"The bots are building a {label} from {len(parts)} parts.",
                           {"name": label, "parts": len(parts)}, final=True)


skills = [BuildObject()]
