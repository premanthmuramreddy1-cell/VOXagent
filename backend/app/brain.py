"""Self-checking answers against the evidence the tools found, and learning the user's preferences as they talk."""

import json
import re
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select

from app.db import Memory, SessionLocal

Complete = Callable[..., Awaitable[str]]  # complete(messages, schema=None) -> text

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}},
                   "fixed": {"type": "string"}},
    "required": ["ok", "problems", "fixed"],
}
CHECKER = ("You fact-check an assistant's answer against the evidence its tools returned. Flag only claims that the "
           "evidence contradicts, or numbers, names and dates the evidence does not support. Style is not a problem. "
           "If the answer is fine, return ok=true, no problems and an empty fixed. Otherwise return ok=false, the "
           "problems, and in fixed the whole corrected answer, same language, tone, length and [n] citations.")


async def check(complete: Complete, question: str, answer: str, evidence: str) -> str:
    """The answer, corrected where the evidence disagrees with it. Any checker failure keeps the answer as it is."""
    if not answer.strip() or not evidence.strip():
        return answer
    messages = [{"role": "system", "content": CHECKER},
                {"role": "user", "content": f"Question: {question}\n\n<evidence>\n{evidence[:24_000]}\n</evidence>"
                                            f"\n\n<answer>\n{answer}\n</answer>"}]
    try:
        verdict = json.loads(await complete(messages, CHECK_SCHEMA))
        fixed = str(verdict.get("fixed") or "").strip()
        return fixed if verdict.get("ok") is False and fixed else answer
    except Exception:  # a broken check must never cost the user the answer itself
        return answer


# Only messages that sound like a lasting preference reach the model, so ordinary turns cost nothing extra.
PREFERENCE_CUE = re.compile(
    r"\b(i (really )?(prefer|like|love|hate|dislike|don'?t like|always|never|usually|want you to)|call me|"
    r"my (name|favou?rite|birthday)|i'?m (a |an )?(vegetarian|vegan|student|developer|doctor|teacher)|"
    r"i (live|work) (in|at)|(in|use) (celsius|fahrenheit|metric|imperial|km|miles|kg|pounds)|"
    r"(be|keep it|answer) (short|shorter|brief|detailed|formal|casual|simple)|from now on|remember that)\b", re.I)
LEARN_SCHEMA = {"type": "object", "properties": {"preferences": {"type": "array", "items": {"type": "string"}}},
                "required": ["preferences"]}
LEARNER = ("Extract lasting facts and preferences about the user from their message, each as one complete "
           "third-person line that keeps their exact specifics. Examples: 'Call me Captain' -> 'wants to be called "
           "Captain'; 'always answer in one short sentence' -> 'wants every answer in one short sentence'; 'I use "
           "Celsius' -> 'uses Celsius'; 'I'm vegetarian' -> 'is vegetarian'. Cover name, answer length and tone, units, "
           "favourites, diet, job, city, language. Skip one-off requests and anything about other people. Never "
           "include passwords, card or ID numbers. Return an empty list when there is nothing lasting.")
SECRET = re.compile(r"\d{6,}|password|passcode|pin\b|otp|cvv", re.I)


async def learn(complete: Complete, user_id: str, text: str) -> list[str]:
    """Save the lasting preferences in one user message as memories; returns the new ones."""
    if not PREFERENCE_CUE.search(text):
        return []
    try:
        found = json.loads(await complete([{"role": "system", "content": LEARNER},
                                           {"role": "user", "content": text[:2000]}], LEARN_SCHEMA))["preferences"]
    except Exception:  # learning is best effort, after the answer was given
        return []
    saved = []
    with SessionLocal() as db:
        lines = [line for line in (str(p).strip()[:200] for p in found[:5]) if len(line.split()) >= 2]  # a bare "Captain" is not usable
        if found and not lines:  # only fragments ("concise", "dosa"): their own words are the faithful record
            lines = [f'in their words: "{" ".join(text.split())[:200]}"']
        for line in lines:
            if SECRET.search(line):
                continue
            seen = db.scalar(select(Memory).where(Memory.user_id == user_id,
                                                  func.lower(Memory.text) == line.lower()))
            if seen is None:
                db.add(Memory(user_id=user_id, kind="preference", text=line))
                saved.append(line)
        db.commit()
    return saved


def preferences(user_id: str, limit: int = 15) -> list[str]:
    """The user's own standing preferences (name, length, tone, units), newest first."""
    with SessionLocal() as db:
        return list(db.scalars(select(Memory.text).where(Memory.user_id == user_id,
                                                         Memory.kind.in_(("preference", "style")))
                               .order_by(Memory.updated_at.desc()).limit(limit)))
