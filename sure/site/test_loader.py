#!/usr/bin/env python3
"""Tests for the site policy loader."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import (
    POLICY_DIGEST_ENV,
    POLICY_SNAPSHOT_DIGEST_ENV,
    SITE_POLICY_ENV,
    SITE_POLICY_SNAPSHOT_ENV,
    SitePolicyError,
    load_site_policy,
    validate_site_policy,
)

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
        "datasets": {"allowed_source_roots": {"default": f"{_ROOT}/datasets"}},
        "execution": {"surfaces": ["local", "vc"], "vc_project": "example-project"},
    }
    base.update(overrides)
    return base


def _digest(value: object) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _snapshot(policy: dict[str, object]) -> dict[str, object]:
    source = {
        "kind": "environment",
        "path": "/config/original-site.yaml",
        "raw_sha256": "sha256:" + "a" * 64,
    }
    bindings = [
        {
            "root_id": "models",
            "role": "read_only_reference",
            "path": f"{_ROOT}/models",
            "resolved_path": f"{_ROOT}/models",
        },
        {
            "root_id": "runtime",
            "role": "runtime_cache",
            "path": f"{_ROOT}/runtime",
            "resolved_path": f"{_ROOT}/runtime",
        },
    ]
    policy_digest = _digest(policy)
    bindings_digest = _digest(bindings)
    payload: dict[str, object] = {
        "schema": "sure.policy.snapshot.v1",
        "site_id": policy["site_id"],
        "policy_version": policy["policy_version"],
        "policy": policy,
        "source": source,
        "path_bindings": bindings,
        "policy_digest": policy_digest,
        "bindings_digest": bindings_digest,
    }
    return {**payload, "snapshot_digest": _digest(payload)}


class VcDefaultPartitionTest(unittest.TestCase):
    def test_default_partition_is_returned(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["vc"], "vc_project": "example-project", "vc_partitions": ["gpu-a"], "vc_default_partition": "gpu-a"})
        )
        self.assertEqual(policy["execution"]["vc_default_partition"], "gpu-a")

    def test_default_partition_must_be_an_allowed_partition(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(execution={"surfaces": ["vc"], "vc_project": "example-project", "vc_partitions": ["gpu-a"], "vc_default_partition": "gpu-b"})
            )
        self.assertIn("execution.vc_default_partition", str(raised.exception))


class VcProjectTest(unittest.TestCase):
    def test_project_is_returned(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["vc"], "vc_project": "example-project"})
        )
        self.assertEqual(policy["execution"]["vc_project"], "example-project")

    def test_project_is_required_for_vc(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(execution={"surfaces": ["vc"]}))
        self.assertIn("execution.vc_project", str(raised.exception))


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
        policy = validate_site_policy(_policy(network={"container_registry": "registry.example/example-org"}))
        self.assertEqual(policy["network"]["container_registry"], "registry.example/example-org")

    def test_container_registry_rejects_a_non_string(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(network={"container_registry": 123}))
        self.assertIn("network.container_registry", str(raised.exception))


class ContainerDeliveryTest(unittest.TestCase):
    def test_repository_template_is_returned(self) -> None:
        policy = validate_site_policy(
            _policy(
                network={"container_registry": "registry.example"},
                container_delivery={
                    "repository_template": "{registry}/my-org/sure-{task}-{model_name}"
                },
            )
        )
        self.assertEqual(
            policy["container_delivery"]["repository_template"],
            "{registry}/my-org/sure-{task}-{model_name}",
        )

    def test_repository_template_requires_a_registry(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(
                    container_delivery={
                        "repository_template": "{registry}/my-org/sure-{model_name}"
                    }
                )
            )
        self.assertIn("network.container_registry", str(raised.exception))

    def test_repository_template_rejects_unknown_fields(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(
                    network={"container_registry": "registry.example"},
                    container_delivery={"repository_template": "{registry}/{owner}/{model_name}"},
                )
            )
        self.assertIn("unsupported field: owner", str(raised.exception))


class ApprovedResultsRootsTest(unittest.TestCase):
    def test_omitted_results_roots_normalize_to_an_empty_list(self) -> None:
        storage = {
            "approved_models_roots": [f"{_ROOT}/models"],
            "forbidden_output_roots": [_ROOT],
            "runtime_root": f"{_ROOT}/runtime",
        }
        policy = validate_site_policy(_policy(storage=storage))
        self.assertEqual(policy["storage"]["approved_results_roots"], [])

    def test_explicit_empty_results_roots_are_rejected(self) -> None:
        storage = {
            "approved_models_roots": [f"{_ROOT}/models"],
            "approved_results_roots": [],
            "forbidden_output_roots": [_ROOT],
            "runtime_root": f"{_ROOT}/runtime",
        }
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(storage=storage))
        self.assertIn("storage.approved_results_roots must be a non-empty list", str(raised.exception))


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


class PolicySnapshotTest(unittest.TestCase):
    def test_snapshot_precedes_mutable_policy_and_preserves_source_identity(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["local"], "local_runtimes": ["python"]})
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot_path = root / "site_policy.resolved.json"
            snapshot_path.write_text(
                json.dumps(_snapshot(policy), ensure_ascii=False),
                encoding="utf-8",
            )
            mutable_policy = root / "site.local.yaml"
            mutable_policy.write_text("schema: invalid\n", encoding="utf-8")

            resolved = load_site_policy(
                environment={
                    SITE_POLICY_SNAPSHOT_ENV: str(snapshot_path),
                    SITE_POLICY_ENV: str(mutable_policy),
                },
                required=True,
            )

        assert resolved is not None
        self.assertEqual(resolved["policy"], policy)
        self.assertEqual(resolved["path"], "/config/original-site.yaml")
        self.assertEqual(resolved["source"], "environment")
        self.assertEqual(resolved["sha256"], "a" * 64)
        self.assertEqual(resolved["policy_digest"], _digest(policy))

    def test_snapshot_rejects_tampered_policy_and_symlink(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["local"], "local_runtimes": ["python"]})
        )
        snapshot = _snapshot(policy)
        snapshot["policy"] = {**policy, "site_id": "tampered"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot_path = root / "site_policy.resolved.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            with self.assertRaises(SitePolicyError) as tampered:
                load_site_policy(
                    environment={SITE_POLICY_SNAPSHOT_ENV: str(snapshot_path)},
                    required=True,
                )
            self.assertIn("site_id", str(tampered.exception))

            target = root / "target.json"
            target.write_text(json.dumps(_snapshot(policy)), encoding="utf-8")
            linked = root / "linked.json"
            linked.symlink_to(target)
            with self.assertRaises(SitePolicyError) as unsafe:
                load_site_policy(
                    environment={SITE_POLICY_SNAPSHOT_ENV: str(linked)},
                    required=True,
                )
            self.assertIn("must not be a symlink", str(unsafe.exception))

    def test_snapshot_must_match_the_run_binding(self) -> None:
        policy = validate_site_policy(
            _policy(execution={"surfaces": ["local"], "local_runtimes": ["python"]})
        )
        original = _snapshot(policy)
        changed_policy = {**policy, "site_id": "changed-site"}
        changed = _snapshot(changed_policy)
        with tempfile.TemporaryDirectory() as temporary:
            snapshot_path = Path(temporary) / "site_policy.resolved.json"
            snapshot_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(SitePolicyError) as policy_mismatch:
                load_site_policy(
                    environment={
                        SITE_POLICY_SNAPSHOT_ENV: str(snapshot_path),
                        POLICY_DIGEST_ENV: str(original["policy_digest"]),
                    },
                    required=True,
                )
            self.assertIn("policy_digest", str(policy_mismatch.exception))

            with self.assertRaises(SitePolicyError) as snapshot_mismatch:
                load_site_policy(
                    environment={
                        SITE_POLICY_SNAPSHOT_ENV: str(snapshot_path),
                        POLICY_SNAPSHOT_DIGEST_ENV: str(original["snapshot_digest"]),
                    },
                    required=True,
                )
            self.assertIn("snapshot_digest", str(snapshot_mismatch.exception))


if __name__ == "__main__":
    unittest.main()
