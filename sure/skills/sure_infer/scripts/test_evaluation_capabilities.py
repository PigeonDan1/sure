#!/usr/bin/env python3
"""Tests for evaluation_capabilities.py."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation_capabilities import discover_engine_capabilities  # noqa: E402


def _engine_root(tmp: str) -> Path:
    """An engine checkout complete enough to skip the static-snapshot branch."""

    root = Path(tmp)
    agent_plan = root / "src" / "sure_eval" / "evaluation" / "agent_plan.py"
    agent_plan.parent.mkdir(parents=True)
    agent_plan.write_text("", encoding="utf-8")
    return root


def _fake_engine(build_pipeline_spec) -> dict[str, types.ModuleType]:
    """sys.modules entries standing in for the engine's sure_eval package."""

    agent_plan = types.ModuleType("sure_eval.evaluation.agent_plan")
    agent_plan.build_agent_plan = lambda task, **kwargs: {"metrics": ["cer"]}
    cli_adapters = types.ModuleType("sure_eval.evaluation.cli_adapters")
    cli_adapters.build_pipeline_spec = build_pipeline_spec
    return {
        "sure_eval.evaluation.agent_plan": agent_plan,
        "sure_eval.evaluation.cli_adapters": cli_adapters,
    }


class DiscoverEngineCapabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        saved = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved))

    def _discover(self, build_pipeline_spec, task: str, language: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = _engine_root(tmp)
            with mock.patch.dict(sys.modules, _fake_engine(build_pipeline_spec)):
                return discover_engine_capabilities(root, task, language)

    def test_engine_failure_is_not_answered_with_defaults(self) -> None:
        """A shadowed sure_eval makes the engine's lazy task import fail mid-call.

        sure_eval/evaluation/scripts/run.py reaches the task pipeline through
        import_module, so the failure lands inside build_pipeline_spec rather
        than at the import lines above it. It must not become a capability
        answer the caller cannot tell from a real one.
        """

        def shadowed(task, **kwargs):
            raise ModuleNotFoundError("No module named 'sure_eval.evaluation.tasks'")

        with self.assertRaises(RuntimeError) as ctx:
            self._discover(shadowed, "asr", "zh")
        self.assertIn("sure_eval.evaluation.tasks", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, ModuleNotFoundError)

    def test_unconfigured_route_is_still_a_real_answer(self) -> None:
        """ValueError is how the engine reports 'no route here', not a failure."""

        def no_route(task, **kwargs):
            raise ValueError(f"No configured routes found for task {task!r} (language=xx)")

        capabilities = self._discover(no_route, "asr", "xx")
        self.assertEqual(capabilities["route_choices"], [])
        self.assertEqual(capabilities["default_metrics"], ["cer"])
        self.assertEqual(capabilities["supported_metrics"], ["cer"])


if __name__ == "__main__":
    unittest.main()
