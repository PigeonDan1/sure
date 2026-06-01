from __future__ import annotations

from sure_eval.tools.mcp_client import ToolAdapter


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args) -> None:
        return None

    def call(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return {"ok": True}


class _FakeRegistry:
    def __init__(self) -> None:
        self.created = 0
        self.client = _FakeClient()

    def create_client(self, name: str) -> _FakeClient:
        self.created += 1
        return self.client


def test_batch_evaluate_reuses_single_client() -> None:
    registry = _FakeRegistry()
    adapter = ToolAdapter(registry)

    results = adapter.batch_evaluate("demo_tool", ["a.wav", "b.wav"], task="ASR")

    assert registry.created == 1
    assert len(results) == 2
    assert [name for name, _ in registry.client.calls] == ["transcribe", "transcribe"]
