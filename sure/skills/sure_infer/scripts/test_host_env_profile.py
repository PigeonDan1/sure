#!/usr/bin/env python3
"""Tests: the shared host-env allowlist and the sanitized launch env it feeds.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_host_env_profile.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import host_env_profile
import python_execution


class SharedAllowlistTests(unittest.TestCase):
    def test_posix_launch_names_are_unchanged(self) -> None:
        self.assertEqual(
            host_env_profile.POSIX_HOST_ENV,
            {
                "CUDA_VISIBLE_DEVICES",
                "LANG",
                "LC_ALL",
                "LD_LIBRARY_PATH",
                "NVIDIA_DRIVER_CAPABILITIES",
                "NVIDIA_VISIBLE_DEVICES",
                "PATH",
                "TERM",
                "TZ",
            },
        )

    def test_windows_identity_names_cover_the_import_probe_floor(self) -> None:
        # The historical probe list: without these, Windows children died on
        # import (WinError 10106 without SystemRoot, `No module named 'pwd'`
        # from getpass.getuser() without USERNAME).
        self.assertTrue(
            {
                "HOME",
                "USERPROFILE",
                "USERNAME",
                "TEMP",
                "TMP",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "SYSTEMDRIVE",
                "OS",
            }
            <= host_env_profile.HOST_ENV_ALLOW
        )

    def test_windows_and_posix_sets_do_not_overlap(self) -> None:
        self.assertFalse(host_env_profile.POSIX_HOST_ENV & host_env_profile.WINDOWS_HOST_ENV)


class SafeEnvironmentTests(unittest.TestCase):
    def test_identity_variables_survive_the_launch_filter(self) -> None:
        source = {
            "PATH": r"C:\Windows\system32",
            "SYSTEMROOT": r"C:\Windows",
            "USERNAME": "runner",
            "TEMP": r"C:\Users\runner\AppData\Local\Temp",
            "USERPROFILE": r"C:\Users\runner",
            "SURE_TOKEN": "must-not-leak",
            "SOME_RANDOM_VAR": "dropped",
        }
        env = python_execution._safe_environment(source, {})
        for key in ("PATH", "SYSTEMROOT", "USERNAME", "TEMP", "USERPROFILE"):
            self.assertEqual(env[key], source[key])
        self.assertNotIn("SURE_TOKEN", env)
        self.assertNotIn("SOME_RANDOM_VAR", env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_declared_env_merges_under_the_same_sensitive_filter(self) -> None:
        declared = {
            "DEVICE": "cuda",
            "MY_API_KEY": "sk-secret",
        }
        env = python_execution._safe_environment({}, declared)
        self.assertEqual(env["DEVICE"], "cuda")
        self.assertNotIn("MY_API_KEY", env)

    def test_declared_env_cannot_override_refused_surface_keys(self) -> None:
        env = python_execution._safe_environment({}, {"PYTHONPATH": "/injected"})
        self.assertNotIn("PYTHONPATH", env)


if __name__ == "__main__":
    unittest.main()
