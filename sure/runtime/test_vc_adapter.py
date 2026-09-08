from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sure.runtime.adapter_manifest import create_adapter_manifest
from sure.runtime.execution_bridge import capability_evidence, digest_json
from sure.runtime.vc_adapter import (
    VcAdapterError,
    prepare_vc_adapter,
    project_vc_policy_snapshot,
    run_vc_adapter,
)
from sure.site.loader import validate_site_policy


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
ALLOWED_ROOT = Path("/tmp/sure-adapter/results")


def _policy() -> dict:
    return validate_site_policy(
        {
            "schema": "sure.site.policy.v1",
            "site_id": "vc-adapter-test",
            "policy_version": 1,
            "storage": {
                "approved_models_roots": ["/reference/models"],
                "approved_results_roots": [str(ALLOWED_ROOT)],
                "forbidden_output_roots": ["/reference"],
                "runtime_root": "/tmp/sure-adapter/runtime",
            },
            "datasets": {"allowed_source_roots": {"default": "/reference/datasets"}},
            "execution": {
                "surfaces": ["local", "vc"],
                "local_runtimes": ["container"],
                "vc_project": "sure-test",
                "vc_partitions": ["gpu-a", "gpu-z"],
            },
        }
    )


def _policy_snapshot() -> dict:
    policy = _policy()
    bindings = [
        {
            "root_id": "approved-models.0",
            "role": "read_only_reference",
            "path": "/reference/models",
            "resolved_path": "/reference/models",
        },
        {
            "root_id": "approved-results.0",
            "role": "controlled_publication",
            "path": str(ALLOWED_ROOT),
            "resolved_path": str(ALLOWED_ROOT),
        },
        {
            "root_id": "dataset.default",
            "role": "dataset_source",
            "path": "/reference/datasets",
            "resolved_path": "/reference/datasets",
        },
        {
            "root_id": "forbidden-output.0",
            "role": "forbidden_output",
            "path": "/reference",
            "resolved_path": "/reference",
        },
        {
            "root_id": "runtime",
            "role": "runtime_cache",
            "path": "/tmp/sure-adapter/runtime",
            "resolved_path": "/tmp/sure-adapter/runtime",
        },
    ]
    bindings.sort(key=lambda item: item["root_id"])
    policy_digest = digest_json(policy)
    bindings_digest = digest_json(bindings)
    payload = {
        "schema": "sure.policy.snapshot.v1",
        "site_id": policy["site_id"],
        "policy_version": policy["policy_version"],
        "policy": policy,
        "source": {"kind": "test", "path": "/tmp/sure-adapter/site.yaml", "raw_sha256": DIGEST_A},
        "path_bindings": bindings,
        "policy_digest": policy_digest,
        "bindings_digest": bindings_digest,
    }
    return {**payload, "snapshot_digest": digest_json(payload)}


def _manifest(snapshot: dict) -> dict:
    return create_adapter_manifest(
        {
            "manifest_id": "sure-vc-local-port",
            "manifest_version": "1.0.0",
            "surface": "vc",
            "executor": {
                "executor_id": "sure.external.vc.mock",
                "kind": "remote",
                "version": "1.0.0",
                "digest": DIGEST_B,
                "trust_level": "host_enforced",
            },
            "policy_snapshot_digest": snapshot["snapshot_digest"],
            "authorization": {"allowed_projects": ["sure-test"], "allowed_partitions": ["gpu-a", "gpu-z"]},
            "resource_limits": {"max_gpus": 4, "max_memory_gb": 128, "max_cpus": 32},
            "timeouts": {
                "submit_seconds": 300,
                "wait_seconds": 1800,
                "command_seconds": 1200,
                "cancel_seconds": 120,
                "poll_seconds": 15,
            },
            "cancellation": {
                "supported": True,
                "strategy": "job_delete",
                "confirmation": "best_effort",
                "timeout_outcome": "BLOCKED",
            },
            "output_scope": {
                "output_root": "artifacts",
                "logs_root": "artifacts/vc_logs",
                "write_policy": "declared_outputs_only",
                "logs_retained": True,
            },
            "runtime": {
                "runtime_identity_digest": DIGEST_C,
                "container": {
                    "image": f"registry.example/sure/trans:1.0.0@{DIGEST_D}",
                    "image_digest": DIGEST_D,
                },
            },
            "attestation": {"mode": "receipt_digest"},
        }
    )


def _output_contract() -> dict:
    return {
        "schema": "sure.execution_output_contract.v1",
        "mode": "producing",
        "outputs": [{"artifact_id": "result", "path": "result.json", "kind": "file", "required": True}],
        "temporary_paths": [".staging"],
        "allow_missing_on_failure": True,
        "retain_failed_outputs": True,
    }


class VcAdapterPortTests(unittest.TestCase):
    def setUp(self) -> None:
        ALLOWED_ROOT.mkdir(parents=True, exist_ok=True)
        self.snapshot = _policy_snapshot()
        self.manifest = _manifest(self.snapshot)
        self.run_root = Path(tempfile.mkdtemp(prefix="port-", dir=ALLOWED_ROOT))
        self.resources = {"gpus": 1, "memory_gb": 32, "cpus": 8}
        self.timeouts = {
            "submit_seconds": 300,
            "wait_seconds": 1800,
            "command_seconds": 1200,
            "cancel_seconds": 120,
            "poll_seconds": 15,
        }
        self.preparation = prepare_vc_adapter(
            policy_snapshot=self.snapshot,
            manifest=self.manifest,
            executor=self.manifest["executor"],
            runtime_identity_digest=DIGEST_C,
            container_image_digest=DIGEST_D,
            output_scope={"output_root": "artifacts", "logs_root": "artifacts/vc_logs"},
            project="sure-test",
            partition="gpu-a",
            resources=self.resources,
            timeouts=self.timeouts,
            run_id="adapter-run",
            unit_id="vc-execute",
            operation="inference",
            staging_root=self.run_root,
            entrypoint={"executable": "python", "argv": ["-c", "print('ok')"]},
            subject={
                "bundle_manifest_path": str(self.run_root / "bundle.json"),
                "bundle_digest": DIGEST_A,
                "runtime_identity_digest": DIGEST_C,
            },
            capability_requirements=[
                {"capability_id": "sure.execution.remote", "capability_class": "execution_capability", "required": True},
                {"capability_id": "sure.execution.vc", "capability_class": "execution_capability", "required": True},
            ],
            output_contract=_output_contract(),
            request_id="adapter-request",
            created_at="2026-09-08T00:00:00Z",
        )

    def tearDown(self) -> None:
        for path in sorted(self.run_root.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.run_root.rmdir()

    def _evidence(self, status: str = "AVAILABLE") -> list[dict]:
        return [
            capability_evidence("sure.execution.remote", status=status, details={"mock": True}),
            capability_evidence("sure.execution.vc", status=status, details={"mock": True}),
        ]

    def _mock_submitter(self, *, timed_out: bool = False):
        seen: list[bool] = []
        mock_vc = self.run_root.parent / "mock-vc"
        mock_vc.write_text("#!/usr/bin/env python3\nprint('mock vc submit')\n", encoding="utf-8")
        mock_vc.chmod(0o755)

        def submit(payload: dict) -> dict:
            seen.append((self.run_root / "execution_request.json").is_file())
            process = subprocess.run([str(mock_vc), "submit"], check=False, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0)
            log_dir = Path(payload["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stdout.log").write_text(process.stdout, encoding="utf-8")
            if not timed_out:
                (self.run_root / "result.json").write_text('{"ok":true}\n', encoding="utf-8")
            return {
                "job_id": "mock-job",
                "partition": payload["partition"],
                "project": payload["project"],
                "exit_code": None if timed_out else 0,
                "timed_out": timed_out,
                "duration_ms": 1,
                "log_dir": str(log_dir),
                "submit_command": [str(mock_vc), "submit"],
                "vc_diagnostics": process.stdout,
            }

        return submit, seen

    def test_projection_and_request_are_bound_to_verified_policy(self) -> None:
        projection = project_vc_policy_snapshot(self.snapshot)
        self.assertEqual(projection["policy_authorization"]["allowed_partitions"], ["gpu-a", "gpu-z"])
        self.assertEqual(self.preparation.request["policy_digest"], projection["policy_digest"])
        self.assertEqual(self.preparation.request["adapter_manifest_digest"], self.manifest["manifest_digest"])
        self.assertEqual(self.preparation.request["runtime_requirements"]["vc_partition"], "gpu-a")

    def test_policy_projection_matches_the_canonical_policy_fixture(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "canonical" / "fixtures" / "external-adapter-policy.v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        policy = validate_site_policy(fixture["site_policy"])
        bindings = [
            {
                "root_id": "approved-models.0",
                "role": "read_only_reference",
                "path": "/reference/models",
                "resolved_path": "/reference/models",
            },
            {
                "root_id": "approved-results.0",
                "role": "controlled_publication",
                "path": "/tmp/sure-adapter/results",
                "resolved_path": "/tmp/sure-adapter/results",
            },
            {
                "root_id": "dataset.default",
                "role": "dataset_source",
                "path": "/reference/datasets",
                "resolved_path": "/reference/datasets",
            },
            {
                "root_id": "forbidden-output.0",
                "role": "forbidden_output",
                "path": "/reference",
                "resolved_path": "/reference",
            },
            {
                "root_id": "runtime",
                "role": "runtime_cache",
                "path": "/tmp/sure-adapter/runtime",
                "resolved_path": "/tmp/sure-adapter/runtime",
            },
        ]
        bindings.sort(key=lambda item: item["root_id"])
        policy_digest = digest_json(policy)
        bindings_digest = digest_json(bindings)
        payload = {
            "schema": "sure.policy.snapshot.v1",
            "site_id": policy["site_id"],
            "policy_version": policy["policy_version"],
            "policy": policy,
            "source": {"kind": "bundled", "path": "/config/site.test.yaml", "raw_sha256": DIGEST_B},
            "path_bindings": bindings,
            "policy_digest": policy_digest,
            "bindings_digest": bindings_digest,
        }
        snapshot = {**payload, "snapshot_digest": digest_json(payload)}
        projection = project_vc_policy_snapshot(snapshot)
        self.assertEqual(projection["policy_surfaces"], fixture["expected_vc_projection"]["policy_surfaces"])
        self.assertEqual(projection["policy_authorization"], fixture["expected_vc_projection"]["policy_authorization"])

    def test_request_is_written_before_opt_in_mock_submit_and_contract_is_valid(self) -> None:
        submitter, seen = self._mock_submitter()
        run = run_vc_adapter(
            self.preparation,
            staging_root=self.run_root,
            submitter=submitter,
            allow_submit=True,
            capability_evidence_values=self._evidence(),
            capability_available=True,
        )
        self.assertEqual(seen, [True])
        self.assertTrue(run.submitted)
        self.assertIsNotNone(run.receipt)
        self.assertEqual(run.receipt["lifecycle"], "SUCCEEDED")
        self.assertEqual(run.admission["status"], "ADMITTED")
        self.assertTrue(run.admission["receipt_valid"])
        self.assertTrue(run.contract and run.contract["contract_valid"])
        self.assertTrue((self.run_root / "execution_request.json").is_file())
        self.assertTrue((self.run_root / "execution_receipt.json").is_file())
        self.assertTrue((self.run_root / "execution_admission.json").is_file())

    def test_default_path_is_cooperative_and_does_not_submit(self) -> None:
        called = False

        def forbidden(_: dict) -> dict:
            nonlocal called
            called = True
            raise AssertionError("submitter must not run without allow_submit")

        run = run_vc_adapter(
            self.preparation,
            staging_root=self.run_root,
            submitter=forbidden,
            capability_evidence_values=self._evidence(),
            capability_available=True,
        )
        self.assertFalse(called)
        self.assertIsNone(run.receipt)
        self.assertIsNone(run.contract)
        self.assertEqual(run.admission["status"], "REJECTED")
        self.assertEqual(run.admission["reason_code"], "INVALID_CONTRACT")
        self.assertTrue((self.run_root / "execution_request.json").is_file())

    def test_missing_capability_never_calls_submitter_and_is_not_started(self) -> None:
        called = False

        def forbidden(_: dict) -> dict:
            nonlocal called
            called = True
            raise AssertionError("missing capability must stop before submit")

        run = run_vc_adapter(
            self.preparation,
            staging_root=self.run_root,
            submitter=forbidden,
            allow_submit=True,
            capability_evidence_values=self._evidence("MISSING"),
            capability_available=False,
        )
        self.assertFalse(called)
        self.assertFalse(run.submitted)
        self.assertEqual(run.receipt["lifecycle"], "NOT_STARTED")
        self.assertEqual(run.admission["status"], "CAPABILITY_MISSING")
        self.assertFalse(run.contract["contract_valid"])

    def test_timeout_preserves_cancel_uncertainty(self) -> None:
        submitter, _ = self._mock_submitter(timed_out=True)
        run = run_vc_adapter(
            self.preparation,
            staging_root=self.run_root,
            submitter=submitter,
            allow_submit=True,
            capability_evidence_values=self._evidence(),
            capability_available=True,
            cancellation_confirmed=False,
        )
        self.assertEqual(run.receipt["lifecycle"], "CANCELLED")
        codes = {item["code"] for item in run.receipt["diagnostics"]}
        self.assertIn("CANCEL_UNCONFIRMED", codes)
        self.assertTrue(run.contract["contract_valid"])

    def test_policy_or_staging_drift_is_rejected_before_any_write(self) -> None:
        drifted = copy.deepcopy(self.manifest)
        drifted["policy_snapshot_digest"] = DIGEST_D
        with self.assertRaises(VcAdapterError):
            prepare_vc_adapter(
                policy_snapshot=self.snapshot,
                manifest=drifted,
                executor=drifted["executor"],
                runtime_identity_digest=DIGEST_C,
                container_image_digest=DIGEST_D,
                output_scope={"output_root": "artifacts", "logs_root": "artifacts/vc_logs"},
                project="sure-test",
                partition="gpu-a",
                resources=self.resources,
                timeouts=self.timeouts,
                run_id="drift",
                unit_id="vc-execute",
                operation="inference",
                staging_root=Path("/reference/forbidden"),
                entrypoint={"executable": "python", "argv": []},
                subject={"bundle_manifest_path": "/tmp/bundle.json"},
                capability_requirements=[],
            )
        self.assertFalse((self.run_root / "execution_request.json").exists())


if __name__ == "__main__":
    unittest.main()
