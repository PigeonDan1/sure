#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import execution_provenance as ep


class ExecutionProvenanceEnvTests(unittest.TestCase):
    def test_env_maps_fields_and_path(self) -> None:
        with TemporaryDirectory() as td:
            path = Path(td) / "execution_provenance.json"
            provenance = {
                "schema": "sure.eval.execution_provenance.v1",
                "harness_commit": "h" * 40,
                "evaluation_engine_commit": "e" * 40,
                "evaluation_runtime_id": "eval-rt",
                "evaluation_runtime_lock_sha256": "d" * 64,
                "image_digest": "sha256:" + "a" * 64,
                "image_ref": "registry.example/demo@sha256:" + "a" * 64,
            }
            env = ep.execution_provenance_env(path, provenance)
            self.assertEqual(env["SURE_EVAL_EXECUTION_PROVENANCE"], str(path))
            self.assertEqual(env["SURE_HARNESS_COMMIT"], "h" * 40)
            self.assertEqual(env["SURE_EVALUATION_ENGINE_COMMIT"], "e" * 40)
            self.assertEqual(env["SURE_EVALUATION_RUNTIME_ID"], "eval-rt")
            self.assertEqual(env["SURE_EVALUATION_LOCK_SHA256"], "d" * 64)
            self.assertEqual(env["SURE_EVAL_CONTAINER_IMAGE_DIGEST"], "sha256:" + "a" * 64)


if __name__ == "__main__":
    unittest.main()
