#!/usr/bin/env python3
from __future__ import annotations

import unittest

from sure.site.loader import SitePolicyError, validate_site_policy


def policy(execution: dict) -> dict:
    return {
        "schema": "sure.site.policy.v1",
        "site_id": "test",
        "policy_version": 1,
        "storage": {
            "approved_models_roots": ["/srv/sure/models"],
            "approved_results_roots": ["/srv/sure/results"],
            "forbidden_output_roots": ["/srv/sure"],
            "runtime_root": "/var/cache/sure/runtime",
        },
        "datasets": {"allowed_source_roots": ["/srv/sure/datasets"]},
        "execution": execution,
    }


class SitePolicyTests(unittest.TestCase):
    def test_legacy_policy_defaults_to_container_only(self) -> None:
        parsed = validate_site_policy(policy({"surfaces": ["local"]}))

        self.assertEqual(parsed["execution"]["local_runtimes"], ["container"])

    def test_python_runtime_requires_explicit_valid_value(self) -> None:
        parsed = validate_site_policy(
            policy({"surfaces": ["local"], "local_runtimes": ["python", "container"]})
        )
        self.assertEqual(parsed["execution"]["local_runtimes"], ["python", "container"])

        with self.assertRaisesRegex(SitePolicyError, "unsupported value"):
            validate_site_policy(policy({"surfaces": ["local"], "local_runtimes": ["host"]}))


if __name__ == "__main__":
    unittest.main()
