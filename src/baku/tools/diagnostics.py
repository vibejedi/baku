"""PC health checks. Baku reports and recommends; virus detection is delegated
to Microsoft Defender rather than reimplemented here.
"""
from __future__ import annotations

import heapq
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

from . import tool

GB = 1024**3
MB = 1024**2


@tool("Snapshot of CPU, memory, disk space, uptime and the heaviest running programs. Read-only.")
def system_health() -> dict:
    psutil.cpu_percent(interval=None)
    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(interval=None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    rows = []
    for p in procs:
        try:
            rows.append({
                "pid": p.info["pid"],
                "name": p.info["name"],
                "ram_mb": round(p.info["memory_info"].rss / MB),
                "cpu_percent": round(p.cpu_percent(interval=None) / psutil.cpu_count(), 1),
            })
        except (psutil.Error, AttributeError):
            continue
    mem = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        if u.total < GB:  # skip empty card readers and tiny system mounts
            continue
        disks.append({
            "drive": part.mountpoint,
            "free_gb": round(u.free / GB, 1),
            "total_gb": round(u.total / GB, 1),
            "percent_used": u.percent,
        })
    warnings = []
    if mem.percent >= 85:
        warnings.append("Memory is nearly full.")
    warnings += [f"{d['drive']} is {d['percent_used']}% full." for d in disks if d["percent_used"] >= 90]
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_percent": mem.percent,
        "ram_total_gb": round(mem.total / GB, 1),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "disks": disks,
        "top_by_ram": heapq.nlargest(5, rows, key=lambda r: r["ram_mb"]),
        "top_by_cpu": heapq.nlargest(5, rows, key=lambda r: r["cpu_percent"]),
        "warnings": warnings,
    }


@tool(
    "Find the biggest files under a folder. Read-only; use it to suggest what could be cleaned up.",
    {
        "path": {"type": "string", "description": "Folder to search, e.g. ~/Downloads"},
        "min_mb": {"type": "number", "description": "Ignore files smaller than this (default 100)"},
    },
    ["path"],
)
def find_large_files(path: str, min_mb: float = 100, limit: int = 15) -> list[dict]:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"Not a folder: {root}")
    found = []
    for dirpath, _dirs, names in os.walk(root, onerror=lambda e: None):
        for name in names:
            full = os.path.join(dirpath, name)
            try:
                size = os.lstat(full).st_size
            except OSError:
                continue
            if size >= min_mb * MB:
                found.append((size, full))
    return [{"path": p, "size_mb": round(s / MB)} for s, p in heapq.nlargest(int(limit), found)]


@tool("Size of the temporary-files folder, a common source of reclaimable space. Read-only.")
def temp_files_size() -> dict:
    root = tempfile.gettempdir()
    total = count = 0
    for dirpath, _dirs, names in os.walk(root, onerror=lambda e: None):
        for name in names:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
                count += 1
            except OSError:
                continue
    return {"folder": root, "files": count, "size_mb": round(total / MB)}


def _powershell(command: str, timeout: int = 60) -> str:
    if sys.platform != "win32":
        raise RuntimeError("This check only works on Windows.")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "PowerShell command failed")
    return result.stdout


@tool("Programs set to launch at Windows startup, which can slow boot. Read-only.")
def list_startup_programs() -> list[dict]:
    out = _powershell(
        "Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location | ConvertTo-Json"
    )
    data = json.loads(out) if out.strip() else []
    return data if isinstance(data, list) else [data]


@tool("Microsoft Defender status: real-time protection, definition age, last scan. Read-only.")
def defender_status() -> dict:
    out = _powershell(
        "Get-MpComputerStatus | Select-Object AntivirusEnabled, RealTimeProtectionEnabled, "
        "AntivirusSignatureAge, QuickScanAge, FullScanAge | ConvertTo-Json"
    )
    return json.loads(out)


@tool("Threats Microsoft Defender has detected on this PC. Read-only.")
def defender_threats() -> list[dict]:
    out = _powershell(
        "Get-MpThreatDetection | Select-Object ThreatID, InitialDetectionTime, Resources, "
        "ThreatStatusID | ConvertTo-Json"
    )
    data = json.loads(out) if out.strip() else []
    return data if isinstance(data, list) else [data]


@tool("Start a Microsoft Defender quick scan. Takes a few minutes and uses CPU.", confirm=True)
def start_defender_quick_scan() -> dict:
    _powershell("Start-MpScan -ScanType QuickScan", timeout=1800)
    return {"scan": "quick", "status": "finished", "next": "Call defender_threats to see results."}


@tool("Battery charge, whether it is plugged in, and estimated time left. Read-only.")
def battery_status() -> dict:
    b = psutil.sensors_battery()
    if b is None:
        return {"has_battery": False}
    known = b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN)
    return {
        "has_battery": True,
        "percent": round(b.percent),
        "plugged_in": bool(b.power_plugged),
        "minutes_left": round(b.secsleft / 60) if known and not b.power_plugged else None,
    }


@tool(
    "Current network speed per adapter, measured over a short sample, plus totals since boot. Read-only.",
    {"seconds": {"type": "number", "description": "Sample length, default 2"}},
)
def network_usage(seconds: float = 2) -> dict:
    seconds = min(max(float(seconds), 0.2), 10)
    before = psutil.net_io_counters(pernic=True)
    time.sleep(seconds)
    after = psutil.net_io_counters(pernic=True)
    adapters = []
    for name, now in after.items():
        was = before.get(name)
        if was is None or (now.bytes_sent == 0 and now.bytes_recv == 0):
            continue
        adapters.append({
            "adapter": name,
            "download_kbps": round((now.bytes_recv - was.bytes_recv) * 8 / 1000 / seconds, 1),
            "upload_kbps": round((now.bytes_sent - was.bytes_sent) * 8 / 1000 / seconds, 1),
            "downloaded_gb_since_boot": round(now.bytes_recv / GB, 2),
            "uploaded_gb_since_boot": round(now.bytes_sent / GB, 2),
        })
    adapters.sort(key=lambda a: a["download_kbps"] + a["upload_kbps"], reverse=True)
    return {"sample_seconds": seconds, "adapters": adapters}


GPU_FIELDS = ["name", "temperature.gpu", "utilization.gpu", "power.draw", "fan.speed",
              "memory.used", "memory.total"]


def parse_gpu_csv(text: str) -> list[dict]:
    """Parse `nvidia-smi --format=csv,noheader,nounits`; unsupported readings become None."""
    def num(value: str):
        try:
            return float(value)
        except ValueError:
            return None  # laptops often report "[N/A]" for fan speed and power
    gpus = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(GPU_FIELDS):
            continue
        gpus.append({
            "name": parts[0],
            "temperature_c": num(parts[1]),
            "load_percent": num(parts[2]),
            "power_watts": num(parts[3]),
            "fan_percent": num(parts[4]),
            "vram_used_mb": num(parts[5]),
            "vram_total_mb": num(parts[6]),
        })
    return gpus


@tool("NVIDIA graphics card temperature, load, power draw, fan and memory. Read-only.")
def gpu_status() -> dict:
    import shutil
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"has_nvidia_gpu": False}
    out = subprocess.run(
        [exe, f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "nvidia-smi failed")
    return {"has_nvidia_gpu": True, "gpus": parse_gpu_csv(out.stdout)}


# --- Fixes: each one changes the machine, so each needs the owner's yes ----------

PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "memory compression", "secure system",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "msmpeng.exe",
    "init", "systemd", "launchd",
}


@tool(
    "Close one running program to free memory or CPU. Unsaved work in it is lost. "
    "Use the pid and name exactly as system_health reported them.",
    {"pid": {"type": "integer"}, "name": {"type": "string"}},
    ["pid", "name"],
    confirm=True,
)
def end_process(pid: int, name: str) -> dict:
    pid = int(pid)
    try:
        proc = psutil.Process(pid)
        actual = proc.name()
    except psutil.NoSuchProcess:
        return {"ended": False, "reason": "That program is no longer running."}
    if actual.lower() != name.lower():
        raise ValueError(f"Process {pid} is {actual!r}, not {name!r}; run system_health again.")
    me = psutil.Process()
    if actual.lower() in PROTECTED_PROCESSES or pid in {me.pid, *(p.pid for p in me.parents())}:
        raise ValueError(f"{actual} is needed by Windows or by Baku and will not be closed.")
    freed = proc.memory_info().rss
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except psutil.TimeoutExpired:
        return {"ended": False, "reason": f"{actual} did not close when asked."}
    return {"ended": True, "name": actual, "freed_mb": round(freed / MB)}


def clean_folder(root: str, older_than_days: float) -> dict:
    """Delete files under root not modified for older_than_days. Files in use are skipped."""
    cutoff = time.time() - older_than_days * 86400
    deleted = freed = skipped = 0
    for dirpath, _dirs, names in os.walk(root, onerror=lambda e: None):
        for name in names:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
                if st.st_mtime >= cutoff:
                    continue
                os.remove(full)
                deleted += 1
                freed += st.st_size
            except OSError:
                skipped += 1
    return {"folder": root, "deleted_files": deleted, "freed_mb": round(freed / MB),
            "skipped_in_use": skipped}


@tool(
    "Permanently delete temporary files that have not been touched for a while. "
    "Only the system temp folder is cleaned, never personal files.",
    {"older_than_days": {"type": "number", "description": "Default 7, minimum 1"}},
    confirm=True,
)
def clean_temp_files(older_than_days: float = 7) -> dict:
    return clean_folder(tempfile.gettempdir(), max(float(older_than_days), 1))


@tool(
    "Run the health checks and list problems found, each with a recommended fix. "
    "Read-only: it changes nothing. Offer the fixes to the owner and act only if they agree."
)
def health_recommendations() -> list[dict]:
    health = system_health()
    found = []
    if health["ram_used_percent"] >= 85:
        top = health["top_by_ram"][0]
        found.append({
            "problem": f"Memory is {health['ram_used_percent']}% full.",
            "recommendation": f"Close {top['name']} ({top['ram_mb']} MB) if it is not needed.",
            "fix_tool": "end_process", "fix_args": {"pid": top["pid"], "name": top["name"]},
        })
    for p in health["top_by_cpu"]:
        if p["cpu_percent"] >= 50:
            found.append({
                "problem": f"{p['name']} is using {p['cpu_percent']}% of the processor.",
                "recommendation": "Close it if it is stuck or not needed.",
                "fix_tool": "end_process", "fix_args": {"pid": p["pid"], "name": p["name"]},
            })
    temp = temp_files_size()
    if temp["size_mb"] >= 1024:
        found.append({
            "problem": f"Temporary files take up {temp['size_mb']} MB.",
            "recommendation": "Delete temporary files older than a week.",
            "fix_tool": "clean_temp_files", "fix_args": {"older_than_days": 7},
        })
    for d in health["disks"]:
        if d["percent_used"] >= 90:
            found.append({
                "problem": f"{d['drive']} is {d['percent_used']}% full ({d['free_gb']} GB free).",
                "recommendation": "Look for large files to move or remove; use find_large_files.",
                "fix_tool": None,
            })
    if health["uptime_hours"] >= 24 * 7:
        found.append({
            "problem": f"The PC has been on for {round(health['uptime_hours'] / 24)} days.",
            "recommendation": "Restart it; the owner does this themselves.",
            "fix_tool": None,
        })
    battery = battery_status()
    if battery.get("has_battery") and not battery["plugged_in"] and battery["percent"] <= 15:
        found.append({
            "problem": f"Battery is at {battery['percent']}%.",
            "recommendation": "Plug in the charger.", "fix_tool": None,
        })
    return found
