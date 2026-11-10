"""Skill interface and registry.

To add a skill: create app/skills/<name>.py (or a package) exposing a module-level
`skill` object that satisfies `Skill`. The agent core never changes.
"""

import importlib
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel


class Permission(StrEnum):
    READ = "read"  # runs freely
    CHANGE = "change"  # needs the user to say "apply"
    CRITICAL = "critical"  # send, pay, submit, deploy, mass delete: needs an explicit "yes"


@dataclass
class SkillResult:
    summary: str  # short text handed back to the LLM and spoken
    data: dict[str, Any] = field(default_factory=dict)  # shown in the client


@dataclass
class RunContext:
    user_id: str
    session_id: str
    user_text: str  # what the user said this turn
    upload_ids: list[str]  # files attached this turn
    emit: Callable[..., Awaitable[None]]  # emit("event.type", key=value) to the client
    complete: Callable[..., Awaitable[str]]  # complete(messages, schema=None) -> LLM text


class Skill(Protocol):
    name: str
    description: str
    input_schema: type[BaseModel]
    permission: Permission

    async def run(self, args: Any, ctx: RunContext) -> SkillResult: ...


@lru_cache
def discover() -> dict[str, Skill]:
    found: dict[str, Skill] = {}
    for module in pkgutil.iter_modules(__path__):
        skill = getattr(importlib.import_module(f"{__name__}.{module.name}"), "skill", None)
        if skill is not None:
            found[skill.name] = skill
    return found


def as_tool(skill: Skill) -> dict:
    return {
        "type": "function",
        "function": {
            "name": skill.name,
            "description": skill.description,
            "parameters": skill.input_schema.model_json_schema(),
        },
    }
