"""Regression checks for generated JSONL servers and package readiness."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from write_package_gate import validate_jsonl_server_smoke


SCRIPT_DIR = Path(__file__).resolve().parent


class JsonlServerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.run_dir = self.root / "run"
        (self.run_dir / "artifacts").mkdir(parents=True)
        (self.model_dir / "config.yaml").write_text(
            "task: ASR\nserver:\n  command: [python, server.py]\n  protocol: jsonl\n",
            encoding="utf-8",
        )

    def write_model(self) -> None:
        (self.model_dir / "model.py").write_text(
            "class ModelWrapper:\n"
            "    def load(self):\n        pass\n"
            "    def predict(self, payload):\n"
            "        if 'audio_path' not in payload:\n            raise ValueError('missing audio_path')\n"
            "        return {'text': 'نص عربي', 'language': 'ar'}\n",
            encoding="utf-8",
        )

    def run_smoke(self) -> dict:
        """Drive the declared server once and return the evidence it wrote."""
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_DIR / "run_server_smoke.py"),
                "--server-command", json.dumps([sys.executable, "server.py"]),
                "--protocol", "jsonl", "--tool", "transcribe", "--task", "ASR",
                "--arguments-json", json.dumps({"audio_path": "fixture.wav"}),
                "--cwd", str(self.model_dir), "--timeout", "10",
                "--produces", str(self.run_dir / "artifacts" / "server_smoke.json"),
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.run_dir / "artifacts" / "server_smoke.json").read_text(encoding="utf-8"))

    def bind_to_current_server(self, smoke: dict) -> dict:
        """Stamp a synthetic receipt with the server file the bundle ships now."""
        server = self.model_dir / "server.py"
        server.write_text("# a server that answers\n", encoding="utf-8")
        smoke["server_entry_path"] = str(server.resolve())
        smoke["server_entry_sha256"] = hashlib.sha256(server.read_bytes()).hexdigest()
        return smoke

    def test_template_emits_flat_asr_prediction(self) -> None:
        shutil.copyfile(SCRIPT_DIR / "templates" / "server_jsonl.py", self.model_dir / "server.py")
        self.write_model()
        result = subprocess.run(
            [sys.executable, "server.py"],
            cwd=self.model_dir,
            input=json.dumps({"audio_path": "fixture.wav"}) + "\n",
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"text": "نص عربي", "language": "ar"})

        smoke = self.run_smoke()
        self.assertEqual(smoke["status"], "passed")
        validate_jsonl_server_smoke(self.run_dir, self.model_dir)

        failure = subprocess.run(
            [sys.executable, "server.py"], cwd=self.model_dir,
            input="{}\n", capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(failure.returncode, 0)
        self.assertEqual(failure.stdout, "")
        self.assertIn("missing audio_path", failure.stderr)

    def test_package_rejects_missing_and_legacy_jsonl_smoke(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires server_smoke.json"):
            validate_jsonl_server_smoke(self.run_dir, self.model_dir)

        smoke_path = self.run_dir / "artifacts" / "server_smoke.json"
        smoke = {
            "status": "passed", "protocol": "jsonl", "list_only": False,
            "server_command": [sys.executable, "server.py"],
            "steps": {"single_request": {
                "ok": True, "primary_output": '{"ok":true,"result":{"text":"نص"}}',
            }},
        }
        smoke_path.write_text(json.dumps(smoke), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "projected prediction"):
            validate_jsonl_server_smoke(self.run_dir, self.model_dir)

        smoke["steps"]["single_request"].update(prediction={"text": "نص"}, projection="نص")
        smoke_path.write_text(json.dumps(smoke), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "which server file it ran"):
            validate_jsonl_server_smoke(self.run_dir, self.model_dir)

        self.bind_to_current_server(smoke)
        smoke_path.write_text(json.dumps(smoke), encoding="utf-8")
        validate_jsonl_server_smoke(self.run_dir, self.model_dir)

        smoke["server_command"][1] = "other_server.py"
        smoke_path.write_text(json.dumps(smoke), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "projected prediction"):
            validate_jsonl_server_smoke(self.run_dir, self.model_dir)

    def test_package_rejects_a_smoke_from_a_server_the_bundle_no_longer_ships(self) -> None:
        shutil.copyfile(SCRIPT_DIR / "templates" / "server_jsonl.py", self.model_dir / "server.py")
        self.write_model()
        self.run_smoke()
        validate_jsonl_server_smoke(self.run_dir, self.model_dir)

        # The served file changed after the receipt was written: the receipt
        # describes a server the bundle no longer ships.
        (self.model_dir / "server.py").write_text(
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    if line.strip():\n"
            "        sys.stdout.write(json.dumps({'ok': True, 'result': {'text': 'x'}}) + '\\n')\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "different server file"):
            validate_jsonl_server_smoke(self.run_dir, self.model_dir)

    def test_mcp_does_not_require_jsonl_smoke(self) -> None:
        config = self.model_dir / "config.yaml"
        config.write_text(config.read_text(encoding="utf-8").replace("jsonl", "mcp"), encoding="utf-8")
        validate_jsonl_server_smoke(self.run_dir, self.model_dir)


if __name__ == "__main__":
    unittest.main()
