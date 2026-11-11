"""Minimal client for a local Ollama server. Nothing leaves the machine."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import Tier, ollama_url


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, tier: Tier, base_url: str | None = None, timeout: float = 300):
        self.tier = tier
        self.base_url = base_url or ollama_url()
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama returned {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"Cannot reach Ollama at {self.base_url}: {e}") from e

    def installed_models(self) -> list[str]:
        return [m["name"] for m in self._request("/api/tags").get("models", [])]

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Return the assistant message: {'role', 'content', 'tool_calls'?}."""
        payload = {
            "model": self.tier.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.tier.context_tokens},
        }
        if tools:
            payload["tools"] = tools
        return self._request("/api/chat", payload)["message"]
