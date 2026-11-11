"""Command line entry point: `baku` to chat, `baku doctor` to check the setup."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .agent import Agent
from .config import data_dir, gpu_vram_mb, pick_tier, ram_gb
from .llm import LLMError, OllamaClient
from .tools import Tool, load_all
from .tools.reminders import due_now


def confirm_in_terminal(tool: Tool, args: dict) -> bool:
    print(f"\nBaku wants to run: {tool.name} {json.dumps(args)}")
    print(f"  ({tool.description})")
    return input("Allow? [y/N] ").strip().lower() in ("y", "yes")


def doctor() -> int:
    tier = pick_tier()
    print(f"Baku {__version__}")
    print(f"Data folder : {data_dir()}")
    print(f"RAM         : {ram_gb()} GB")
    print(f"GPU VRAM    : {gpu_vram_mb()} MB")
    print(f"Tier        : {tier.name} (model {tier.model}, image generation {'on' if tier.image_gen else 'off'})")
    print(f"Tools       : {len(load_all())}")
    client = OllamaClient(tier, timeout=5)
    try:
        models = client.installed_models()
    except LLMError as e:
        print(f"Ollama      : NOT REACHABLE - {e}")
        print("              Install it from https://ollama.com and start it.")
        return 1
    print(f"Ollama      : running at {client.base_url}")
    if any(m == tier.model or m.startswith(tier.model + ":") for m in models):
        print(f"Model       : {tier.model} is installed")
        return 0
    print(f"Model       : {tier.model} is MISSING - run: ollama pull {tier.model}")
    return 1


def chat() -> int:
    tier = pick_tier()
    agent = Agent(OllamaClient(tier), confirm_in_terminal)
    print(f"Baku is awake ({tier.model}). Type 'exit' to leave.")
    for r in due_now():
        print(f"  Reminder due: {r['title']} ({r['due']})")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            return 0
        try:
            print(f"\nbaku> {agent.ask(text)}")
        except LLMError as e:
            print(f"\n[Baku cannot reach the model: {e}]\nRun `baku doctor` to check the setup.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baku", description="Baku, your local sea-serpent assistant.")
    parser.add_argument("command", nargs="?", default="chat", choices=["chat", "doctor"])
    args = parser.parse_args(argv)
    return doctor() if args.command == "doctor" else chat()


if __name__ == "__main__":
    sys.exit(main())
