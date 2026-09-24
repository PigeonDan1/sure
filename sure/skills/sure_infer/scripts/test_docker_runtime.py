#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docker_runtime as dr


class DockerRuntimeTests(unittest.TestCase):
    def test_override_must_be_absolute(self) -> None:
        with patch.dict(os.environ, {dr.DOCKER_BIN_ENV: "relative/docker"}, clear=False):
            with self.assertRaises(ValueError):
                dr.resolve_docker_binary(which=lambda _name: None)

    def test_override_absolute_executable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "docker"
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            try:
                path.chmod(0o755)
            except OSError:
                pass
            with patch.dict(os.environ, {dr.DOCKER_BIN_ENV: str(path)}, clear=False):
                try:
                    self.assertEqual(dr.resolve_docker_binary(which=lambda _name: None), str(path))
                except ValueError as exc:
                    if "not executable" in str(exc):
                        self.skipTest("host cannot mark temp file executable")
                    raise

    def test_system_candidate_beats_which(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            system = Path(td) / "system-docker"
            system.write_text("#!/bin/sh\n", encoding="utf-8")
            try:
                system.chmod(0o755)
            except OSError:
                pass
            env = {k: v for k, v in os.environ.items() if k not in {dr.DOCKER_BIN_ENV, dr.SHARED_DOCKER_BIN_ENV}}
            with patch.dict(os.environ, env, clear=True):
                with patch.object(dr, "SYSTEM_DOCKER_CANDIDATES", (system,)):
                    with patch.object(dr, "_executable_file", lambda p: p == system):
                        self.assertEqual(
                            dr.resolve_docker_binary(which=lambda _name: "/path/shim-docker"),
                            str(system),
                        )

    def test_which_fallback(self) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {dr.DOCKER_BIN_ENV, dr.SHARED_DOCKER_BIN_ENV}
        }
        with patch.dict(os.environ, env, clear=True):
            with patch.object(dr, "SYSTEM_DOCKER_CANDIDATES", ()):
                self.assertEqual(
                    dr.resolve_docker_binary(which=lambda _name: "/custom/docker"),
                    "/custom/docker",
                )

    def test_shared_env_used_when_skill_absent(self) -> None:
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
                if k not in {dr.DOCKER_BIN_ENV, dr.SHARED_DOCKER_BIN_ENV}
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
