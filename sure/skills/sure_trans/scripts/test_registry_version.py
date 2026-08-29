from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError


SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import materialize_trans_inputs  # noqa: E402
import vc_exec  # noqa: E402


class RegistryVersionTest(unittest.TestCase):
    def test_empty_repository_starts_at_zero_one_zero(self) -> None:
        self.assertEqual(vc_exec.next_image_version([]), "0.1.0")

    def test_highest_semver_patch_is_incremented(self) -> None:
        tags = ["latest", "0.1.9", "0.1.20", "1.0.2", "candidate"]
        self.assertEqual(vc_exec.next_image_version(tags), "1.0.3")

    def test_explicit_version_does_not_query_registry(self) -> None:
        with mock.patch.object(vc_exec, "registry_tags") as registry_tags:
            version, evidence = vc_exec.resolve_image_version("demo", "2.4.6")
        self.assertEqual(version, "2.4.6")
        self.assertEqual(evidence["mode"], "explicit")
        registry_tags.assert_not_called()

    def test_auto_version_checks_source_and_adapter_repositories(self) -> None:
        source = "registry.example/hpc/ai_asr-demo-source"
        adapter = "registry.example/hpc/ai_asr-demo"

        def tags(repository: str) -> list[str]:
            return ["0.1.20"] if repository == source else ["0.1.19", "latest"]

        with mock.patch.object(vc_exec, "registry_host", return_value="registry.example"), mock.patch.object(
            vc_exec, "registry_tags", side_effect=tags
        ):
            version, evidence = vc_exec.resolve_image_version("demo")
        self.assertEqual(version, "0.1.21")
        self.assertEqual(evidence["mode"], "registry_auto")
        self.assertEqual(evidence["repositories"], [source, adapter])
        self.assertEqual(evidence["existing_tags"], ["0.1.19", "0.1.20", "latest"])

    def test_docker_config_credentials_are_loaded_without_changing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            docker_config = Path(temporary)
            encoded = base64.b64encode(b"robot:secret").decode("ascii")
            (docker_config / "config.json").write_text(
                json.dumps({"auths": {"registry.example": {"auth": encoded}}}), encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"DOCKER_CONFIG": str(docker_config)}):
                credentials = vc_exec._docker_registry_credentials("registry.example")
        self.assertEqual(credentials, ("robot", "secret"))

    def test_registry_tags_follows_bearer_challenge(self) -> None:
        headers = Message()
        headers["WWW-Authenticate"] = (
            'Bearer realm="http://auth.example/token",service="harbor-registry",'
            'scope="repository:hpc/ai_asr-demo:pull"'
        )
        unauthorized = HTTPError("http://registry.example/v2/repo/tags/list", 401, "Unauthorized", headers, None)
        responses = [
            unauthorized,
            ({"token": "token-value"}, {}),
            ({"name": "hpc/ai_asr-demo", "tags": ["0.1.2", "0.1.1"]}, {}),
        ]

        def request(url: str, request_headers: dict[str, str]):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with mock.patch.object(vc_exec, "_docker_registry_credentials", return_value=("robot", "secret")), mock.patch.object(
            vc_exec, "_registry_json_request", side_effect=request
        ) as registry_request:
            tags = vc_exec.registry_tags("registry.example/hpc/ai_asr-demo")
        self.assertEqual(tags, ["0.1.1", "0.1.2"])
        token_headers = registry_request.call_args_list[1].args[1]
        final_headers = registry_request.call_args_list[2].args[1]
        self.assertTrue(token_headers["Authorization"].startswith("Basic "))
        self.assertEqual(final_headers["Authorization"], "Bearer token-value")

    def test_registry_failure_is_not_replaced_with_a_guess(self) -> None:
        failure = HTTPError("http://registry.example/v2/repo/tags/list", 500, "Failed", Message(), None)
        with mock.patch.object(vc_exec, "_registry_json_request", side_effect=failure):
            with self.assertRaisesRegex(ValueError, "HTTP 500"):
                vc_exec.registry_tags("registry.example/hpc/ai_asr-demo")

    def test_materialized_input_records_automatic_version_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            model.mkdir(parents=True)
            dockerfile = delivery / "Dockerfile"
            inference = delivery / "infer.py"
            dockerfile.write_text("FROM python:3.11\n", encoding="utf-8")
            inference.write_text("def transcribe(audio_path): return audio_path\n", encoding="utf-8")
            evidence = {
                "mode": "registry_auto",
                "repositories": ["registry.example/hpc/ai_asr-demo"],
                "existing_tags": ["0.1.20"],
                "tags_by_repository": {"registry.example/hpc/ai_asr-demo": ["0.1.20"]},
            }
            argv = [
                "materialize_trans_inputs.py",
                "--dockerfile", str(dockerfile),
                "--model", str(model),
                "--inference-entrypoint", str(inference),
                "--framework", "pytorch",
                "--model-framework", "transformers",
                "--task-type", "asr",
                "--device", "cpu",
                "--vc-partition", "gpu-test",
                "--run-dir", str(run_dir),
                "--repo-root", str(root),
            ]
            with mock.patch.object(materialize_trans_inputs, "resolve_image_version", return_value=("0.1.21", evidence)), mock.patch.object(
                sys, "argv", argv
            ):
                self.assertEqual(materialize_trans_inputs.main(), 0)
            resolved = json.loads(
                (run_dir / "artifacts" / "trans_input_resolved.json").read_text(encoding="utf-8")
            )
        self.assertEqual(resolved["image_version"], "0.1.21")
        self.assertEqual(resolved["image_version_resolution"], evidence)


if __name__ == "__main__":
    unittest.main()
