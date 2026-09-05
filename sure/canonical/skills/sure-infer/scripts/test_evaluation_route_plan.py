#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_evaluation_route_plan as route_plan  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
