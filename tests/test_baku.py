import json
from datetime import datetime, timedelta

import pytest

from baku import config
from baku.agent import Agent
from baku.tools import diagnostics, files, finance, load_all, reminders


@pytest.fixture(autouse=True)
def baku_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKU_HOME", str(tmp_path / "home"))
    for var in ("BAKU_TIER", "BAKU_MODEL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def desktop(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    for name in ("report.pdf", "photo.JPG", "budget.xlsx", "setup.exe", "mystery.xyz",
                 "Chrome.lnk", "desktop.ini"):
        (d / name).write_text(name)
    (d / "Existing").mkdir()
    return d


# --- config -----------------------------------------------------------------

def test_tier_follows_vram():
    assert config.pick_tier(vram_mb=8192).name == "gpu"
    assert config.pick_tier(vram_mb=8192).image_gen
    assert config.pick_tier(vram_mb=0).name == "cpu"
    assert not config.pick_tier(vram_mb=0).image_gen


def test_tier_and_model_overrides(monkeypatch):
    monkeypatch.setenv("BAKU_TIER", "cpu")
    monkeypatch.setenv("BAKU_MODEL", "custom:1b")
    tier = config.pick_tier(vram_mb=8192)
    assert (tier.name, tier.model) == ("cpu", "custom:1b")


# --- files ------------------------------------------------------------------

def test_scan_ignores_shortcuts_and_system_files(desktop):
    result = files.scan_folder(str(desktop))
    assert result["file_count"] == 5
    assert result["by_category"] == {
        "Documents": 1, "Images": 1, "Spreadsheets": 1, "Installers": 1, "Other": 1,
    }


def test_plan_moves_nothing(desktop):
    before = sorted(p.name for p in desktop.iterdir())
    plan = files.plan_organize(str(desktop))
    assert plan["move_count"] == 5
    assert sorted(p.name for p in desktop.iterdir()) == before


def test_apply_then_undo_roundtrip(desktop):
    before = sorted(p.name for p in desktop.iterdir())
    plan = files.plan_organize(str(desktop))
    assert files.apply_organize_plan(plan["plan_id"])["moved"] == 5
    assert (desktop / "Documents" / "report.pdf").read_text() == "report.pdf"
    assert (desktop / "Images" / "photo.JPG").exists()
    assert (desktop / "Chrome.lnk").exists()  # shortcut left alone
    assert files.undo_last_organize()["restored"] == 5
    assert sorted(p.name for p in desktop.iterdir()) == before


def test_apply_never_overwrites(desktop):
    (desktop / "Documents").mkdir()
    (desktop / "Documents" / "report.pdf").write_text("older file")
    plan = files.plan_organize(str(desktop))
    files.apply_organize_plan(plan["plan_id"])
    assert (desktop / "Documents" / "report.pdf").read_text() == "older file"
    assert (desktop / "Documents" / "report (2).pdf").read_text() == "report.pdf"


def test_unknown_plan_and_bad_folder(tmp_path):
    with pytest.raises(ValueError):
        files.apply_organize_plan("plan-nope")
    with pytest.raises(ValueError):
        files.scan_folder(str(tmp_path / "missing"))


# --- reminders --------------------------------------------------------------

def test_reminder_lifecycle():
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
    a = reminders.add_reminder("Pay rent", past)
    reminders.add_reminder("Dentist", future)
    listed = reminders.list_reminders()
    assert [r["title"] for r in listed] == ["Pay rent", "Dentist"]
    assert [r["overdue"] for r in listed] == [True, False]
    assert [r["title"] for r in reminders.due_now()] == ["Pay rent"]
    assert reminders.complete_reminder(a["id"])["completed"]
    assert not reminders.complete_reminder(a["id"])["completed"]
    assert [r["title"] for r in reminders.list_reminders()] == ["Dentist"]


def test_reminder_rejects_bad_date():
    with pytest.raises(ValueError):
        reminders.add_reminder("x", "next tuesday")


# --- finance ----------------------------------------------------------------

def test_monthly_summary():
    finance.add_transaction("income", 30000, "Salary", day="2026-11-05")
    finance.add_transaction("expense", 1200.50, "Food", day="2026-11-06")
    finance.add_transaction("expense", 300.25, "food", day="2026-11-07")
    finance.add_transaction("expense", 8000, "rent", day="2026-11-01")
    finance.add_transaction("expense", 999, "food", day="2026-10-31")  # other month
    s = finance.monthly_summary("2026-11")
    assert s["income"] == 30000
    assert s["expenses"] == 9500.75
    assert s["net"] == 20499.25
    assert s["expenses_by_category"] == {"rent": 8000, "food": 1500.75}


def test_transaction_validation():
    with pytest.raises(ValueError):
        finance.add_transaction("expense", -5, "food")
    with pytest.raises(ValueError):
        finance.add_transaction("gift", 5, "food")


# --- diagnostics ------------------------------------------------------------

def test_system_health_shape():
    h = diagnostics.system_health()
    assert h["ram_total_gb"] > 0 and h["disks"] and len(h["top_by_ram"]) > 0


def test_find_large_files(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"0" * 3 * 1024 * 1024)
    (tmp_path / "small.txt").write_text("hi")
    found = diagnostics.find_large_files(str(tmp_path), min_mb=1)
    assert [f["path"].endswith("big.bin") for f in found] == [True]


# --- agent ------------------------------------------------------------------

class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append(messages)
        return self.replies.pop(0)


def call(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_agent_runs_tool_and_returns_answer(desktop):
    llm = FakeLLM([call("scan_folder", path=str(desktop)), {"role": "assistant", "content": "5 files!"}])
    agent = Agent(llm, confirm=lambda t, a: False)
    assert agent.ask("what's on my desktop?") == "5 files!"
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["file_count"] == 5


def test_agent_blocks_unconfirmed_changes(desktop):
    plan = files.plan_organize(str(desktop))
    llm = FakeLLM([call("apply_organize_plan", plan_id=plan["plan_id"]),
                   {"role": "assistant", "content": "ok, left as is"}])
    agent = Agent(llm, confirm=lambda t, a: False)
    agent.ask("tidy up")
    assert "declined" in llm.seen[1][-1]["content"]
    assert (desktop / "report.pdf").exists()


def test_agent_survives_bad_tool_calls():
    llm = FakeLLM([call("no_such_tool"), call("add_reminder", title="x", due="garbage"),
                   {"role": "assistant", "content": "sorry"}])
    assert Agent(llm, confirm=lambda t, a: True).ask("hi") == "sorry"
    assert "error" in llm.seen[2][-1]["content"]


def test_every_state_changing_tool_requires_confirmation():
    needs = {n for n, t in load_all().items() if t.confirm}
    assert needs == {"apply_organize_plan", "undo_last_organize", "start_defender_quick_scan",
                     "end_process", "clean_temp_files"}


def test_end_process_closes_only_the_named_program():
    import subprocess, sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        import psutil
        name = psutil.Process(child.pid).name()
        with pytest.raises(ValueError):
            diagnostics.end_process(child.pid, "something-else.exe")
        assert child.poll() is None
        assert diagnostics.end_process(child.pid, name)["ended"]
        assert child.wait(timeout=5) is not None
        assert not diagnostics.end_process(child.pid, name)["ended"]
    finally:
        child.kill()


def test_end_process_refuses_baku_itself():
    import os, psutil
    with pytest.raises(ValueError):
        diagnostics.end_process(os.getpid(), psutil.Process().name())


def test_clean_folder_only_removes_old_files(tmp_path):
    import os, time
    old, new = tmp_path / "old.tmp", tmp_path / "sub" / "new.tmp"
    new.parent.mkdir()
    old.write_bytes(b"0" * 2 * 1024 * 1024)
    new.write_text("fresh")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    result = diagnostics.clean_folder(str(tmp_path), older_than_days=7)
    assert (result["deleted_files"], result["freed_mb"]) == (1, 2)
    assert not old.exists() and new.exists()


def test_recommendations_are_read_only_and_point_at_real_tools():
    tools = load_all()
    for rec in diagnostics.health_recommendations():
        assert rec["problem"] and rec["recommendation"]
        assert rec["fix_tool"] is None or tools[rec["fix_tool"]].confirm


def test_new_health_tools():
    assert "has_battery" in diagnostics.battery_status()
    net = diagnostics.network_usage(seconds=0.2)
    assert net["sample_seconds"] == 0.2 and isinstance(net["adapters"], list)
    assert "has_nvidia_gpu" in diagnostics.gpu_status()


def test_parse_gpu_csv_handles_unsupported_readings():
    text = "NVIDIA GeForce RTX 5060 Laptop GPU, 61, 34, 48.20, [N/A], 3120, 8151\n"
    assert diagnostics.parse_gpu_csv(text) == [{
        "name": "NVIDIA GeForce RTX 5060 Laptop GPU", "temperature_c": 61.0, "load_percent": 34.0,
        "power_watts": 48.2, "fan_percent": None, "vram_used_mb": 3120.0, "vram_total_mb": 8151.0,
    }]
