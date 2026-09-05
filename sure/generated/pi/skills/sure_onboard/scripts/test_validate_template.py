import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


TEMPLATE = Path(__file__).parent / "templates" / "validate.py"
STAGE_MODEL_ARTIFACTS = Path(__file__).parent / "stage_model_artifacts.py"


def load_template():
    spec = importlib.util.spec_from_file_location("sure_onboard_validate_template", TEMPLATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load validate template")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_stage_model_artifacts():
    spec = importlib.util.spec_from_file_location("sure_onboard_stage_model_artifacts", STAGE_MODEL_ARTIFACTS)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load stage_model_artifacts")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidateTemplateTest(unittest.TestCase):
    def setUp(self):
        self.module = load_template()
        self.temp_dir = tempfile.TemporaryDirectory()
        artifacts = Path(self.temp_dir.name) / "artifacts"
        self.module.ARTIFACTS_DIR = artifacts
        self.module.VALIDATION_LOG = artifacts / "validation.log"
        self.module.SAMPLE_OUTPUT = artifacts / "sample_output.json"
        self.module.SAMPLE_OUTPUTS = artifacts / "sample_outputs.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_json_preserves_arabic_text(self):
        path = self.module.ARTIFACTS_DIR / "arabic.json"
        self.module.write_json(path, {"text": "مرحبا بالعالم"})
        self.assertIn("مرحبا بالعالم", path.read_text(encoding="utf-8"))

    def test_output_summary_is_complete_json(self):
        outputs = [{"text": "مرحبا " * 200} for _ in range(3)]
        summary = self.module.output_summary(outputs)
        parsed = json.loads(summary)
        self.assertEqual(parsed["sample_count"], 3)
        self.assertIsInstance(parsed["first_output"], dict)

    def test_infer_runs_every_fixture_and_writes_jsonl(self):
        fixtures = [
            {
                "input": {"audio_path": f"sample_{index}.wav", "language": "ar"},
                "fixture": {
                    "key": f"key-{index}",
                    "audio": f"sample_{index}.wav",
                    "dataset": "arabic-test",
                    "ground_truth": f"مرجع {index}",
                },
            }
            for index in range(1, 4)
        ]
        calls = []
        self.module.load_wrapper = lambda: object()
        self.module.fixture_payloads = lambda: fixtures

        def predict(_wrapper, payload):
            calls.append(payload)
            return {"text": f"نص {len(calls)}", "language": "ar"}

        self.module.run_predict = predict
        self.assertTrue(self.module.stage_infer())
        self.assertEqual(calls, [fixture["input"] for fixture in fixtures])
        rows = [json.loads(line) for line in self.module.SAMPLE_OUTPUTS.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["key"], "key-3")
        self.assertEqual(rows[2]["ground_truth"], "مرجع 3")
        self.assertEqual(rows[2]["dataset"], "arabic-test")
        self.assertEqual(rows[2]["output"]["text"], "نص 3")
        self.assertEqual(json.loads(self.module.SAMPLE_OUTPUT.read_text(encoding="utf-8"))["text"], "نص 1")
        infer_result = json.loads((self.module.ARTIFACTS_DIR / "infer_result.json").read_text(encoding="utf-8"))
        self.assertEqual(infer_result["sample_outputs_path"], "artifacts/sample_outputs.jsonl")

    def test_fixture_payloads_use_first_selected_set_and_preserve_metadata(self):
        model_dir = Path(self.temp_dir.name) / "model"
        first = model_dir / "fixture" / "asr" / "a-selected"
        second = model_dir / "fixture" / "asr" / "z-other"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "one.wav").write_bytes(b"wav")
        (second / "other.wav").write_bytes(b"wav")
        (first / "gt.jsonl").write_text(
            json.dumps(
                {
                    "key": "arabic-1",
                    "audio": "one.wav",
                    "language": "ar",
                    "dataset": "arabic-test",
                    "ground_truth": "النص المرجعي",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (second / "gt.jsonl").write_text(
            json.dumps({"key": "other", "audio": "other.wav", "ground_truth": "other"}) + "\n",
            encoding="utf-8",
        )
        self.module.MODEL_DIR = model_dir
        fixtures = self.module.fixture_payloads()
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0]["fixture"]["key"], "arabic-1")
        self.assertEqual(fixtures[0]["fixture"]["ground_truth"], "النص المرجعي")

    def test_sample_outputs_jsonl_is_staged(self):
        stage_model_artifacts = load_stage_model_artifacts()
        self.assertIn("sample_outputs.jsonl", stage_model_artifacts.OPTIONAL_RUN_ARTIFACTS)


if __name__ == "__main__":
    unittest.main()
