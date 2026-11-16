"""Local web app: a chat window plus a PC health panel, served only to this machine."""
from __future__ import annotations

import json
import secrets
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import gpu_vram_mb, pick_tier, ram_gb
from .llm import LLMError, OllamaClient
from .tools import Tool, diagnostics, files

WEB = Path(__file__).parent / "web"
CONFIRM_TIMEOUT = 300  # seconds to wait for the owner before treating it as a no


def explain(tool: Tool, args: dict) -> str:
    """What the owner is being asked to approve, in plain words."""
    try:
        if tool.name == "apply_organize_plan":
            return files.describe_plan(str(args.get("plan_id", "")))
        if tool.name == "undo_last_organize":
            return "Put the files from the last tidy-up back where they were."
        if tool.name == "end_process":
            return f"Close {args.get('name')}. Any unsaved work in it will be lost."
        if tool.name == "clean_temp_files":
            days = args.get("older_than_days", 7)
            return f"Permanently delete temporary files not used in the last {days} days."
        if tool.name == "start_defender_quick_scan":
            return "Run a Microsoft Defender quick scan. It takes a few minutes."
    except Exception:
        pass
    return f"{tool.description}\n{json.dumps(args, indent=2)}"


class Session:
    """One conversation. The agent runs on a worker thread; the page polls for progress."""

    def __init__(self, llm=None):
        self.tier = pick_tier()
        self.llm = llm or OllamaClient(self.tier)
        self.lock = threading.Lock()
        self.busy = False
        self._decided = threading.Event()
        self.reset()

    def reset(self) -> bool:
        with self.lock:
            if self.busy:
                return False
            self.agent = Agent(self.llm, self._confirm, on_tool=self._on_tool)
            self.log: list[dict] = []
            self.pending: dict | None = None
            self._allowed = False
            return True

    def _say(self, role: str, text: str) -> None:
        with self.lock:
            self.log.append({"role": role, "text": text})

    def _on_tool(self, name: str, args: dict) -> None:
        self._say("tool", name)

    def _confirm(self, tool: Tool, args: dict) -> bool:
        with self.lock:
            self._allowed = False
            self._decided.clear()
            self.pending = {"id": secrets.token_hex(8), "tool": tool.name, "args": args,
                            "summary": explain(tool, args)}
        self._decided.wait(CONFIRM_TIMEOUT)
        with self.lock:
            self.pending = None
            return self._allowed

    def decide(self, confirm_id: str, allow: bool) -> bool:
        with self.lock:
            if not self.pending or self.pending["id"] != confirm_id:
                return False
            self._allowed = bool(allow)
            self._decided.set()
            return True

    def chat(self, text: str) -> bool:
        with self.lock:
            if self.busy:
                return False
            self.busy = True
            self.log.append({"role": "user", "text": text})
        threading.Thread(target=self._work, args=(text,), daemon=True).start()
        return True

    def _work(self, text: str) -> None:
        try:
            self._say("baku", self.agent.ask(text) or "(Baku had nothing to say.)")
        except LLMError as e:
            self._say("error", f"Baku cannot reach the model. {e}")
        except Exception as e:  # keep the app alive whatever a turn does
            self._say("error", f"Something went wrong: {type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.busy = False

    def state(self, since: int) -> dict:
        with self.lock:
            return {"busy": self.busy, "pending": self.pending, "total": len(self.log),
                    "messages": self.log[max(since, 0):]}

    def status(self) -> dict:
        info = {"version": __version__, "tier": self.tier.name, "model": self.tier.model,
                "ram_gb": ram_gb(), "gpu_vram_mb": gpu_vram_mb(),
                "ollama_running": False, "model_installed": False}
        try:
            models = OllamaClient(self.tier, timeout=3).installed_models()
            info["ollama_running"] = True
            info["model_installed"] = any(
                m == self.tier.model or m.startswith(self.tier.model + ":") for m in models)
        except LLMError:
            pass
        return info


def health_snapshot() -> dict:
    """Health data for the side panel. Uses the tools directly, so it works without a model."""
    out = {}
    for key, fn in (("health", diagnostics.system_health), ("battery", diagnostics.battery_status),
                    ("gpu", diagnostics.gpu_status),
                    ("recommendations", diagnostics.health_recommendations)):
        try:
            out[key] = fn()
        except Exception as e:
            out[key] = {"error": f"{type(e).__name__}: {e}"}
    return out


def make_handler(session: Session, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # stay quiet in the console
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def _local(self) -> bool:
            # Only this machine may talk to Baku, and only through Baku's own page.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost")

        def _authorised(self) -> bool:
            return self._local() and secrets.compare_digest(
                self.headers.get("X-Baku-Token") or "", token)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if not self._local():
                return self._json({"error": "forbidden"}, 403)
            if path == "/":
                page = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if path == "/baku.png":
                return self._send(200, (WEB / "baku.png").read_bytes(), "image/png")
            if not self._authorised():
                return self._json({"error": "forbidden"}, 403)
            if path == "/api/status":
                return self._json(session.status())
            if path == "/api/health":
                return self._json(health_snapshot())
            if path == "/api/state":
                since = query.removeprefix("since=")
                return self._json(session.state(int(since) if since.isdigit() else 0))
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            if not self._authorised():
                return self._json({"error": "forbidden"}, 403)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 100_000)
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            if self.path == "/api/chat":
                text = str(body.get("text", "")).strip()
                if not text:
                    return self._json({"error": "empty message"}, 400)
                ok = session.chat(text)
                return self._json({"ok": ok}, 200 if ok else 409)
            if self.path == "/api/confirm":
                ok = session.decide(str(body.get("id", "")), body.get("allow") is True)
                return self._json({"ok": ok}, 200 if ok else 409)
            if self.path == "/api/reset":
                ok = session.reset()
                return self._json({"ok": ok}, 200 if ok else 409)
            self._json({"error": "not found"}, 404)

    return Handler


def start_server(port: int = 0, session: Session | None = None):
    """Start serving on this machine only. Returns (server, url, token)."""
    token = secrets.token_urlsafe(24)
    handler = make_handler(session or Session(), token)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:  # port taken, e.g. Baku is already open: let the system pick one
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/", token


def smoke_test() -> int:
    """Start the app, load its page and status, and exit. Used to check a packaged build."""
    server, url, token = start_server(0)
    try:
        page = urllib.request.urlopen(url, timeout=10).read().decode()
        req = urllib.request.Request(url + "api/status", headers={"X-Baku-Token": token})
        status = json.loads(urllib.request.urlopen(req, timeout=10).read())
        health = health_snapshot()
        assert "Baku" in page and token in page, "page did not load"
        assert "error" not in health["health"], health["health"]
        print(f"Baku {status['version']} OK: tier {status['tier']}, model {status['model']}, "
              f"RAM {status['ram_gb']} GB")
        return 0
    finally:
        server.shutdown()


def run_app(port: int = 8765, window: bool = True) -> int:
    server, url, _ = start_server(port)
    print(f"Baku is running at {url}  (close this window to stop Baku)")
    try:
        if window:
            try:
                import webview  # optional: pip install "baku[app]"
                webview.create_window("Baku", url, width=1180, height=780, min_size=(720, 520))
                webview.start()
                return 0
            except Exception:
                pass  # no window toolkit available: fall back to the browser
        webbrowser.open(url)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0
