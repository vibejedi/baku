# Baku

Naga Industries' local AI assistant for Windows. Baku (short for Bakunawa) is a small sea
serpent that lives on your PC and helps with three things:

1. **Tidying** files, documents and the Desktop.
2. **Money and time**: a personal ledger, reminders and events.
3. **PC health**: what is slowing the machine down, what is taking up space, and
   Microsoft Defender status and scans.

Everything runs on the device. The language model is served locally by
[Ollama](https://ollama.com); no data is sent to a cloud service.

## Status

Version 0.1 has the agent loop, the tools, a terminal chat and a desktop app with a chat
window and a live PC health panel. Not built yet: the 3D mascot, image generation on the
GPU tier, and background reminder notifications.

## Setup (Windows)

1. Install Python 3.10+ and [Ollama](https://ollama.com).
2. Install Baku from this folder:

   ```bash
   pip install -e .
   ```

3. Check the setup. This reports which hardware tier was detected and the exact
   `ollama pull` command for the model that tier uses:

   ```bash
   baku doctor
   ```

4. Open the app (use `baku` alone for the terminal chat):

   ```bash
   baku app
   ```

The app runs in your browser by default. For a standalone window, install the extra with
`pip install -e ".[app]"`.

### Packaged build

Every push builds `Baku.exe` on Windows through GitHub Actions (the "Windows build"
workflow). Download the `Baku-windows` artifact from the latest run and double-click it; no
Python needed. Ollama is still required for the chat; the PC health panel works without it.

## Hardware tiers

Baku picks a tier at startup from the NVIDIA GPU's memory.

| Tier | Chosen when | Default model | Image generation |
|---|---|---|---|
| `gpu` | 6 GB+ VRAM (Naga Lakan II, RTX 5060 8 GB) | `qwen3:8b` | planned |
| `cpu` | otherwise (Naga Sipag I, integrated graphics) | `qwen3:4b` | off |

The default models are starting points and have not been benchmarked on the two devices.
Override with environment variables:

| Variable | Purpose |
|---|---|
| `BAKU_TIER` | Force `gpu` or `cpu` |
| `BAKU_MODEL` | Any Ollama model that supports tool calling |
| `BAKU_OLLAMA_URL` | Ollama address (default `http://127.0.0.1:11434`) |
| `BAKU_HOME` | Where the database and undo journal live (default `%LOCALAPPDATA%\Baku`) |

## Safety rules built into the tools

- Baku reports first, then recommends, and changes something only when the user opts in.
- Baku never deletes personal files. Organizing only moves them, never overwrites, and the
  last run can be undone. The one thing it can delete, with approval, is old temporary files.
- Baku can close a program with approval, but refuses to close ones Windows needs.
- Organizing is two steps: a plan that touches nothing, then an apply the user must approve.
- Shortcuts (`.lnk`) and system files are left where they are.
- Anything that changes the machine asks for a yes first.
- Money totals are computed in code, not by the language model.
- Virus detection is delegated to Microsoft Defender.

## Development

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

Layout: `src/baku/agent.py` is the conversation loop, `src/baku/llm.py` talks to Ollama,
and each file in `src/baku/tools/` registers tools with the `@tool` decorator.
