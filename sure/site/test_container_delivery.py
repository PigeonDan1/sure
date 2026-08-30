#!/usr/bin/env python3
from __future__ import annotations

import unittest

from sure.site.container_delivery import (
    ContainerDeliveryError,
    resolve_container_image,
    resolve_container_repository,
)


def _policy() -> dict[str, object]:
    return {
        "network": {"container_registry": "registry.example"},
        "container_delivery": {
            "repository_template": "{registry}/my-org/sure-{task}-{model_name}"
        },
    }


class ContainerDeliveryResolutionTest(unittest.TestCase):
    def test_resolves_a_portable_repository(self) -> None:
        self.assertEqual(
            resolve_container_repository(
                _policy(),
                task_type="ASR",
                model_name="OpenAI__Whisper Large/V3",
            ),
            "registry.example/my-org/sure-asr-openai__whisper-large-v3",
        )

    def test_resolves_a_stage_and_version(self) -> None:
        self.assertEqual(
            resolve_container_image(
                _policy(),
                task_type="asr",
                model_name="demo",
                stage="source",
                version="0.1.0",
            ),
            "registry.example/my-org/sure-asr-demo-source:0.1.0",
        )

    def test_registry_delivery_requires_an_explicit_template(self) -> None:
        with self.assertRaisesRegex(ContainerDeliveryError, "repository_template"):
            resolve_container_repository(
                {"network": {"container_registry": "registry.example"}},
                task_type="asr",
                model_name="demo",
            )

    def test_rejects_an_invalid_version(self) -> None:
        with self.assertRaisesRegex(ContainerDeliveryError, "invalid container image version"):
            resolve_container_image(
                _policy(),
                task_type="asr",
                model_name="demo",
                version="bad tag",
            )


if __name__ == "__main__":
    unittest.main()
