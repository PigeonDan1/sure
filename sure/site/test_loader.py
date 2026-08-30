#!/usr/bin/env python3
"""Tests for the site policy loader."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import SitePolicyError, validate_site_policy

_ROOT = "/srv"


def _policy(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema": "sure.site.policy.v1",
        "site_id": "test-site",
        "policy_version": 1,
        "storage": {
            "approved_models_roots": [f"{_ROOT}/models"],
            "approved_results_roots": [f"{_ROOT}/results"],
            "forbidden_output_roots": [_ROOT],
            "runtime_root": f"{_ROOT}/runtime",
        },
        "datasets": {"allowed_source_roots": [f"{_ROOT}/datasets"]},
        "execution": {"surfaces": ["local", "vc"]},
    }
    base.update(overrides)
    return base


class VcDefaultPartitionTest(unittest.TestCase):
    def test_default_partition_is_returned(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["vc"], "vc_partitions": ["gpu-a"], "vc_default_partition": "gpu-a"})
        )
        self.assertEqual(policy["execution"]["vc_default_partition"], "gpu-a")
    def test_default_partition_must_be_an_allowed_partition(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(execution={"surfaces": ["vc"], "vc_partitions": ["gpu-a"], "vc_default_partition": "gpu-b"})
            )
        self.assertIn("execution.vc_default_partition", str(raised.exception))


class LocalRuntimeTest(unittest.TestCase):
    def test_omitted_local_runtimes_remain_container_only(self) -> None:
        policy = validate_site_policy(_policy(execution={"surfaces": ["local"]}))
        self.assertEqual(policy["execution"]["local_runtimes"], ["container"])

    def test_python_runtime_requires_explicit_site_permission(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["local"], "local_runtimes": ["python", "container"]})
        )
        self.assertEqual(policy["execution"]["local_runtimes"], ["python", "container"])

    def test_rejects_an_unsupported_local_runtime(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(execution={"surfaces": ["local"], "local_runtimes": ["virtualenv"]})
            )
        self.assertIn("execution.local_runtimes", str(raised.exception))


class ContainerRegistryTest(unittest.TestCase):
    def test_container_registry_is_returned(self) -> None:
        policy = validate_site_policy(_policy(network={"container_registry": "registry.example/hpc"}))
        self.assertEqual(policy["network"]["container_registry"], "registry.example/hpc")

    def test_container_registry_rejects_a_non_string(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(network={"container_registry": 123}))
        self.assertIn("network.container_registry", str(raised.exception))


class AbsolutePathTest(unittest.TestCase):
    def test_rejects_a_path_that_does_not_start_with_a_slash(self) -> None:
        storage = {
            "approved_models_roots": ["C:/srv/models"],
            "approved_results_roots": [f"{_ROOT}/results"],
            "forbidden_output_roots": [_ROOT],
            "runtime_root": f"{_ROOT}/runtime",
        }
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(storage=storage))
        self.assertIn("storage.approved_models_roots[0]", str(raised.exception))

    def test_accepts_an_absolute_dataset_projection_root(self) -> None:
        policy = validate_site_policy(
            _policy(
                datasets={
                    "allowed_source_roots": [f"{_ROOT}/datasets"],
                    "projection_root": "/var/lib/sure/dataset-projections",
                }
            )
        )
        self.assertEqual(
            policy["datasets"]["projection_root"],
            "/var/lib/sure/dataset-projections",
        )

    def test_rejects_a_relative_dataset_projection_root(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(
                    datasets={
                        "allowed_source_roots": [f"{_ROOT}/datasets"],
                        "projection_root": "data/projections",
                    }
                )
            )
        self.assertIn("datasets.projection_root", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
