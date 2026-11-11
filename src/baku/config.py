"""Paths, hardware tiers and model choices.

Everything here can be overridden with environment variables so the same build
runs on both test devices without code changes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil


def data_dir() -> Path:
    """Where Baku keeps its database, plans and undo journals."""
    override = os.environ.get("BAKU_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Baku"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "baku"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass(frozen=True)
class Tier:
    name: str
    model: str
    context_tokens: int
    image_gen: bool


# Default models are starting points to benchmark on the real devices, not
# measured picks. Both need tool-calling support in Ollama.
TIERS = {
    # e.g. Naga Lakan II: discrete GPU with ~8 GB VRAM
    "gpu": Tier("gpu", "qwen3:8b", 8192, image_gen=True),
    # e.g. Naga Sipag I: integrated graphics, CPU inference
    "cpu": Tier("cpu", "qwen3:4b", 4096, image_gen=False),
}

MIN_GPU_VRAM_MB = 6000


def gpu_vram_mb() -> int:
    """Largest NVIDIA GPU's VRAM in MB, or 0 when none is found."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return max((int(line) for line in out.split() if line.isdigit()), default=0)
    except (OSError, subprocess.SubprocessError):
        return 0


def pick_tier(vram_mb: int | None = None) -> Tier:
    forced = os.environ.get("BAKU_TIER")
    if forced in TIERS:
        tier = TIERS[forced]
    else:
        vram = gpu_vram_mb() if vram_mb is None else vram_mb
        tier = TIERS["gpu"] if vram >= MIN_GPU_VRAM_MB else TIERS["cpu"]
    model = os.environ.get("BAKU_MODEL")
    if model:
        tier = Tier(tier.name, model, tier.context_tokens, tier.image_gen)
    return tier


def ollama_url() -> str:
    return os.environ.get("BAKU_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def ram_gb() -> float:
    return round(psutil.virtual_memory().total / 1024**3, 1)
