#!/usr/bin/env python3
"""The model-local venv interpreter must follow the host's venv layout.

A venv created on Windows holds `Scripts/python.exe`; one created on POSIX
holds `bin/python`. Both gates spelled the POSIX layout unconditionally, so on
Windows they answered "no interpreter" for a perfectly good venv and claimed a
`bin/python` that no Windows venv ever creates.

Both spellings are exercised on every host: each case builds the venv as data,
one test taking the layout this platform creates and one taking the other
platform's, so neither is skipped anywhere.

Run directly:
    cd sure/skills/sure_onboard/scripts && python test_venv_interpreter_layout.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import check_env
import run_validate
from sure.runtime.uvenv import runtime_python_relative

OTHER_LAYOUT = "bin/python" if runtime_python_relative() == "Scripts/python.exe" else "Scripts/python.exe"


class VenvInterpreterLayout(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def _model_dir(self, layout: str) -> Path:
        """A model directory whose venv holds only that layout's interpreter."""
        model_dir = self.root / "sure" / "models" / "demo"
        interpreter = model_dir / ".venv" / layout
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.write_text("", encoding="utf-8")
        return model_dir

    def _resolve_python_executable(self, model_dir: Path) -> tuple[Path | None, str | None]:
        return check_env.resolve_python_executable(
            {},
            backend="uv",
            run_dir=self.root,
            artifact_path=self.root / "build_env_result.json",
            model_dir=model_dir,
            repo_root=self.root,
        )

    def test_build_env_gate_finds_the_venv_this_platform_creates(self) -> None:
        model_dir = self._model_dir(runtime_python_relative())

        resolved, error = self._resolve_python_executable(model_dir)

        self.assertIsNone(error)
        self.assertEqual(resolved, model_dir / ".venv" / runtime_python_relative())

    def test_build_env_gate_ignores_the_other_platforms_layout(self) -> None:
        model_dir = self._model_dir(OTHER_LAYOUT)

        resolved, error = self._resolve_python_executable(model_dir)

        self.assertIsNone(resolved)
        self.assertIn(str(model_dir / ".venv" / runtime_python_relative()), error or "")

    def test_validate_gate_uses_the_venv_this_platform_creates(self) -> None:
        model_dir = self._model_dir(runtime_python_relative())

        command = run_validate.maybe_use_model_local_python(
            ["python", "-c", "print(1)"], shell=False, cwd=model_dir
        )

        self.assertEqual(
            command,
            [str(model_dir / ".venv" / runtime_python_relative()), "-c", "print(1)"],
        )

    def test_validate_gate_ignores_the_other_platforms_layout(self) -> None:
        model_dir = self._model_dir(OTHER_LAYOUT)

        command = run_validate.maybe_use_model_local_python(
            ["python", "-c", "print(1)"], shell=False, cwd=model_dir
        )

        self.assertEqual(command, ["python", "-c", "print(1)"])


if __name__ == "__main__":
    unittest.main()
