from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from .resource_locator import (
        CANONICAL_SKILLS_ROOT_ENV,
        SEMANTIC_BACKEND_ROOT_ENV,
        resolve_semantic_backend,
    )
    from .semantic_backend import (
        SemanticBackendResolutionError,
        load_semantic_backend_manifest,
        resolve_semantic_backend_operation,
    )
except ImportError:  # pragma: no cover - direct-file compatibility
    from resource_locator import CANONICAL_SKILLS_ROOT_ENV, SEMANTIC_BACKEND_ROOT_ENV, resolve_semantic_backend
    from semantic_backend import SemanticBackendResolutionError, load_semantic_backend_manifest, resolve_semantic_backend_operation


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "sure" / "canonical" / "shared" / "evaluation" / "backend-manifest.json"
PACKAGE_DIR = REPOSITORY_ROOT / "sure" / "skills" / "sure_eval"


class SemanticBackendTests(unittest.TestCase):
    def test_manifest_and_resource_locator_resolve_the_same_canonical_operation(self) -> None:
        manifest = load_semantic_backend_manifest(PACKAGE_DIR, manifest_path=MANIFEST_PATH)
        resolved = resolve_semantic_backend_operation(
            "sure.eval.run",
            package_dir=PACKAGE_DIR,
            manifest_path=MANIFEST_PATH,
            expected_registry_digest=manifest.registry_digest,
        )
        via_locator = resolve_semantic_backend(
            "sure.eval.run",
            environment={
                "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                "SURE_SEMANTIC_BACKEND_MANIFEST": str(MANIFEST_PATH),
            },
        )
        self.assertEqual(resolved.path, via_locator.path)
        self.assertEqual(resolved.source, "canonical")
        self.assertEqual(resolved.resource_digest, via_locator.resource_digest)
        self.assertEqual(resolved.registry_digest, manifest.registry_digest)

    def test_explicit_backend_root_is_pinned_by_tree_and_resource_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend_root = root / "backend"
            shutil.copytree(
                REPOSITORY_ROOT / "sure" / "canonical" / "skills" / "sure-infer",
                backend_root / "sure-evaluation-backend",
            )
            resolved = resolve_semantic_backend_operation(
                "sure.eval.run",
                package_dir=PACKAGE_DIR,
                manifest_path=MANIFEST_PATH,
                environment={
                    "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                    SEMANTIC_BACKEND_ROOT_ENV: str(backend_root),
                },
            )
            self.assertEqual(resolved.source, "semantic-backend-root")
            self.assertEqual(resolved.path, backend_root / "sure-evaluation-backend" / "scripts" / "run_eval.py")
            self.assertEqual(resolved.integrity_root, "scripts")
            (backend_root / "sure-evaluation-backend" / "host-metadata.txt").write_text(
                "outside integrity root\n", encoding="utf-8"
            )
            still_resolved = resolve_semantic_backend_operation(
                "sure.eval.run",
                package_dir=PACKAGE_DIR,
                manifest_path=MANIFEST_PATH,
                environment={
                    "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                    SEMANTIC_BACKEND_ROOT_ENV: str(backend_root),
                },
            )
            self.assertEqual(still_resolved.path, resolved.path)
            (backend_root / "sure-evaluation-backend" / "scripts" / "tampered.txt").write_text(
                "inside integrity root\n", encoding="utf-8"
            )
            with self.assertRaises(SemanticBackendResolutionError):
                resolve_semantic_backend_operation(
                    "sure.eval.run",
                    package_dir=PACKAGE_DIR,
                    manifest_path=MANIFEST_PATH,
                    environment={
                        "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                        SEMANTIC_BACKEND_ROOT_ENV: str(backend_root),
                    },
                )

    def test_tampered_tree_and_symlink_are_rejected_before_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_root = root / "canonical"
            shutil.copytree(
                REPOSITORY_ROOT / "sure" / "canonical" / "skills" / "sure-infer",
                canonical_root / "sure-infer",
            )
            unrelated = canonical_root / "sure-infer" / "scripts" / "README.md"
            if unrelated.exists():
                unrelated.write_text("tampered\n", encoding="utf-8")
            else:
                (canonical_root / "sure-infer" / "scripts" / "tampered.txt").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(SemanticBackendResolutionError):
                resolve_semantic_backend_operation(
                    "sure.eval.run",
                    package_dir=PACKAGE_DIR,
                    manifest_path=MANIFEST_PATH,
                    environment={
                        "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                        CANONICAL_SKILLS_ROOT_ENV: str(canonical_root),
                    },
                )

            target = canonical_root / "sure-infer" / "scripts" / "run_eval.py"
            target.unlink()
            target.symlink_to(REPOSITORY_ROOT / "sure" / "canonical" / "skills" / "sure-infer" / "scripts" / "run_eval.py")
            with self.assertRaises(SemanticBackendResolutionError):
                resolve_semantic_backend_operation(
                    "sure.eval.run",
                    package_dir=PACKAGE_DIR,
                    manifest_path=MANIFEST_PATH,
                    environment={
                        "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                        CANONICAL_SKILLS_ROOT_ENV: str(canonical_root),
                    },
                )

    def test_expected_registry_and_bundle_digests_are_fail_closed(self) -> None:
        with self.assertRaises(SemanticBackendResolutionError):
            resolve_semantic_backend_operation(
                "sure.eval.run",
                package_dir=PACKAGE_DIR,
                manifest_path=MANIFEST_PATH,
                expected_registry_digest="sha256:" + "0" * 64,
            )
        manifest = load_semantic_backend_manifest(PACKAGE_DIR, manifest_path=MANIFEST_PATH)
        bundle_digest = manifest.bundles[0].canonical_tree_digest
        self.assertIsNotNone(bundle_digest)
        with self.assertRaises(SemanticBackendResolutionError):
            resolve_semantic_backend_operation(
                "sure.eval.run",
                package_dir=PACKAGE_DIR,
                manifest_path=MANIFEST_PATH,
                expected_bundle_digest="sha256:" + "f" * 64,
            )

    def test_repository_relative_roots_are_explicit_and_tree_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            package = repository / "package"
            backend = repository / "sure" / "runtime" / "shared-validator"
            package.mkdir(parents=True)
            backend.mkdir(parents=True)
            script = b"print('shared validator')\n"
            (backend / "check.py").write_bytes(script)
            resource_digest = f"sha256:{hashlib.sha256(script).hexdigest()}"
            tree_digest = f"sha256:{hashlib.sha256(f'check.py{chr(0)}{resource_digest}'.encode()).hexdigest()}"
            bundle = {
                "schema": "sure.semantic.backend.bundle.v1",
                "bundle_id": "shared-validator",
                "version": "test-v1",
                "description": "Repository-relative test backend.",
                "canonical_root": "sure/runtime/shared-validator",
                "canonical_root_kind": "repository",
                "legacy_root": "sure/runtime/shared-validator",
                "legacy_root_kind": "repository",
                "integrity_root": ".",
                "canonical_tree_digest": tree_digest,
                "legacy_tree_digest": tree_digest,
                "operations": [
                    {
                        "operation_id": "sure.test.shared.validate",
                        "description": "Validate from a repository root.",
                        "entrypoint": "check.py",
                        "consumer_skill_ids": ["sure_infer"],
                        "kind": "validate",
                        "timeout_ms": 1000,
                        "deterministic": True,
                        "canonical_resource_digest": resource_digest,
                        "legacy_resource_digest": resource_digest,
                    }
                ],
            }
            unsigned = {"schema": "sure.semantic.backend.manifest.v1", "bundles": [bundle]}
            encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            manifest = {**unsigned, "registry_digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}"}
            manifest_path = root / "semantic-backends.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            resolved = resolve_semantic_backend_operation(
                "sure.test.shared.validate",
                package_dir=package,
                manifest_path=manifest_path,
                environment={"SURE_REPOSITORY_ROOT": str(repository)},
            )
            self.assertEqual(resolved.source, "canonical")
            self.assertEqual(resolved.path, backend / "check.py")
            invalid_bundle = {**bundle, "canonical_root_kind": "ambient"}
            invalid_unsigned = {"schema": "sure.semantic.backend.manifest.v1", "bundles": [invalid_bundle]}
            invalid_encoded = json.dumps(invalid_unsigned, sort_keys=True, separators=(",", ":")).encode()
            invalid_manifest = {
                **invalid_unsigned,
                "registry_digest": f"sha256:{hashlib.sha256(invalid_encoded).hexdigest()}",
            }
            invalid_manifest_path = root / "invalid-semantic-backends.json"
            invalid_manifest_path.write_text(json.dumps(invalid_manifest), encoding="utf-8")
            with self.assertRaises(SemanticBackendResolutionError):
                load_semantic_backend_manifest(
                    package,
                    manifest_path=invalid_manifest_path,
                    environment={"SURE_REPOSITORY_ROOT": str(repository)},
                )
            (backend / "unregistered.txt").write_text("tamper\n", encoding="utf-8")
            with self.assertRaises(SemanticBackendResolutionError):
                resolve_semantic_backend_operation(
                    "sure.test.shared.validate",
                    package_dir=package,
                    manifest_path=manifest_path,
                    environment={"SURE_REPOSITORY_ROOT": str(repository)},
                )

    def test_shared_memory_entrypoint_matches_all_legacy_wrappers(self) -> None:
        canonical = (
            REPOSITORY_ROOT
            / "sure"
            / "canonical"
            / "shared"
            / "memory-backend"
            / "scripts"
            / "check_memory_extraction.py"
        )
        legacy_wrappers = [
            REPOSITORY_ROOT / "sure" / "skills" / skill / "scripts" / "check_memory_extraction.py"
            for skill in ("sure_feed", "sure_onboard", "sure_infer", "sure_eval", "sure_trans")
        ]
        self.assertTrue(all(path.read_bytes() == legacy_wrappers[0].read_bytes() for path in legacy_wrappers))
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            run_dir = workspace / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            declaration = artifacts / "extraction_declaration.json"
            declaration.write_text(
                json.dumps(
                    {
                        "schema": "sure.memory.extraction.v2",
                        "no_new_lessons": True,
                        "no_lessons_reason": "No reusable lesson in this fixture.",
                        "covered_by": [],
                        "candidates": [],
                        "infra_noise": False,
                        "infra_evidence": [],
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "SURE_RUNTIME_SUPPORT_ROOT": str(REPOSITORY_ROOT),
                "SURE_REPOSITORY_ROOT": str(workspace),
            }
            common = ["--run-dir", str(run_dir), "--produces", str(declaration)]
            def run(entrypoint: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-B", str(entrypoint), *common, "--repo-root", str(workspace)]
                    if entrypoint != canonical
                    else [sys.executable, "-B", str(entrypoint), *common],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )

            canonical_result = run(canonical)
            self.assertEqual(canonical_result.returncode, 0, canonical_result.stderr)
            for legacy in legacy_wrappers:
                legacy_result = run(legacy)
                self.assertEqual(canonical_result.returncode, legacy_result.returncode)
                self.assertEqual(canonical_result.stdout, legacy_result.stdout)
                self.assertEqual(canonical_result.stderr, legacy_result.stderr)
            declaration.write_text(
                json.dumps(
                    {
                        "schema": "sure.memory.extraction.v2",
                        "no_new_lessons": True,
                        "no_lessons_reason": "",
                        "covered_by": [],
                        "candidates": [],
                        "infra_noise": False,
                        "infra_evidence": [],
                    }
                ),
                encoding="utf-8",
            )
            canonical_failure = run(canonical)
            self.assertEqual(canonical_failure.returncode, 1)
            for legacy in legacy_wrappers:
                legacy_failure = run(legacy)
                self.assertEqual(canonical_failure.returncode, legacy_failure.returncode)
                self.assertEqual(canonical_failure.stdout, legacy_failure.stdout)
                self.assertEqual(canonical_failure.stderr, legacy_failure.stderr)

    def test_shared_feed_validators_resolve_and_preserve_legacy_cli_output(self) -> None:
        manifest = load_semantic_backend_manifest(PACKAGE_DIR, manifest_path=MANIFEST_PATH)
        cases = (
            ("sure.feed.validate_match_task", "check_match_task.py"),
            ("sure.feed.validate_model_input", "check_model_input.py"),
            ("sure.feed.validate_rank_select", "check_rank_select.py"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifact = run_dir / "artifact.json"
            artifact.write_text("{}\n", encoding="utf-8")
            environment = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
            }
            for operation_id, filename in cases:
                resolved = resolve_semantic_backend_operation(
                    operation_id,
                    package_dir=PACKAGE_DIR,
                    manifest_path=MANIFEST_PATH,
                    expected_registry_digest=manifest.registry_digest,
                )
                self.assertEqual(resolved.source, "canonical")
                self.assertEqual(
                    resolved.path,
                    REPOSITORY_ROOT / "sure" / "canonical" / "shared" / "feed-validator" / "scripts" / filename,
                )
                legacy = REPOSITORY_ROOT / "sure" / "skills" / "sure_feed" / "scripts" / filename
                canonical_result = subprocess.run(
                    [sys.executable, "-B", str(resolved.path), "--run-dir", str(run_dir), "--produces", str(artifact)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                legacy_result = subprocess.run(
                    [sys.executable, "-B", str(legacy), "--run-dir", str(run_dir), "--produces", str(artifact)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(canonical_result.returncode, legacy_result.returncode)
                self.assertEqual(canonical_result.stdout, legacy_result.stdout)
                self.assertEqual(canonical_result.stderr, legacy_result.stderr)

    def test_shared_onboard_validators_resolve_and_preserve_legacy_cli_output(self) -> None:
        manifest = load_semantic_backend_manifest(PACKAGE_DIR, manifest_path=MANIFEST_PATH)
        cases = (
            ("sure.onboard.validate_build_plan", "check_build_plan.py"),
            ("sure.onboard.validate_spec", "check_spec.py"),
            ("sure.onboard.validate_fixture", "check_fixture.py"),
            ("sure.onboard.validate_weights", "check_weights.py"),
            ("sure.onboard.validate_artifact_manifest", "check_artifact_manifest.py"),
            ("sure.onboard.validate_verdict", "check_verdict.py"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifact = run_dir / "artifact.json"
            artifact.write_text("{}\n", encoding="utf-8")
            environment = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
            }
            for operation_id, filename in cases:
                resolved = resolve_semantic_backend_operation(
                    operation_id,
                    package_dir=PACKAGE_DIR,
                    manifest_path=MANIFEST_PATH,
                    expected_registry_digest=manifest.registry_digest,
                )
                self.assertEqual(resolved.source, "canonical")
                self.assertEqual(
                    resolved.path,
                    REPOSITORY_ROOT / "sure" / "canonical" / "shared" / "onboard-validator" / "scripts" / filename,
                )
                legacy = REPOSITORY_ROOT / "sure" / "skills" / "sure_onboard" / "scripts" / filename
                canonical_result = subprocess.run(
                    [sys.executable, "-B", str(resolved.path), "--run-dir", str(run_dir), "--produces", str(artifact)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                legacy_result = subprocess.run(
                    [sys.executable, "-B", str(legacy), "--run-dir", str(run_dir), "--produces", str(artifact)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(canonical_result.returncode, legacy_result.returncode)
                self.assertEqual(canonical_result.stdout, legacy_result.stdout)
                self.assertEqual(canonical_result.stderr, legacy_result.stderr)

    def test_invalid_manifest_is_not_replaced_by_a_later_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantic-backends.json"
            value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            value["registry_digest"] = "sha256:" + "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(SemanticBackendResolutionError):
                load_semantic_backend_manifest(
                    PACKAGE_DIR,
                    manifest_path=path,
                    environment={"SURE_REPOSITORY_ROOT": str(REPOSITORY_ROOT)},
                )


if __name__ == "__main__":
    unittest.main()
