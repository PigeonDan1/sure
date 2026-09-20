#!/usr/bin/env python3
"""Tests for the site policy loader."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import SitePolicyError, load_site_policy, validate_site_policy

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
    def test_accepts_a_windows_drive_letter_path(self) -> None:
        storage = {
            "approved_models_roots": ["C:/Users/example/.sure/approved/models"],
            "approved_results_roots": ["C:/Users/example/.sure/approved/results"],
            "forbidden_output_roots": ["C:/Users/example/.sure/approved"],
            "runtime_root": "C:\\Users\\example\\.sure\\runtime",
        }
        policy = validate_site_policy(_policy(storage=storage))
        self.assertEqual(
            policy["storage"]["approved_models_roots"][0],
            "C:/Users/example/.sure/approved/models",
        )
        self.assertEqual(policy["storage"]["runtime_root"], "C:\\Users\\example\\.sure\\runtime")

    def test_still_accepts_a_posix_path_on_every_host(self) -> None:
        policy = validate_site_policy(_policy())
        self.assertEqual(policy["storage"]["approved_models_roots"][0], f"{_ROOT}/models")

    def test_rejects_a_path_that_is_neither_posix_nor_drive_rooted(self) -> None:
        storage = {
            "approved_models_roots": ["srv/models"],
            "approved_results_roots": [f"{_ROOT}/results"],
            "forbidden_output_roots": [_ROOT],
            "runtime_root": f"{_ROOT}/runtime",
        }
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(storage=storage))
        self.assertIn("storage.approved_models_roots[0]", str(raised.exception))

    def test_rejects_a_drive_letter_with_no_separator(self) -> None:
        storage = {
            "approved_models_roots": ["C:models"],
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


_TOKEN_FIXTURE = "\n".join(
    [
        "schema: sure.site.policy.v1",
        "site_id: token-fixture",
        "policy_version: 1",
        "storage:",
        '  approved_models_roots: ["${HOME}/.sure/approved/models"]',
        '  forbidden_output_roots: ["${HOME}/.sure/approved"]',
        '  runtime_root: "${HOME}/.sure/runtime"',
        "datasets:",
        "  allowed_source_roots:",
        '    smoke: "${REPO}/fixtures/tasks"',
        "execution:",
        "  surfaces: [local]",
        "",
    ]
)


def _expected_home() -> str:
    return str(Path.home()).replace("\\", "/").rstrip("/")


class TokenExpansionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sure-site-token-"))
        (self.root / "config").mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_expands_tokens_for_an_explicit_policy_path(self) -> None:
        fixture = self.root / "config" / "token.yaml"
        fixture.write_text(_TOKEN_FIXTURE, encoding="utf-8")

        resolved = load_site_policy(
            repository_root=self.root,
            environment={"SURE_SITE_POLICY": str(fixture)},
        )

        self.assertEqual(resolved["source"], "environment")
        self.assertEqual(
            resolved["policy"]["storage"]["approved_models_roots"][0],
            f"{_expected_home()}/.sure/approved/models",
        )
        self.assertEqual(
            resolved["policy"]["datasets"]["allowed_source_roots"]["smoke"],
            f"{str(self.root).replace(chr(92), '/')}/fixtures/tasks",
        )

    def test_expands_tokens_for_a_local_policy(self) -> None:
        (self.root / "config" / "site.local.yaml").write_text(_TOKEN_FIXTURE, encoding="utf-8")

        resolved = load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(resolved["source"], "local")
        self.assertEqual(
            resolved["policy"]["storage"]["forbidden_output_roots"][0],
            f"{_expected_home()}/.sure/approved",
        )


_REPO_ROOT = Path(__file__).resolve().parents[2]


class CandidateOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sure-site-order-"))
        (self.root / "config").mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _ship_default(self) -> None:
        shutil.copyfile(
            _REPO_ROOT / "config" / "site.default.yaml",
            self.root / "config" / "site.default.yaml",
        )

    def test_default_is_selected_when_nothing_else_is_configured(self) -> None:
        self._ship_default()

        resolved = load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(resolved["source"], "default")
        self.assertEqual(resolved["policy"]["site_id"], "local-default")
        self.assertEqual(
            resolved["policy"]["storage"]["approved_models_roots"][0],
            f"{_expected_home()}/.sure/approved/models",
        )
        self.assertEqual(
            resolved["policy"]["datasets"]["allowed_source_roots"]["smoke"],
            f"{str(self.root).replace(chr(92), '/')}/fixtures/tasks",
        )
        self.assertEqual(resolved["policy"]["execution"]["local_runtimes"], ["python", "container"])
        self.assertNotIn("network", resolved["policy"])

    def test_local_outranks_the_shipped_default(self) -> None:
        self._ship_default()
        (self.root / "config" / "site.local.yaml").write_text(_TOKEN_FIXTURE, encoding="utf-8")

        resolved = load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(resolved["source"], "local")

    def test_bundled_outranks_local(self) -> None:
        self._ship_default()
        (self.root / "config" / "site.local.yaml").write_text(_TOKEN_FIXTURE, encoding="utf-8")
        (self.root / "config" / "site.bundled.yaml").write_text(_TOKEN_FIXTURE, encoding="utf-8")

        resolved = load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(resolved["source"], "bundled")

    def test_returns_none_when_even_the_default_is_absent(self) -> None:
        self.assertIsNone(load_site_policy(repository_root=self.root, environment={}))


_PLAIN_FIXTURE = _TOKEN_FIXTURE.replace("${HOME}", "/srv").replace("${REPO}", "/srv")


class MissingHomeTest(unittest.TestCase):
    """Five modules load the policy at import scope, so a host without a home
    directory must get a policy or a readable SitePolicyError, never a
    RuntimeError nobody catches."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sure-site-no-home-"))
        (self.root / "config").mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_a_policy_without_the_home_token_still_loads(self) -> None:
        (self.root / "config" / "site.local.yaml").write_text(_PLAIN_FIXTURE, encoding="utf-8")

        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("Could not determine home directory")):
            resolved = load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(resolved["source"], "local")

    def test_a_policy_that_needs_the_home_token_reports_a_policy_error(self) -> None:
        fixture = self.root / "config" / "site.local.yaml"
        fixture.write_text(_TOKEN_FIXTURE, encoding="utf-8")

        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("Could not determine home directory")):
            with self.assertRaises(SitePolicyError) as raised:
                load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(
            str(raised.exception),
            f"Cannot expand ${{HOME}} in local site policy {fixture}: no home directory",
        )

    def test_the_shipped_default_is_not_offered_without_a_home(self) -> None:
        shutil.copyfile(
            _REPO_ROOT / "config" / "site.default.yaml",
            self.root / "config" / "site.default.yaml",
        )

        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("Could not determine home directory")):
            self.assertIsNone(load_site_policy(repository_root=self.root, environment={}))


class PolicyEncodingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sure-site-encoding-"))
        (self.root / "config").mkdir()
        self.fixture = self.root / "config" / "site.local.yaml"
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_refuses_a_policy_that_is_not_valid_utf8(self) -> None:
        self.fixture.write_bytes(_PLAIN_FIXTURE.encode("utf-8").replace(b"approved/models", b"\xff\xfeapproved/models"))

        with self.assertRaises(SitePolicyError) as raised:
            load_site_policy(repository_root=self.root, environment={})

        self.assertTrue(
            str(raised.exception).startswith(f"Cannot parse local site policy {self.fixture}: "),
            str(raised.exception),
        )

    def test_still_loads_a_policy_that_starts_with_a_byte_order_mark(self) -> None:
        self.fixture.write_bytes(b"\xef\xbb\xbf" + _TOKEN_FIXTURE.encode("utf-8"))
        expanded = "﻿" + _TOKEN_FIXTURE.replace("${HOME}", _expected_home()).replace(
            "${REPO}", str(self.root).replace(chr(92), "/")
        )

        resolved = load_site_policy(repository_root=self.root, environment={})

        self.assertEqual(resolved["sha256"], hashlib.sha256(expanded.encode("utf-8")).hexdigest())


class RemovedFieldTest(unittest.TestCase):
    def test_rejects_network_internal_git_host(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(network={"internal_git_host": "git.example"}))
        self.assertIn("network has unknown field: internal_git_host", str(raised.exception))

    def test_rejects_network_gateway_portal(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(_policy(network={"gateway_portal": "https://portal.example"}))
        self.assertIn("network has unknown field: gateway_portal", str(raised.exception))

    def test_rejects_execution_vc_partition_priority(self) -> None:
        with self.assertRaises(SitePolicyError) as raised:
            validate_site_policy(
                _policy(
                    execution={
                        "surfaces": ["vc"],
                        "vc_project": "example-project",
                        "vc_partitions": ["gpu-a"],
                        "vc_partition_priority": {"gpu-a": 1},
                    }
                )
            )
        self.assertIn("execution has unknown field: vc_partition_priority", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
