"""The conversation loop: ask the model, run the tools it requests, repeat."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from .tools import Tool, load_all

PERSONA = """You are Baku, a small, friendly sea serpent (short for Bakunawa) who lives in \
this computer and helps its owner. You run entirely on this device.

You help with three things: tidying files and folders, tracking money and reminders, and \
checking the PC's health.

Rules:
- Use tools for facts about this computer, the ledger and reminders. Never guess them.
- Never do arithmetic on money yourself; use monthly_summary.
- Before moving files, make a plan with plan_organize and show the preview.
- You cannot delete files. If something should be removed, tell the owner where it is.
- For viruses, rely on Microsoft Defender tools; do not claim a file is safe or infected yourself.
- If a tool fails, say so plainly.
- Keep replies short and warm. A little sea-serpent charm is welcome; do not overdo it.
"""

MAX_STEPS = 8


class Agent:
    def __init__(self, llm, confirm: Callable[[Tool, dict], bool], tools: dict[str, Tool] | None = None):
        self.llm = llm
        self.confirm = confirm
        self.tools = tools if tools is not None else load_all()
        self.messages: list[dict] = []

    def _system(self) -> dict:
        now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
        return {"role": "system", "content": f"{PERSONA}\nCurrent local time: {now}"}

    def _run_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"No such tool: {name}"})
        if tool.confirm and not self.confirm(tool, args):
            return json.dumps({"error": "The owner declined this action."})
        try:
            return json.dumps(tool.func(**args), default=str)
        except Exception as e:  # reported back to the model so it can explain
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    def ask(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        schemas = [t.schema() for t in self.tools.values()]
        for _ in range(MAX_STEPS):
            reply = self.llm.chat([self._system(), *self.messages], schemas)
            self.messages.append(reply)
            calls = reply.get("tool_calls") or []
            if not calls:
                return reply.get("content", "")
            for call in calls:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                self.messages.append(
                    {"role": "tool", "tool_name": fn["name"], "content": self._run_tool(fn["name"], args)}
                )
        return "I got tangled up trying to do that. Could you ask me in a simpler way?"
