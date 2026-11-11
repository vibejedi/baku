"""Tool registry. Each tool is a plain function plus a JSON schema the model sees.

Tools marked ``confirm=True`` change the user's machine and are never run
without an explicit yes from the user.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    confirm: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(description: str, properties: dict | None = None, required: list[str] | None = None,
         confirm: bool = False):
    def wrap(func):
        REGISTRY[func.__name__] = Tool(
            name=func.__name__,
            description=description,
            parameters={
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            func=func,
            confirm=confirm,
        )
        return func
    return wrap


def load_all() -> dict[str, Tool]:
    from . import diagnostics, files, finance, reminders  # noqa: F401  (registers tools)
    return REGISTRY
