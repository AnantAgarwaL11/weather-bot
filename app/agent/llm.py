"""Thin, mockable LLM client. Only OpenAI is wired; any object with the same two
methods (e.g. a test fake or another provider) can be injected."""
from __future__ import annotations

import json
from typing import Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict: ...
    def complete_text(self, system: str, user: str) -> str: ...


class OpenAIClient:
    def __init__(self, api_key: str, model: str, timeout: float = 15.0):
        from openai import OpenAI  # imported lazily so the app runs without the key

        self.client = OpenAI(api_key=api_key, timeout=timeout)
        self.model = model

    def _call(self, system: str, user: str, json_mode: bool) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def complete_json(self, system: str, user: str) -> dict:
        data = json.loads(self._call(system, user, json_mode=True))
        if not isinstance(data, dict):
            raise ValueError("LLM did not return a JSON object")
        return data

    def complete_text(self, system: str, user: str) -> str:
        return self._call(system, user, json_mode=False)
