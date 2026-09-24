#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docker_runtime as dr


class OnboardDockerRuntimeTests(unittest.TestCase):
    def test_onboard_env_beats_shared(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            onboard = Path(td) / "onboard-docker"
            shared = Path(td) / "shared-docker"
            for path in (onboard, shared):
                path.write_text("#!/bin/sh\n", encoding="utf-8")
                try:
                    path.chmod(0o755)
                except OSError:
                    pass
            with patch.dict(
                os.environ,
                {
                    dr.ONBOARD_DOCKER_BIN_ENV: str(onboard),
                    dr.SHARED_DOCKER_BIN_ENV: str(shared),
                },
                clear=False,
            ):
                try:
                    self.assertEqual(dr.resolve_docker_binary(which=lambda _name: None), str(onboard))
                except ValueError as exc:
                    if "not executable" in str(exc):
                        self.skipTest("host cannot mark temp file executable")
                    raise

    def test_shared_env_used_when_onboard_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            shared = Path(td) / "shared-docker"
            shared.write_text("#!/bin/sh\n", encoding="utf-8")
            try:
                shared.chmod(0o755)
            except OSError:
                pass
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {dr.ONBOARD_DOCKER_BIN_ENV, dr.SHARED_DOCKER_BIN_ENV}
            }
            env[dr.SHARED_DOCKER_BIN_ENV] = str(shared)
            with patch.dict(os.environ, env, clear=True):
                try:
                    self.assertEqual(dr.resolve_docker_binary(which=lambda _name: None), str(shared))
                except ValueError as exc:
                    if "not executable" in str(exc):
                        self.skipTest("host cannot mark temp file executable")
                    raise


if __name__ == "__main__":
    unittest.main()
