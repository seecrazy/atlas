"""Optional OpenAI-compatible LLM provider."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .config import ProjectConfig


class LLMUnavailable(RuntimeError):
    pass


class LLMProvider:
    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_config(cls, config: ProjectConfig) -> "LLMProvider":
        settings = config.llm
        base_url = os.environ.get(str(settings.get("base_url_env") or "ATLAS_LLM_BASE_URL"))
        api_key = os.environ.get(str(settings.get("api_key_env") or "ATLAS_LLM_API_KEY"))
        model = os.environ.get(str(settings.get("model_env") or "ATLAS_LLM_MODEL"))
        return cls(base_url=base_url, api_key=api_key, model=model)

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> str:
        if not self.available:
            raise LLMUnavailable("LLM provider is not configured")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body: Any = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"LLM request failed: {exc}") from exc
        try:
            return str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable("LLM response did not contain message content") from exc
