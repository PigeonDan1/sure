from __future__ import annotations

import json
import shutil
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
