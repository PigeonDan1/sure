"""Opt-in contract smoke test for a local Qwen3-VL SGLang endpoint."""

from __future__ import annotations

import json
import os
import urllib.request
import unittest


@unittest.skipUnless(os.environ.get("SURE_QWEN3_VL_SMOKE") == "1", "set SURE_QWEN3_VL_SMOKE=1 for live SGLang smoke")
class Qwen3VLSmokeTests(unittest.TestCase):
    base_url = os.environ.get("QWEN3_VL_BASE_URL", "http://127.0.0.1:31000/v1")
    api_key = os.environ.get("QWEN3_VL_API_KEY", "")

    def request(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def test_models_and_chat_completion(self) -> None:
        models = self.request("/models")
        ids = {str(item.get("id")) for item in models.get("data", []) if isinstance(item, dict)}
        self.assertIn("qwen3-vl-4b-instruct", ids)
        result = self.request(
            "/chat/completions",
            {
                "model": "qwen3-vl-4b-instruct",
                "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": 8,
            },
        )
        text = str((result.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        self.assertTrue(text)
