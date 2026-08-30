from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from sure.site import container_registry


class ContainerRegistryTest(unittest.TestCase):
    def test_empty_repository_starts_at_zero_one_zero(self) -> None:
        self.assertEqual(container_registry.next_image_version([]), "0.1.0")

    def test_next_version_uses_highest_semver_patch(self) -> None:
        self.assertEqual(
            container_registry.next_image_version(
                ["latest", "0.1.9", "0.1.20", "1.0.2", "candidate"]
            ),
            "1.0.3",
        )

    def test_explicit_version_does_not_query_registry(self) -> None:
        reader = mock.Mock()
        version, evidence = container_registry.resolve_image_version(
            ["registry.example/org/model"], "2.4.6", tag_reader=reader
        )
        self.assertEqual(version, "2.4.6")
        self.assertEqual(evidence["mode"], "explicit")
        reader.assert_not_called()

    def test_auto_version_checks_all_repositories(self) -> None:
        source = "registry.example/org/model-source"
        target = "registry.example/org/model"

        def tags(repository: str) -> list[str]:
            return ["0.1.20"] if repository == source else ["0.1.19", "latest"]

        version, evidence = container_registry.resolve_image_version(
            [source, target], tag_reader=tags
        )
        self.assertEqual(version, "0.1.21")
        self.assertEqual(evidence["repositories"], [source, target])
        self.assertEqual(evidence["existing_tags"], ["0.1.19", "0.1.20", "latest"])

    def test_default_tag_reader_can_be_replaced_for_a_registry_test(self) -> None:
        with mock.patch.object(container_registry, "registry_tags", return_value=[]):
            version, _ = container_registry.resolve_image_version(
                ["registry.example/org/model"]
            )
        self.assertEqual(version, "0.1.0")

    def test_docker_config_credentials_are_loaded_without_changing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            docker_config = Path(temporary)
            encoded = base64.b64encode(b"robot:secret").decode("ascii")
            (docker_config / "config.json").write_text(
                json.dumps({"auths": {"registry.example": {"auth": encoded}}}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"DOCKER_CONFIG": str(docker_config)}):
                credentials = container_registry._docker_registry_credentials("registry.example")
        self.assertEqual(credentials, ("robot", "secret"))

    def test_registry_tags_follows_bearer_challenge(self) -> None:
        headers = Message()
        headers["WWW-Authenticate"] = (
            'Bearer realm="http://auth.example/token",service="registry",'
            'scope="repository:org/model:pull"'
        )
        unauthorized = HTTPError(
            "http://registry.example/v2/repo/tags/list", 401, "Unauthorized", headers, None
        )
        responses = [
            unauthorized,
            ({"token": "token-value"}, {}),
            ({"name": "org/model", "tags": ["0.1.2", "0.1.1"]}, {}),
        ]

        def request(url: str, request_headers: dict[str, str]):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with mock.patch.object(
            container_registry, "_docker_registry_credentials", return_value=("robot", "secret")
        ), mock.patch.object(
            container_registry, "_registry_json_request", side_effect=request
        ) as registry_request:
            tags = container_registry.registry_tags("registry.example/org/model")
        self.assertEqual(tags, ["0.1.1", "0.1.2"])
        self.assertTrue(
            registry_request.call_args_list[1].args[1]["Authorization"].startswith("Basic ")
        )
        self.assertEqual(
            registry_request.call_args_list[2].args[1]["Authorization"], "Bearer token-value"
        )

    def test_registry_failure_is_not_replaced_with_a_guess(self) -> None:
        failure = HTTPError(
            "http://registry.example/v2/repo/tags/list", 500, "Failed", Message(), None
        )
        with mock.patch.object(container_registry, "_registry_json_request", side_effect=failure):
            with self.assertRaisesRegex(ValueError, "HTTP 500"):
                container_registry.registry_tags("registry.example/org/model")


if __name__ == "__main__":
    unittest.main()
