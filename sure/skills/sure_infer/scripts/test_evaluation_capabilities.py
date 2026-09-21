#!/usr/bin/env python3
"""Tests for evaluation_capabilities.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation_capabilities import discover_engine_capabilities  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
ENGINE_ROOT = Path(__file__).resolve().parents[4] / "sure" / "external" / "sure-evaluation"

PLAN_WITH_CER = 'def build_agent_plan(task, **kwargs):\n    return {"metrics": ["cer"]}\n'
SPEC_WITHOUT_ROUTES = 'def build_pipeline_spec(task, **kwargs):\n    return {"route_choices": []}\n'


class DiscoverEngineCapabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine_root = Path(tmp.name)

    def _discover(
        self,
        task: str,
        language: str,
        *,
        agent_plan: str = PLAN_WITH_CER,
        cli_adapters: str = SPEC_WITHOUT_ROUTES,
    ):
        """Discover against a stand-in engine checkout whose probe modules behave as asked."""

        package = self.engine_root / "src" / "sure_eval" / "evaluation"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "agent_plan.py").write_text(agent_plan, encoding="utf-8")
        (package / "cli_adapters.py").write_text(cli_adapters, encoding="utf-8")
        return discover_engine_capabilities(self.engine_root, task, language)

    def test_engine_failure_is_not_answered_with_defaults(self) -> None:
        """A shadowed sure_eval makes the engine's lazy task import fail mid-call.

        sure_eval/evaluation/scripts/run.py reaches the task pipeline through
        import_module, so the failure lands inside build_pipeline_spec rather
        than at the import lines above it. It must not become a capability
        answer the caller cannot tell from a real one.
        """

        shadowed = (
            "def build_pipeline_spec(task, **kwargs):\n"
            "    raise ModuleNotFoundError(\"No module named 'sure_eval.evaluation.tasks'\")\n"
        )
        with self.assertRaises(RuntimeError) as ctx:
            self._discover("asr", "zh", cli_adapters=shadowed)
        self.assertIn(str(self.engine_root), str(ctx.exception))
        self.assertIn("ModuleNotFoundError", str(ctx.exception))
        self.assertIn("sure_eval.evaluation.tasks", str(ctx.exception))

    def test_unconfigured_route_is_still_a_real_answer(self) -> None:
        """ValueError is how the engine reports 'no route here', not a failure."""

        no_route = (
            "def build_pipeline_spec(task, **kwargs):\n"
            '    raise ValueError(f"No configured routes found for task {task!r} (language=xx)")\n'
        )
        capabilities = self._discover("asr", "xx", cli_adapters=no_route)
        self.assertEqual(capabilities["route_choices"], [])
        self.assertEqual(capabilities["default_metrics"], ["cer"])
        self.assertEqual(capabilities["supported_metrics"], ["cer"])

    def test_task_the_engine_rejects_stays_a_value_error(self) -> None:
        """The engine rejects an unsupported language from build_agent_plan itself.

        Both callers read ValueError as 'the engine does not cover this route'
        and fall back; turning it into a RuntimeError would make an ordinary
        unsupported language look like a broken engine.
        """

        rejected = 'def build_agent_plan(task, **kwargs):\n    raise ValueError("Unsupported ASR language: xx")\n'
        with self.assertRaises(ValueError) as ctx:
            self._discover("asr", "xx", agent_plan=rejected)
        self.assertIn("Unsupported ASR language: xx", str(ctx.exception))


@unittest.skipUnless(ENGINE_ROOT.is_dir(), "sure-evaluation submodule is not checked out")
class ProbeProcessBoundaryTest(unittest.TestCase):
    """The engine's src/ ships a package called sure_eval, the same top-level name
    as the harness-local package in this directory, and the two are divergent
    forks of it. Whichever is imported first pins the name for the whole process,
    so each case runs in its own interpreter with a real import order.
    """

    def _run(self, body: str) -> dict:
        code = "\n".join(
            [
                "import inspect, json, sys",
                "from pathlib import Path",
                "SCRIPTS, ENGINE_ROOT = Path(sys.argv[1]), Path(sys.argv[2])",
                "sys.path.insert(0, str(SCRIPTS))",
                "from evaluation_capabilities import discover_engine_capabilities",
                body,
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPTS), str(ENGINE_ROOT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_probe_leaves_the_local_package_importable(self) -> None:
        answer = self._run(
            "\n".join(
                [
                    "capabilities = discover_engine_capabilities(ENGINE_ROOT, 'ASR', 'zh')",
                    "leaked = sorted(n for n in sys.modules if n == 'sure_eval' or n.startswith('sure_eval.'))",
                    "from sure_eval.core.config import Config",
                    "print(json.dumps({'metrics': capabilities['supported_metrics'],"
                    " 'leaked': leaked, 'config': inspect.getfile(Config)}))",
                ]
            )
        )
        self.assertEqual(answer["metrics"], ["cer"])
        self.assertEqual(answer["leaked"], [])
        self.assertEqual(Path(answer["config"]), SCRIPTS / "sure_eval" / "core" / "config.py")

    def test_probe_answers_after_the_local_package_is_imported(self) -> None:
        answer = self._run(
            "\n".join(
                [
                    "from sure_eval.core.config import Config",
                    "capabilities = discover_engine_capabilities(ENGINE_ROOT, 'ASR', 'zh')",
                    "print(json.dumps({'metrics': capabilities['supported_metrics'],"
                    " 'config': inspect.getfile(Config)}))",
                ]
            )
        )
        self.assertEqual(answer["metrics"], ["cer"])
        self.assertEqual(Path(answer["config"]), SCRIPTS / "sure_eval" / "core" / "config.py")


if __name__ == "__main__":
    unittest.main()
