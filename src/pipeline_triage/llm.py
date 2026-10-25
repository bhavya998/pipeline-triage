"""OpenAI-compatible chat client with tool calls and usage/cost accounting."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    price: tuple[float, float, float] = (0.0, 0.0, 0.0)  # USD/M: cached in, uncached in, out


ENDPOINTS: dict[str, Endpoint] = {
    "deepseek-flash": Endpoint("deepseek-flash", "https://api.deepseek.com", "deepseek-flash",
                               "DEEPSEEK_API_KEY", {"thinking": {"type": "disabled"}},
                               (0.003, 0.15, 0.6)),
    "qwen3.5-4b": Endpoint("qwen3.5-4b", os.environ.get("LLAMA_URL", "http://127.0.0.1:8091/v1"),
                           "local", "", {"chat_template_kwargs": {"enable_thinking": False}}),
    "qwen3.5-9b": Endpoint("qwen3.5-9b", os.environ.get("LLAMA_URL", "http://127.0.0.1:8091/v1"),
                           "local", "", {"chat_template_kwargs": {"enable_thinking": False}}),
}


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    uncached: int = 0
    output: int = 0

    def add(self, u: dict[str, Any]) -> None:
        prompt = int(u.get("prompt_tokens", 0))
        cached = int(u.get("prompt_cache_hit_tokens",
                           (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0)
        self.calls += 1
        self.cached += cached
        self.uncached += prompt - cached
        self.output += int(u.get("completion_tokens", 0))

    def cost(self, ep: Endpoint) -> float:
        c, u, o = ep.price
        return (self.cached * c + self.uncached * u + self.output * o) / 1e6


def load_env() -> None:
    for path in (Path(".env"), Path("../.env")):
        if path.exists():
            for line in path.read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() and value.strip():
                    os.environ.setdefault(key.strip(), value.strip())


class ChatClient:
    def __init__(self, ep: Endpoint, timeout: float = 300.0) -> None:
        load_env()
        headers = {}
        if ep.api_key_env:
            headers["Authorization"] = f"Bearer {os.environ[ep.api_key_env]}"
        self.ep = ep
        self.usage = Usage()
        self.http = httpx.Client(base_url=ep.base_url, headers=headers, timeout=timeout)

    def complete(self, messages: list[dict], tools: list[dict], temperature: float = 0.0,
                 max_tokens: int = 2048) -> dict[str, Any]:
        body = {"model": self.ep.model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": [{"type": "function", "function": t} for t in tools],
                **self.ep.extra_body}
        for attempt in range(5):
            try:
                resp = self.http.post("/chat/completions", json=body)
                if resp.status_code in (429, 500, 502, 503) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                break
            except httpx.TransportError:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        data = resp.json()
        self.usage.add(data.get("usage") or {})
        return data["choices"][0]["message"]

    def close(self) -> None:
        self.http.close()
