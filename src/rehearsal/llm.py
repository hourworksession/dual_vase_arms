"""LLM clients for the rehearsal engine.

OpenRouterClient talks to any OpenAI-compatible chat endpoint (OpenRouter by
default; Ollama or LM Studio by changing ``base_url``). It uses only the
standard library so the test bed has no extra dependencies.

MockClient returns scripted answers so the whole engine can run offline and
in the test suite.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

OPENROUTER_URL = "https://openrouter.ai/api/v1"


@dataclass
class LLMReply:
    content: dict[str, Any]          # parsed JSON object from the model
    model: str                       # model that actually answered
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    raw_text: str = ""


class LLMError(RuntimeError):
    pass


@dataclass
class OpenRouterClient:
    models: list[str]
    api_key: str | None = None
    base_url: str = OPENROUTER_URL
    temperature: float = 0.2
    max_tokens: int = 800
    timeout_s: float = 60.0
    max_retries: int = 3
    app_name: str = "dual_vase_arms rehearsal"
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key and "openrouter.ai" in self.base_url:
            raise LLMError("Set OPENROUTER_API_KEY (environment variable or .env file).")

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "X-Title": self.app_name}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def build_payload(self, messages: list[dict], schema: dict, schema_name: str) -> dict:
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        if "openrouter.ai" in self.base_url:
            # Fallback list: OpenRouter tries each in order. require_parameters
            # stops it routing to a provider that would ignore the schema.
            payload["models"] = self.models
            payload["provider"] = {"require_parameters": True}
            payload["usage"] = {"include": True}
        else:
            payload["model"] = self.models[0]
        return payload

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read().decode())

    def get(self, path: str) -> dict:
        req = urllib.request.Request(self.base_url.rstrip("/") + path, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read().decode())

    def complete_json(self, messages: list[dict], schema: dict, schema_name: str) -> LLMReply:
        payload = self.build_payload(messages, schema, schema_name)
        delay = 2.0
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            t0 = time.monotonic()
            try:
                data = self._post("/chat/completions", payload)
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:400]
                last = LLMError(f"HTTP {e.code}: {body}")
                if e.code in (429, 502, 503) and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise last from e
            except urllib.error.URLError as e:
                last = LLMError(f"Network error: {e.reason}")
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise last from e
            self.calls += 1
            if "error" in data:
                raise LLMError(f"Provider error: {data['error']}")
            text = data["choices"][0]["message"].get("content") or ""
            usage = data.get("usage") or {}
            return LLMReply(
                content=parse_json_object(text),
                model=data.get("model", "?"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cost_usd=usage.get("cost"),
                latency_s=time.monotonic() - t0,
                raw_text=text,
            )
        raise last or LLMError("No reply")


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating code fences some models add."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise LLMError(f"Reply was not JSON: {text[:200]!r}")
    return json.loads(t[start:end + 1])


@dataclass
class MockClient:
    """Offline stand-in. ``policy`` receives the messages and returns a dict."""
    policy: Callable[[list[dict]], dict]
    name: str = "mock"
    calls: int = field(default=0, init=False)

    def complete_json(self, messages: list[dict], schema: dict, schema_name: str) -> LLMReply:
        self.calls += 1
        out = self.policy(messages)
        return LLMReply(content=out, model=self.name, raw_text=json.dumps(out))


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines) so the key never goes in config."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
