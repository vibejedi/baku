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
    procs = list(psutil.process_iter(["name", "memory_info"]))
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
