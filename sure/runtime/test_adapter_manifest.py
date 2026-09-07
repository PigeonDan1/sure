from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from sure.runtime.adapter_manifest import (
    admit_adapter_manifest,
    create_adapter_manifest,
    manifest_digest,
    validate_adapter_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "sure" / "canonical" / "fixtures" / "external-adapter-manifest.v1.json").read_text(encoding="utf-8")
)


def _parts(path: str) -> tuple[dict, str]:
    parts = path.split(".")
    key = parts.pop()
    return parts, key


def _delete(root: dict, path: str) -> None:
    parts, key = _parts(path)
    current = root
    for part in parts:
        value = current.get(part)
        if not isinstance(value, dict):
            return
        current = value
    current.pop(key, None)


def _set(root: dict, path: str, value: object) -> None:
    parts, key = _parts(path)
    current = root
    for part in parts:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[key] = value


def _apply(root: dict, case: dict) -> None:
    for path in case.get("delete", []):
        _delete(root, path)
    for path, value in case.get("set", {}).items():
        _set(root, path, value)


class AdapterManifestTests(unittest.TestCase):
    def test_complete_manifest_is_valid_and_hash_matches_fixture(self) -> None:
        valid = FIXTURE["valid"]
        self.assertEqual(validate_adapter_manifest(valid), [])
        self.assertEqual(manifest_digest(valid), valid["manifest_digest"])

    def test_create_normalizes_and_self_binds_manifest(self) -> None:
        unsigned = copy.deepcopy(FIXTURE["valid"])
        unsigned.pop("manifest_digest")
        self.assertEqual(create_adapter_manifest(unsigned), FIXTURE["valid"])

    def test_non_vc_manifest_does_not_require_queue_fields(self) -> None:
        remote = copy.deepcopy(FIXTURE["valid"])
        remote["surface"] = "remote"
        remote["authorization"] = {"allowed_projects": ["example-project"]}
        remote["runtime"].pop("container")
        remote["cancellation"] = {
            "supported": False,
            "strategy": "none",
            "confirmation": "not_applicable",
            "timeout_outcome": "BLOCKED",
        }
        remote.pop("manifest_digest")
        manifest = create_adapter_manifest(remote)
        self.assertEqual(validate_adapter_manifest(manifest), [])
        context = copy.deepcopy(FIXTURE["admission_context"])
        context["surface"] = "remote"
        context["policy_surfaces"] = ["remote"]
        context["policy_authorization"] = {"allowed_projects": ["example-project"]}
        context.pop("container_image_digest")
        context.pop("project")
        context.pop("partition")
        self.assertEqual(admit_adapter_manifest(manifest, context), [])

    def test_malformed_cases_are_rejected_with_stable_reason(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["id"]):
                candidate = copy.deepcopy(FIXTURE["valid"])
                _apply(candidate, case)
                errors = validate_adapter_manifest(candidate)
                self.assertEqual(errors, case["expected_errors"])

    def test_complete_manifest_is_admitted_for_bound_context(self) -> None:
        context = FIXTURE["admission_context"]
        self.assertEqual(admit_adapter_manifest(FIXTURE["valid"], context), [])

    def test_admission_cases_are_rejected_with_stable_reason(self) -> None:
        for case in FIXTURE["admission_cases"]:
            with self.subTest(case=case["id"]):
                context = copy.deepcopy(FIXTURE["admission_context"])
                _apply(context, case)
                errors = admit_adapter_manifest(FIXTURE["valid"], context)
                self.assertEqual(errors, case["expected_errors"])


if __name__ == "__main__":
    unittest.main()
