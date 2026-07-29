#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_predictions as ep  # noqa: E402


class BridgeFailureReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = ep._describe_external_pipeline
        self.addCleanup(setattr, ep, "_describe_external_pipeline", self.original)

    def _applies(self, failures: list[str] | None, **overrides: object) -> bool:
        kwargs: dict[str, object] = {
            "engine_root": Path("/engine"),
            "metric": "cer",
            "pipeline_id": None,
            "task": "ASR",
            "language": "zh",
            "timeout": 60,
            "failures": failures,
        }
        kwargs.update(overrides)
        return ep._external_metric_applies_to_task_language(**kwargs)

    def test_records_reason_when_the_bridge_itself_fails(self) -> None:
        def boom(**_: object) -> dict[str, object]:
            raise RuntimeError(
                "external sure-evaluation describe failed (returncode=1)\n"
                "STDOUT:\n\n"
                "STDERR:\n"
                "Traceback (most recent call last):\n"
                "  File \"<string>\", line 4, in <module>\n"
                "TypeError: build_pipeline_spec() got an unexpected keyword argument 'pipeline_id'"
            )

        ep._describe_external_pipeline = boom
        failures: list[str] = []
        self.assertFalse(self._applies(failures))
        self.assertEqual(len(failures), 1)
        self.assertIn("cer", failures[0])
        self.assertIn("unexpected keyword argument 'pipeline_id'", failures[0])

    def test_labels_the_reason_with_the_pipeline_id_when_given(self) -> None:
        def boom(**_: object) -> dict[str, object]:
            raise RuntimeError("engine exploded")

        ep._describe_external_pipeline = boom
        failures: list[str] = []
        self.assertFalse(self._applies(failures, metric=None, pipeline_id="asr.zh.cer.v1"))
        self.assertIn("asr.zh.cer.v1", failures[0])

    def test_supported_request_records_nothing(self) -> None:
        ep._describe_external_pipeline = lambda **_: {"pipeline_id": "asr.zh.cer.v1"}
        failures: list[str] = []
        self.assertTrue(self._applies(failures))
        self.assertEqual(failures, [])

    def test_failures_argument_stays_optional(self) -> None:
        def boom(**_: object) -> dict[str, object]:
            raise RuntimeError("engine exploded")

        ep._describe_external_pipeline = boom
        self.assertFalse(self._applies(None))

    def test_summary_keeps_the_tail_and_bounds_the_length(self) -> None:
        exc = RuntimeError("\n".join(f"line {index}" for index in range(1, 41)))
        summary = ep._summarize_bridge_error(exc)
        self.assertIn("line 40", summary)
        self.assertNotIn("line 1 ", summary)
        self.assertLessEqual(len(summary), 400)

    def test_summary_collapses_blank_lines(self) -> None:
        exc = RuntimeError("first\n\n\nsecond")
        self.assertEqual(ep._summarize_bridge_error(exc), "first | second")


class UnsupportedMessageTests(unittest.TestCase):
    def test_message_quotes_the_underlying_engine_errors(self) -> None:
        message = ep._unsupported_request_message(
            source="current sure-evaluation engine",
            dataset="aishell1__v1.0.2__asr",
            task="ASR",
            dataset_task="ASR",
            language="zh",
            requested="cer",
            failures=["cer: TypeError: build_pipeline_spec() got an unexpected keyword argument 'pipeline_id'"],
        )
        self.assertIn("aishell1__v1.0.2__asr", message)
        self.assertIn("requested=cer", message)
        self.assertIn("unexpected keyword argument", message)

    def test_message_without_failures_stays_short(self) -> None:
        message = ep._unsupported_request_message(
            source="legacy evaluator",
            dataset="aishell1__v1.0.2__asr",
            task="ASR",
            dataset_task="ASR",
            language="zh",
            requested="cer",
            failures=[],
        )
        self.assertIn("legacy evaluator", message)
        self.assertNotIn("rejected", message)


if __name__ == "__main__":
    unittest.main()
