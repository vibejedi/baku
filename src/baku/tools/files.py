"""Tidy a folder (Desktop, Downloads, ...) by sorting loose files into category folders.

Safety model: planning never touches anything; applying only moves (never
deletes or overwrites) and writes a journal so the last run can be undone.
"""
from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from pathlib import Path

from ..config import data_dir
from . import tool

CATEGORIES = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".ppt", ".pptx"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi", ".msix"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs", ".ps1"},
}
# Left where they are: shortcuts and system files that belong on the Desktop.
SKIP_SUFFIXES = {".lnk", ".url", ".ini", ".sys", ".tmp"}


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "Other"


def _loose_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() not in SKIP_SUFFIXES
    )


def _resolve_folder(path: str) -> Path:
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise ValueError(f"Not a folder: {folder}")
    return folder.resolve()


def _plans_dir() -> Path:
    d = data_dir() / "plans"
    d.mkdir(exist_ok=True)
    return d


def _free_name(target: Path) -> Path:
    """Avoid overwriting: report.pdf -> report (2).pdf."""
    n = 2
    candidate = target
    while candidate.exists():
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    return candidate


@tool(
    "Look at the loose files in a folder and count them by category. Read-only.",
    {"path": {"type": "string", "description": "Folder path, e.g. ~/Desktop"}},
    ["path"],
)
def scan_folder(path: str) -> dict:
    folder = _resolve_folder(path)
    files = _loose_files(folder)
    counts = Counter(category_for(f) for f in files)
    return {
        "folder": str(folder),
        "file_count": len(files),
        "total_mb": round(sum(f.stat().st_size for f in files) / 1024**2, 1),
        "by_category": dict(counts),
    }


@tool(
    "Draft a plan to sort a folder's loose files into category subfolders. "
    "Nothing is moved; returns a plan_id to pass to apply_organize_plan.",
    {"path": {"type": "string", "description": "Folder path, e.g. ~/Desktop"}},
    ["path"],
)
def plan_organize(path: str) -> dict:
    folder = _resolve_folder(path)
    moves = [
        {"from": str(f), "to": str(folder / category_for(f) / f.name)}
        for f in _loose_files(folder)
    ]
    plan_id = f"plan-{int(time.time() * 1000)}"
    (_plans_dir() / f"{plan_id}.json").write_text(json.dumps({"folder": str(folder), "moves": moves}))
    preview = [f"{Path(m['from']).name} -> {Path(m['to']).parent.name}/" for m in moves[:15]]
    return {"plan_id": plan_id, "move_count": len(moves), "preview": preview}


@tool(
    "Carry out a plan made by plan_organize. Moves files; can be reversed with undo_last_organize.",
    {"plan_id": {"type": "string"}},
    ["plan_id"],
    confirm=True,
)
def apply_organize_plan(plan_id: str) -> dict:
    plan_file = _plans_dir() / f"{Path(plan_id).name}.json"
    if not plan_file.exists():
        raise ValueError(f"Unknown plan: {plan_id}")
    plan = json.loads(plan_file.read_text())
    done, skipped = [], []
    for move in plan["moves"]:
        src, dst = Path(move["from"]), Path(move["to"])
        if not src.is_file():
            skipped.append(src.name)
            continue
        dst.parent.mkdir(exist_ok=True)
        dst = _free_name(dst)
        shutil.move(str(src), str(dst))
        done.append({"from": str(src), "to": str(dst)})
    (data_dir() / "last_organize.json").write_text(json.dumps(done))
    plan_file.unlink()
    return {"moved": len(done), "skipped_missing": skipped}


@tool("Undo the most recent apply_organize_plan, putting files back where they were.", confirm=True)
def undo_last_organize() -> dict:
    journal = data_dir() / "last_organize.json"
    if not journal.exists():
        return {"restored": 0, "note": "Nothing to undo."}
    restored, failed = 0, []
    for move in reversed(json.loads(journal.read_text())):
        src, dst = Path(move["to"]), Path(move["from"])
        if not src.is_file() or dst.exists():
            failed.append(src.name)
            continue
        shutil.move(str(src), str(dst))
        restored += 1
        try:
            src.parent.rmdir()  # only succeeds if the category folder is now empty
        except OSError:
            pass
    journal.unlink()
    return {"restored": restored, "could_not_restore": failed}
