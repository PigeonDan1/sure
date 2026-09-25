from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scaffold_adapter import io_contract_for


class SeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        template = Path(__file__).parent / "templates/validate.py"
        rendered = template.read_text().replace("__TASK_TYPE__", "SE").replace(
            "__IO_CONTRACT_JSON__", json.dumps(io_contract_for("se"))
        )
        script = self.root / "validate.py"
        script.write_text(rendered)
        spec = importlib.util.spec_from_file_location("se_validate", script)
        self.validator = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"SURE_VALIDATE_ARTIFACTS_DIR": str(self.root / "artifacts")}):
            spec.loader.exec_module(self.validator)

    def test_fixture_keeps_clean_reference_out_of_inference(self):
        fixture = self.root / "fixture/se"
        fixture.mkdir(parents=True)
        gt = fixture / "gt.jsonl"
        gt.write_text(json.dumps({"audio": "noisy.wav", "reference_audio": "clean.wav"}))
        with patch.dict(os.environ, {"SURE_VALIDATE_INPUT_JSON": ""}):
            self.assertEqual(self.validator.first_fixture_payload(), {"audio_path": str(fixture / "noisy.wav")})
            gt.write_text(json.dumps({"reference_audio": "clean.wav"}))
            with self.assertRaisesRegex(ValueError, "scoring-only"):
                self.validator.first_fixture_payload()

    def test_inference_writes_promotable_audio(self):
        class Wrapper:
            def predict(self, payload):
                path = Path(payload["output_path"])
                path.write_bytes(b"generated audio")
                return {"audio_path": str(path)}

        with patch.object(self.validator, "load_wrapper", return_value=Wrapper()), patch.object(
            self.validator, "first_fixture_payload", return_value={"audio_path": "noisy.wav"}
        ):
            self.assertTrue(self.validator.stage_infer())
        output = json.loads(self.validator.SAMPLE_OUTPUT.read_text())
        self.assertEqual(Path(output["audio_path"]).parent, self.root / "artifacts/outputs")
        self.assertTrue(self.validator.stage_contract())

    def test_contract_rejects_missing_empty_and_non_path_audio(self):
        empty = self.root / "empty.wav"
        empty.touch()
        for value in (str(self.root / "missing.wav"), str(empty), ["fake.wav"]):
            with self.subTest(value=value):
                self.assertTrue(self.validator.validate_contract({"audio_path": value}, io_contract_for("se")))

    def test_inference_rejects_output_outside_requested_path(self):
        class Wrapper:
            def predict(self, payload):
                return {"audio_path": payload["audio_path"]}

        with patch.object(self.validator, "load_wrapper", return_value=Wrapper()), patch.object(
            self.validator, "first_fixture_payload", return_value={"audio_path": "noisy.wav"}
        ):
            self.assertFalse(self.validator.stage_infer())
        result = json.loads(self.validator.result_path("infer").read_text())
        self.assertIn("requested output_path", result["error"])

    def test_rerun_rejects_stale_audio_and_symlink_output(self):
        output = self.root / "artifacts/outputs/enhanced.wav"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"stale audio")
        external = self.root / "external.wav"
        external.write_bytes(b"external audio")

        class NoWriteWrapper:
            def predict(self, payload):
                return {"audio_path": payload["output_path"]}

        class SymlinkWrapper:
            def predict(self, payload):
                Path(payload["output_path"]).symlink_to(external)
                return {"audio_path": payload["output_path"]}

        for wrapper in (NoWriteWrapper(), SymlinkWrapper()):
            with self.subTest(wrapper=type(wrapper).__name__), patch.object(
                self.validator, "load_wrapper", return_value=wrapper
            ), patch.object(self.validator, "first_fixture_payload", return_value={"audio_path": "noisy.wav"}):
                self.assertFalse(self.validator.stage_infer())
        self.assertEqual(external.read_bytes(), b"external audio")


if __name__ == "__main__":
    unittest.main()
