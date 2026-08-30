from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import materialize_trans_inputs  # noqa: E402


class TransRegistryVersionTest(unittest.TestCase):
    def test_materialized_input_records_resolved_repositories_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            model.mkdir(parents=True)
            dockerfile = delivery / "Dockerfile"
            inference = delivery / "infer.py"
            dockerfile.write_text("FROM python:3.11\n", encoding="utf-8")
            inference.write_text(
                "def transcribe(audio_path): return audio_path\n", encoding="utf-8"
            )
            policy = {
                "schema": "sure.site.policy.v1",
                "site_id": "test-site",
                "policy_version": 1,
                "storage": {
                    "approved_models_roots": ["/srv/models"],
                    "approved_results_roots": ["/srv/results"],
                    "forbidden_output_roots": ["/srv"],
                    "runtime_root": "/srv/runtime",
                },
                "datasets": {"allowed_source_roots": ["/srv/datasets"]},
                "execution": {
                    "surfaces": ["vc"],
                    "vc_partitions": ["gpu-test"],
                    "vc_default_partition": "gpu-test",
                },
                "network": {"container_registry": "registry.example"},
                "container_delivery": {
                    "repository_template": "{registry}/org/{task}-{model_name}"
                },
            }
            policy_path = root / "site.yaml"
            policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
            source = "registry.example/org/asr-example__demo-source"
            target = "registry.example/org/asr-example__demo"
            evidence = {
                "mode": "registry_auto",
                "repositories": [source, target],
                "existing_tags": ["0.1.20"],
                "tags_by_repository": {source: ["0.1.20"], target: []},
            }
            argv = [
                "materialize_trans_inputs.py",
                "--dockerfile",
                str(dockerfile),
                "--model",
                str(model),
                "--model-name",
                "example__demo",
                "--inference-entrypoint",
                str(inference),
                "--framework",
                "pytorch",
                "--model-framework",
                "transformers",
                "--task-type",
                "asr",
                "--device",
                "cpu",
                "--vc-partition",
                "gpu-test",
                "--run-dir",
                str(run_dir),
                "--repo-root",
                str(root),
            ]
            with mock.patch.dict(
                os.environ, {"SURE_SITE_POLICY": str(policy_path.resolve())}
            ), mock.patch.object(
                materialize_trans_inputs,
                "resolve_image_version",
                return_value=("0.1.21", evidence),
            ) as version_resolver, mock.patch.object(sys, "argv", argv):
                self.assertEqual(materialize_trans_inputs.main(), 0)
            resolved = json.loads(
                (run_dir / "artifacts" / "trans_input_resolved.json").read_text(
                    encoding="utf-8"
                )
            )
        version_resolver.assert_called_once_with([source, target], None)
        self.assertEqual(resolved["image_version"], "0.1.21")
        self.assertEqual(resolved["image_version_resolution"], evidence)
        self.assertEqual(resolved["container_delivery"]["source_image"], f"{source}:0.1.21")
        self.assertEqual(resolved["container_delivery"]["target_image"], f"{target}:0.1.21")


if __name__ == "__main__":
    unittest.main()
