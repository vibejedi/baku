"""Reminders and simple calendar events."""
from __future__ import annotations

from datetime import datetime

from . import tool
from .store import connect


def _parse(when: str) -> datetime:
    try:
        return datetime.fromisoformat(when)
    except ValueError as e:
        raise ValueError(f"Use ISO format like 2026-12-01T09:00, got {when!r}") from e


@tool(
    "Save a reminder or event.",
    {
        "title": {"type": "string"},
        "due": {"type": "string", "description": "Local date-time, ISO format: 2026-12-01T09:00"},
    },
    ["title", "due"],
)
def add_reminder(title: str, due: str) -> dict:
    when = _parse(due).isoformat(timespec="minutes")
    with connect() as conn:
        cur = conn.execute("INSERT INTO reminders (title, due) VALUES (?, ?)", (title.strip(), when))
        return {"id": cur.lastrowid, "title": title.strip(), "due": when}


@tool(
    "List reminders that are not done yet, soonest first. Overdue ones are flagged.",
    {"until": {"type": "string", "description": "Optional ISO date-time upper bound"}},
)
def list_reminders(until: str | None = None) -> list[dict]:
    query, args = "SELECT id, title, due FROM reminders WHERE done = 0", []
    if until:
        query += " AND due <= ?"
        args.append(_parse(until).isoformat(timespec="minutes"))
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as conn:
        rows = conn.execute(query + " ORDER BY due", args).fetchall()
    return [{**dict(r), "overdue": r["due"] < now} for r in rows]


@tool("Mark a reminder as done.", {"id": {"type": "integer"}}, ["id"])
def complete_reminder(id: int) -> dict:
    with connect() as conn:
        cur = conn.execute("UPDATE reminders SET done = 1 WHERE id = ? AND done = 0", (int(id),))
    return {"id": int(id), "completed": cur.rowcount == 1}


def due_now() -> list[dict]:
    """Reminders that are due or overdue; shown when Baku starts."""
    return list_reminders(until=datetime.now().isoformat(timespec="minutes"))
