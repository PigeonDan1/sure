from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sure.runtime.repository_layout import (
    RepositoryLayoutError,
    evaluation_engine_root,
    local_results_root,
    repository_root,
    runtime_support_root,
)


class RepositoryLayoutTests(unittest.TestCase):
    def test_discovers_legacy_repository_and_its_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "sure" / "skills" / "sure_infer" / "scripts" / "run_eval.py"
            script.parent.mkdir(parents=True)
            script.touch()
            (root / "sure" / "runtime" / "evaluation").mkdir(parents=True)
            (root / "sure" / "runtime" / "evaluation" / "runtime.json").touch()
            (root / "sure" / "site").mkdir(parents=True)
            (root / "sure" / "site" / "loader.py").touch()
            self.assertEqual(repository_root(script, {}), root)
            self.assertEqual(runtime_support_root(script, repository=root, environment={}), root)

    def test_requires_an_explicit_workspace_for_an_installed_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            script = runtime / "backends" / "eval" / "scripts" / "run_eval.py"
            script.parent.mkdir(parents=True)
            script.touch()
            (runtime / "runtime-support.lock.json").touch()
            (runtime / "sure" / "runtime" / "evaluation").mkdir(parents=True)
            (runtime / "sure" / "runtime" / "evaluation" / "runtime.json").touch()
            (runtime / "sure" / "site").mkdir(parents=True)
            (runtime / "sure" / "site" / "loader.py").touch()
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            with self.assertRaises(RepositoryLayoutError):
                repository_root(script, {})
            environment = {"SURE_REPOSITORY_ROOT": str(workspace)}
            self.assertEqual(repository_root(script, environment), workspace)
            self.assertEqual(runtime_support_root(script, repository=workspace, environment=environment), runtime)

    def test_rejects_markerless_layouts_and_invalid_explicit_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "arbitrary" / "nested" / "run_eval.py"
            script.parent.mkdir(parents=True)
            script.touch()
            with self.assertRaises(RepositoryLayoutError):
                repository_root(script, {})

            repository = root / "repository"
            (repository / "sure" / "runtime" / "evaluation").mkdir(parents=True)
            (repository / "sure" / "runtime" / "evaluation" / "runtime.json").touch()
            (repository / "sure" / "site").mkdir(parents=True)
            (repository / "sure" / "site" / "loader.py").touch()
            invalid = root / "not-a-runtime"
            invalid.mkdir()
            with self.assertRaises(RepositoryLayoutError):
                runtime_support_root(
                    script,
                    repository=repository,
                    environment={"SURE_RUNTIME_SUPPORT_ROOT": str(invalid)},
                )

    def test_keeps_external_inputs_and_local_outputs_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "local-results"
            engine = root / "external-engine"
            environment = {
                "SURE_LOCAL_RESULTS_ROOT": str(results),
                "SURE_EVALUATION_HOME": str(engine),
            }
            self.assertEqual(local_results_root(root, environment), results.resolve())
            self.assertEqual(evaluation_engine_root(root, environment), engine.resolve())


if __name__ == "__main__":
    unittest.main()
