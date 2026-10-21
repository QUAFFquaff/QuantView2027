from __future__ import annotations

import json
import os
from typing import Any


class AIUnavailable(RuntimeError):
    pass


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, model: str, timeout: float = 90):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise AIUnavailable("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable("openai package is not installed") from exc
        base_url = os.getenv("OPENAI_BASE_URL") or None
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def complete_json(self, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = self.client.chat.completions.create(model=self.model, messages=messages)
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
