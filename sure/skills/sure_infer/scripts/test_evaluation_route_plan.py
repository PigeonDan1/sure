#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_evaluation_route_plan as route_plan  # noqa: E402
from evaluation_capabilities import EngineRouteUnavailable  # noqa: E402

NO_ROUTE = "No configured route found for ASR (language=zh, metric=utmos)"


def plan_with_blocking_node() -> dict:
    """The shape build_agent_plan returns when a node environment is missing."""
    return {
        "status": "blocked",
        "can_run_now": False,
        "selected_routes": [
            {
                "route": "asr/cer",
                "env_checks": [
                    {
                        "node_id": "normalization/wetext_norm",
                        "group": "normalization-extra",
                        "blocking": True,
                        "setup": {
                            "command": (
                                "cd /engine/src/sure_eval/evaluation/nodes/normalization/wetext_norm"
                                " && uv venv --python 3.11 && uv sync --frozen"
                            )
                        },
                    },
                    {"node_id": "scoring/token_cer", "blocking": False},
                ],
            }
        ],
        "next_steps": [
            "cd /engine/src/sure_eval/evaluation/nodes/normalization/wetext_norm"
            " && uv venv --python 3.11 && uv sync --frozen"
        ],
    }


class MaintainerSetupCommandTests(unittest.TestCase):
    """SKILL.md forbids a run to build a node environment; the plan must not ask it to."""

    def test_the_plan_never_hands_the_run_a_uv_command(self) -> None:
        # This is the command a run followed for twenty minutes before hitting
        # its own timeout, and it reached the run through this artifact.
        commands = route_plan._maintainer_setup_commands(plan_with_blocking_node())
        self.assertTrue(commands)
        for command in commands:
            self.assertNotIn("uv sync", command)
            self.assertNotIn("uv venv", command)

    def test_a_blocking_node_is_named_with_the_command_that_prepares_it(self) -> None:
        blockers = route_plan._node_environment_blockers(plan_with_blocking_node())
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["node_id"], "normalization/wetext_norm")
        self.assertEqual(blockers[0]["group"], "normalization-extra")
        self.assertIn("sure-eval env setup --node normalization/wetext_norm", blockers[0]["prepare_command"])


    def test_the_command_is_gone_from_the_nested_plan_too(self) -> None:
        # Each dataset entry copies selected_routes and the whole plan, so
        # filtering only the top-level list left the forbidden command sitting
        # in the artifact the run actually reads.
        scrubbed = route_plan._scrubbed_plan(plan_with_blocking_node())
        text = json.dumps(scrubbed)
        self.assertNotIn("uv sync", text)
        self.assertNotIn("uv venv", text)
        self.assertIn("sure-eval env setup --node normalization/wetext_norm", text)

    def test_a_ready_plan_keeps_its_own_next_steps(self) -> None:
        ready = {"selected_routes": [], "next_steps": ["Run `sure-eval metric describe`, then run it."]}
        self.assertEqual(
            route_plan._maintainer_setup_commands(ready),
            ["Run `sure-eval metric describe`, then run it."],
        )


class BlockingIssueTests(unittest.TestCase):
    """Which failure gets the dataset/model task hint appended."""

    def test_a_route_the_engine_does_not_have_keeps_the_task_mismatch_hint(self) -> None:
        issue = route_plan._blocking_issue("demo__v1", EngineRouteUnavailable(NO_ROUTE))
        self.assertTrue(issue.startswith(f"demo__v1: {NO_ROUTE}"), issue)
        self.assertIn("the dataset task does not match the model task", issue)

    def test_a_broken_engine_is_not_dressed_up_as_a_task_mismatch(self) -> None:
        issue = route_plan._blocking_issue("demo__v1", RuntimeError("No module named 'numpy'"))
        self.assertEqual(issue, "demo__v1: No module named 'numpy'")

    def test_the_hint_follows_the_engine_answer_not_its_wording(self) -> None:
        """A broken engine whose message happens to quote a route error is still broken.

        The child process reports which of the two the engine gave; reading the
        message instead made any failure that repeated the engine's wording look
        like a dataset the user had paired with the wrong model.
        """

        issue = route_plan._blocking_issue("demo__v1", RuntimeError(NO_ROUTE))
        self.assertEqual(issue, f"demo__v1: {NO_ROUTE}")

    def test_a_nameless_dataset_still_names_itself(self) -> None:
        self.assertEqual(
            route_plan._blocking_issue("", ValueError("Unsupported ASR language: xx")),
            "(unknown dataset): Unsupported ASR language: xx",
        )


if __name__ == "__main__":
    unittest.main()
