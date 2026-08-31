#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import approval_core
from sure.runtime.model.bootstrap import _expected_manifest, _probe, _runtime_id, manifest_sha256


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ApprovalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "staging" / "demo-model"
        self.approved = self.root / "approved"
        self.runtime_root = self.root / "runtime"
        self.policy = self.root / "site.yaml"
        self.source.mkdir(parents=True)
        self.policy.write_text(
            "\n".join(
                [
                    "schema: sure.site.policy.v1",
                    "site_id: approval-test",
                    "policy_version: 1",
                    "storage:",
                    f"  approved_models_roots: [{self.approved}]",
                    f"  approved_results_roots: [{self.root / 'results'}]",
                    f"  forbidden_output_roots: [{self.root}]",
                    f"  runtime_root: {self.runtime_root}",
                    "datasets:",
                    f"  allowed_source_roots: [{self.root / 'datasets'}]",
                    "execution:",
                    "  surfaces: [local]",
                    "  local_runtimes: [python]",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        previous = os.environ.get("SURE_SITE_POLICY")
        os.environ["SURE_SITE_POLICY"] = str(self.policy)

        def restore() -> None:
            if previous is None:
                os.environ.pop("SURE_SITE_POLICY", None)
            else:
                os.environ["SURE_SITE_POLICY"] = previous

        self.addCleanup(restore)

    def build_python_bundle(self) -> None:
        for name, content in {
            "model.py": "VALUE = 1\n",
            "server.py": "print('server')\n",
            "validate.py": "print('validation passed')\n",
            "__init__.py": "\n",
            "model.spec.yaml": "task: asr\n",
            "config.yaml": "task: asr\n",
            "requirements.lock": "",
        }.items():
            (self.source / name).write_text(content, encoding="utf-8")

        probe = _probe(Path(sys.executable))
        lock_hash = approval_core.sha256_file(self.source / "requirements.lock")
        runtime_id = _runtime_id(lock_hash, probe)
        runtime = self.runtime_root / "models" / runtime_id
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "python").symlink_to(Path(sys.executable).resolve())
        (runtime / "requirements.lock").write_text("", encoding="utf-8")
        (runtime / "installed-packages.txt").write_text("", encoding="utf-8")
        runtime_manifest = _expected_manifest(
            runtime_id=runtime_id,
            lock_sha256=lock_hash,
            probe=probe,
            installed_packages_sha256=approval_core.sha256_file(runtime / "installed-packages.txt"),
        )
        write_json(runtime / "runtime-manifest.json", runtime_manifest)

        artifacts = self.source / "artifacts"
        artifacts.mkdir()
        write_json(artifacts / "model_input_resolved.json", {"model_name": "demo-model", "deployment_type": "local", "package_profile": "none"})
        write_json(artifacts / "artifact_manifest.json", {"status": "finalized", "model_name": "demo-model"})
        write_json(artifacts / "model_runtime_manifest.json", runtime_manifest)
        runtime_binding = {
            "required": True,
            "schema": runtime_manifest["schema"],
            "runtime_id": runtime_id,
            "runtime_type": "model_python",
            "backend": "uv",
            "python_executable": "bin/python",
            "python_version": runtime_manifest["python_version"],
            "python_abi": runtime_manifest["python_abi"],
            "python_platform": runtime_manifest["python_platform"],
            "manifest_path": "artifacts/model_runtime_manifest.json",
            "manifest_sha256": manifest_sha256(runtime_manifest),
            "lockfile_path": "requirements.lock",
            "lock_sha256": lock_hash,
            "working_dir": ".",
            "server_command": ["bin/python", "server.py"],
            "tool_names": ["transcribe"],
            "required_imports": [],
            "gpu_required": False,
        }
        model_hashes = {
            name: approval_core.sha256_file(self.source / name)
            for name in ("model.py", "server.py", "validate.py", "__init__.py", "model.spec.yaml", "config.yaml")
        }
        inventory = {
            "schema": "sure.onboard.runtime_inventory.v2",
            "generated_at": approval_core.now_iso(),
            "status": "ready",
            "model": {"name": "demo-model", "deployment_type": "local", "bundle_root": "."},
            "local_runtime": {"purpose": "validation", "eligible_for_eval": False},
            "model_runtime": runtime_binding,
            "harness_runtime": {"required": False, "runtime_type": "harness_python"},
            "container_runtime": {"required": False},
            "weights": {},
            "readiness": {"local_ready": True, "bundle_ready": True},
            "evidence": {"model_core_sha256": model_hashes},
            "policy": {"eval_runtime": "python", "host_python_fallback": False, "image_override_allowed": False, "nfs_models_mutable_by_eval": False},
        }
        package = {
            "schema": "sure.onboard.package_gate.v2",
            "status": "passed",
            "package_profile": "none",
            "model_name": "demo-model",
            "model_dir": ".",
            "readiness": {"local_ready": True, "container_ready": False, "docker_ready": False, "registry_ready": False, "bundle_ready": True},
        }
        write_json(artifacts / "runtime_inventory.json", inventory)
        write_json(artifacts / "package_gate.json", package)
        write_json(artifacts / "verdict.json", {"status": "passed"})
        required = {
            f"artifacts/{name}": approval_core.sha256_file(artifacts / name)
            for name in ("runtime_inventory.json", "package_gate.json", "verdict.json", "artifact_manifest.json", "model_runtime_manifest.json")
        }
        deployment = {
            "schema": "sure.onboard.deployment_ready.v2",
            "generated_at": approval_core.now_iso(),
            "status": "ready",
            "model_name": "demo-model",
            "package_profile": "none",
            "target_image": None,
            "target_image_digest": None,
            "target_image_ref": None,
            "model_runtime": runtime_binding,
            "runtime_inventory": "artifacts/runtime_inventory.json",
            "package_gate": "artifacts/package_gate.json",
            "verdict": "artifacts/verdict.json",
            "artifact_manifest": "artifacts/artifact_manifest.json",
            "required_artifact_sha256": required,
            "bundle_identity_sha256": approval_core.sha256_bytes(approval_core.canonical_json(required)),
            "execution_policy": {
                "container_only": False,
                "eval_runtime": "python",
                "isolation": "trusted_host",
                "model_integrity": "verify_before_after",
                "nfs_models_read_only": False,
                "model_bundle_mutation_allowed": False,
                "host_python_fallback": False,
                "approved_image_override": False,
            },
        }
        write_json(artifacts / "deployment_ready.json", deployment)

    def audit(self, run_dir: Path) -> dict:
        run_artifacts = run_dir / "artifacts"
        run_artifacts.mkdir(parents=True)
        args = argparse.Namespace(
            invocation_cwd=str(self.root),
            model_dir=str(self.source),
            mode="audit",
            repair="safe",
            review_manifest=None,
            decision=None,
            replace=False,
        )
        resolved = approval_core.resolve_input(args)
        write_json(run_artifacts / "approve_input_resolved.json", resolved)
        producer = approval_core.classify_producer(run_dir)
        write_json(run_artifacts / "producer_contract_report.json", producer)
        integrity = approval_core.audit_integrity(run_dir)
        write_json(run_artifacts / "integrity_report.json", integrity)
        plan = approval_core.plan_repairs(run_dir)
        write_json(run_artifacts / "repair_plan.json", plan)
        report = approval_core.apply_repairs(run_dir)
        write_json(run_artifacts / "repair_report.json", report)
        manifest = approval_core.build_manifest(run_dir)
        write_json(run_artifacts / "approval_manifest.json", manifest)
        runtime = approval_core.verify_runtime(run_dir)
        write_json(run_artifacts / "runtime_verification.json", runtime)
        review = approval_core.build_review(run_dir)
        write_json(run_artifacts / "review_packet.json", review)
        return review

    def test_full_python_audit_approval_and_eval_binding(self) -> None:
        self.build_python_bundle()
        source_before, _, _ = approval_core.tree_digest(self.source)
        audit_run = self.root / "runs" / "audit"
        review = self.audit(audit_run)
        self.assertEqual(review["status"], "awaiting_approval")
        self.assertTrue(review["approval"]["eval_visible"])

        approve_run = self.root / "runs" / "approve"
        approve_artifacts = approve_run / "artifacts"
        approve_artifacts.mkdir(parents=True)
        decision = approval_core.verify_decision(audit_run / "artifacts" / "review_packet.json", "approve", "validated in test")
        write_json(approve_artifacts / "approval_decision.json", decision)
        publication = approval_core.publish(approve_run, replace=False)
        write_json(approve_artifacts / "publication_result.json", publication)
        ready = approval_core.verify_publication(approve_run)
        write_json(approve_artifacts / "approval_ready.json", ready)

        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["deployment_binding"]["runtime_kind"], "python")
        self.assertEqual(Path(ready["destination"]), self.approved / "demo-model")
        source_after, _, _ = approval_core.tree_digest(self.source)
        self.assertEqual(source_before, source_after)

    def test_incomplete_onboard_product_is_rejected(self) -> None:
        write_json(self.source / "artifacts" / "model_input_resolved.json", {"model_name": "demo-model"})
        run = self.root / "runs" / "incomplete"
        (run / "artifacts").mkdir(parents=True)
        args = argparse.Namespace(invocation_cwd=str(self.root), model_dir=str(self.source), mode="audit", repair="safe", review_manifest=None, decision=None, replace=False)
        write_json(run / "artifacts" / "approve_input_resolved.json", approval_core.resolve_input(args))
        report = approval_core.classify_producer(run)
        self.assertEqual(report["status"], "failed")
        self.assertIn("TERMINAL_EVIDENCE_MISSING", {item["code"] for item in report["findings"]})

    def test_approval_root_comes_from_active_site_policy(self) -> None:
        self.build_python_bundle()
        args = argparse.Namespace(
            invocation_cwd=str(self.root),
            model_dir=str(self.source),
            mode="audit",
            repair="safe",
            review_manifest=None,
            decision=None,
            replace=False,
        )
        resolved = approval_core.resolve_input(args)
        self.assertEqual(resolved["approval"]["root"], str(self.approved))
        self.assertEqual(resolved["approval"]["configured_root"], str(self.approved))
        self.assertEqual(resolved["approval"]["destination"], str(self.approved / "demo-model"))
        self.assertTrue(resolved["approval"]["eval_visible"])

    def test_publication_rejects_review_packet_with_nonconfigured_root(self) -> None:
        self.build_python_bundle()
        audit_run = self.root / "runs" / "legacy-root-audit"
        review = self.audit(audit_run)
        custom_root = self.root / "custom-approved"
        review["approval"]["root"] = str(custom_root)
        review["approval"]["destination"] = str(custom_root / "demo-model")
        review["packet_digest"] = approval_core._packet_digest(review)
        review_path = audit_run / "artifacts" / "review_packet.json"
        write_json(review_path, review)

        approve_run = self.root / "runs" / "legacy-root-approve"
        approve_artifacts = approve_run / "artifacts"
        approve_artifacts.mkdir(parents=True)
        decision = approval_core.verify_decision(review_path, "approve", "validated in test")
        write_json(approve_artifacts / "approval_decision.json", decision)
        with self.assertRaisesRegex(approval_core.ApprovalError, "invalid publication destination"):
            approval_core.publish(approve_run, replace=False)

    def test_repair_none_does_not_silently_change_the_candidate(self) -> None:
        self.build_python_bundle()
        (self.source / ".cache").mkdir()
        (self.source / ".cache" / "derived.bin").write_bytes(b"cache")
        run = self.root / "runs" / "repair-none"
        run_artifacts = run / "artifacts"
        run_artifacts.mkdir(parents=True)
        args = argparse.Namespace(
            invocation_cwd=str(self.root),
            model_dir=str(self.source),
            mode="audit",
            repair="none",
            review_manifest=None,
            decision=None,
            replace=False,
        )
        write_json(run_artifacts / "approve_input_resolved.json", approval_core.resolve_input(args))
        write_json(run_artifacts / "producer_contract_report.json", approval_core.classify_producer(run))
        write_json(run_artifacts / "integrity_report.json", approval_core.audit_integrity(run))
        plan = approval_core.plan_repairs(run)
        self.assertEqual(plan["status"], "failed")
        self.assertIn("REPAIRS_DISABLED", {item["code"] for item in plan["findings"]})

    def test_broken_virtualenv_link_is_excluded_without_blocking_audit(self) -> None:
        self.build_python_bundle()
        (self.source / ".venv" / "bin").mkdir(parents=True)
        (self.source / ".venv" / "bin" / "python").symlink_to("/missing/python")
        review = self.audit(self.root / "runs" / "broken-venv")
        self.assertEqual(review["status"], "awaiting_approval")
        self.assertIn("exclude_unreferenced_caches", review["repairs_applied"])
        self.assertFalse((Path(review["candidate_dir"]) / ".venv").exists())

    def test_explicit_rejection_is_successful_terminal_without_publication(self) -> None:
        self.build_python_bundle()
        audit_run = self.root / "runs" / "reject-audit"
        self.audit(audit_run)
        approve_run = self.root / "runs" / "reject-approve"
        decision_path = approve_run / "artifacts" / "approval_decision.json"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("verify_human_decision.py")),
                "--run-dir",
                str(approve_run),
                "--produces",
                str(decision_path),
                "--review-manifest",
                str(audit_run / "artifacts" / "review_packet.json"),
                "--decision",
                "reject",
                "--rationale",
                "rejected in test",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(approval_core.read_json(decision_path)["status"], "rejected")
        with self.assertRaisesRegex(approval_core.ApprovalError, "requires an approved explicit decision"):
            approval_core.publish(approve_run, replace=False)

    def test_published_decision_tamper_is_rejected(self) -> None:
        self.build_python_bundle()
        audit_run = self.root / "runs" / "publish-audit"
        self.audit(audit_run)
        approve_run = self.root / "runs" / "publish-approve"
        approve_artifacts = approve_run / "artifacts"
        approve_artifacts.mkdir(parents=True)
        decision = approval_core.verify_decision(
            audit_run / "artifacts" / "review_packet.json", "approve", "validated in test"
        )
        write_json(approve_artifacts / "approval_decision.json", decision)
        publication = approval_core.publish(approve_run, replace=False)
        write_json(approve_artifacts / "publication_result.json", publication)
        published_decision = Path(publication["destination"]) / "artifacts" / "approval_decision.json"
        tampered = approval_core.read_json(published_decision)
        tampered["decision"] = "reject"
        write_json(published_decision, tampered)
        with self.assertRaisesRegex(approval_core.ApprovalError, "approval decision is invalid"):
            approval_core.verify_publication(approve_run)

    def test_external_symlink_and_candidate_tamper_are_rejected(self) -> None:
        self.build_python_bundle()
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        (self.source / "checkpoints").mkdir()
        (self.source / "checkpoints" / "external.bin").symlink_to(outside)
        run = self.root / "runs" / "link"
        (run / "artifacts").mkdir(parents=True)
        args = argparse.Namespace(invocation_cwd=str(self.root), model_dir=str(self.source), mode="audit", repair="safe", review_manifest=None, decision=None, replace=False)
        write_json(run / "artifacts" / "approve_input_resolved.json", approval_core.resolve_input(args))
        write_json(run / "artifacts" / "producer_contract_report.json", approval_core.classify_producer(run))
        integrity = approval_core.audit_integrity(run)
        self.assertEqual(integrity["status"], "failed")
        self.assertIn("EXTERNAL_SYMLINK", {item["code"] for item in integrity["findings"]})

        (self.source / "checkpoints" / "external.bin").unlink()
        review = self.audit(self.root / "runs" / "tamper")
        (Path(review["candidate_dir"]) / "model.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(approval_core.ApprovalError, "candidate changed"):
            approval_core.verify_decision(self.root / "runs" / "tamper" / "artifacts" / "review_packet.json", "approve", None)


if __name__ == "__main__":
    unittest.main()
