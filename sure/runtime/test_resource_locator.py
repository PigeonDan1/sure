from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from .resource_locator import (
        CANONICAL_SKILLS_ROOT_ENV,
        LEGACY_SKILLS_ROOT_ENV,
        ResourceResolutionError,
        resolve_skill_script,
    )
except ImportError:  # pragma: no cover - direct-file compatibility
    from resource_locator import (
        CANONICAL_SKILLS_ROOT_ENV,
        LEGACY_SKILLS_ROOT_ENV,
        ResourceResolutionError,
        resolve_skill_script,
    )


class ResourceLocatorTests(unittest.TestCase):
    def test_explicit_backend_wins_and_legacy_remains_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            (repo / "sure" / "skills" / "sure_eval" / "scripts").mkdir(parents=True)
            (repo / "sure" / "canonical" / "skills" / "sure-eval" / "scripts").mkdir(parents=True)
            (repo / "sure" / "skills" / "sure_eval" / "scripts" / "check.py").write_text("legacy\n")
            (repo / "sure" / "canonical" / "skills" / "sure-eval" / "scripts" / "check.py").write_text("canonical\n")
            environment = {"SURE_REPOSITORY_ROOT": str(repo)}
            self.assertEqual(
                resolve_skill_script("sure_eval", "check.py", environment=environment).read_text(),
                "legacy\n",
            )
            environment[CANONICAL_SKILLS_ROOT_ENV] = str(repo / "sure" / "canonical" / "skills")
            self.assertEqual(
                resolve_skill_script("sure_eval", "check.py", environment=environment).read_text(),
                "canonical\n",
            )

    def test_explicit_legacy_root_can_be_used_by_a_portable_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            (repo / "sure" / "skills").mkdir(parents=True)
            backend = root / "backend"
            (backend / "sure_eval" / "scripts").mkdir(parents=True)
            script = backend / "sure_eval" / "scripts" / "check.py"
            script.write_text("backend\n")
            environment = {
                "SURE_REPOSITORY_ROOT": str(repo),
                LEGACY_SKILLS_ROOT_ENV: str(backend),
            }
            self.assertEqual(resolve_skill_script("sure_eval", "check.py", environment=environment).resolve(), script.resolve())

    def test_rejects_absolute_and_parent_resources(self) -> None:
        with self.assertRaises(ResourceResolutionError):
            resolve_skill_script("sure_eval", "../check.py", environment={"SURE_REPOSITORY_ROOT": str(Path.cwd())})
        with self.assertRaises(ResourceResolutionError):
            resolve_skill_script("sure_eval", "/tmp/check.py", environment={"SURE_REPOSITORY_ROOT": str(Path.cwd())})


if __name__ == "__main__":
    unittest.main()
