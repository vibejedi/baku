"""Personal finance ledger. Sums are done in code, never by the language model."""
from __future__ import annotations

from datetime import date

from . import tool
from .store import connect


@tool(
    "Record money earned or spent.",
    {
        "kind": {"type": "string", "enum": ["income", "expense"]},
        "amount": {"type": "number", "description": "Positive number"},
        "category": {"type": "string", "description": "e.g. food, rent, salary"},
        "note": {"type": "string"},
        "day": {"type": "string", "description": "YYYY-MM-DD; defaults to today"},
    },
    ["kind", "amount", "category"],
)
def add_transaction(kind: str, amount: float, category: str, note: str = "", day: str | None = None) -> dict:
    amount = round(float(amount), 2)
    if kind not in ("income", "expense"):
        raise ValueError("kind must be 'income' or 'expense'")
    if amount <= 0:
        raise ValueError("amount must be positive")
    day = date.fromisoformat(day).isoformat() if day else date.today().isoformat()
    category = category.strip().lower()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO transactions (day, kind, amount, category, note) VALUES (?, ?, ?, ?, ?)",
            (day, kind, amount, category, note),
        )
    return {"id": cur.lastrowid, "day": day, "kind": kind, "amount": amount, "category": category}


@tool(
    "Totals for one month: income, expenses, net, and expenses by category.",
    {"month": {"type": "string", "description": "YYYY-MM; defaults to the current month"}},
)
def monthly_summary(month: str | None = None) -> dict:
    month = month or date.today().strftime("%Y-%m")
    date.fromisoformat(month + "-01")  # validates the format
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, category, SUM(amount) AS total FROM transactions "
            "WHERE substr(day, 1, 7) = ? GROUP BY kind, category ORDER BY total DESC",
            (month,),
        ).fetchall()
    income = round(sum(r["total"] for r in rows if r["kind"] == "income"), 2)
    expenses = round(sum(r["total"] for r in rows if r["kind"] == "expense"), 2)
    return {
        "month": month,
        "income": income,
        "expenses": expenses,
        "net": round(income - expenses, 2),
        "expenses_by_category": {
            r["category"]: round(r["total"], 2) for r in rows if r["kind"] == "expense"
        },
    }
