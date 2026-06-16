from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "src" / "sure_eval" / "models" / "asr_kimi_audio"


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, MODEL_DIR / relative_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_choice_rejects_transcription_sentences() -> None:
    validate_multitask = load_module("kimi_validate_multitask", "validate_multitask.py")

    assert validate_multitask.extract_choice("At an art workshop, a teacher says hello.") == ""
    assert validate_multitask.extract_choice("We will leave no stone unturned.") == ""


def test_extract_choice_accepts_explicit_answer_formats() -> None:
    validate_multitask = load_module("kimi_validate_multitask", "validate_multitask.py")

    assert validate_multitask.extract_choice("B") == "B"
    assert validate_multitask.extract_choice("B<|im_msg_end|>") == "B"
    assert validate_multitask.extract_choice("答案：C") == "C"
    assert validate_multitask.extract_choice("Answer: d.") == "D"
    assert validate_multitask.extract_choice("!B") == "B"
    assert validate_multitask.extract_choice("！C") == "C"


def test_build_slu_prompt_keeps_slu_audio_only_even_with_metadata() -> None:
    validate_multitask = load_module("kimi_validate_multitask_slu_prompt", "validate_multitask.py")

    base_prompt = "请听音频中的题目并作答。"
    prompt = validate_multitask.build_slu_prompt(
        {
            "prompt": base_prompt,
            "question": "这句话表达的成语含义是什么？",
            "choice_a": "选项甲",
            "choice_b": "选项乙",
            "choice_c": "选项丙",
            "choice_d": "选项丁",
        }
    )

    assert prompt == base_prompt


def test_build_slu_prompt_preserves_audio_only_fixture() -> None:
    validate_multitask = load_module("kimi_validate_multitask_slu_audio_only", "validate_multitask.py")

    prompt = "请听音频中的题目并作答。如果题目是选择题，只输出 A、B、C 或 D。"

    assert validate_multitask.build_slu_prompt({"prompt": prompt}) == prompt


def test_load_slu_metadata_indexes_by_key(tmp_path) -> None:
    validate_multitask = load_module("kimi_validate_multitask_slu_metadata", "validate_multitask.py")
    metadata_path = tmp_path / "mmsu_metadata.json"
    metadata_path.write_text(
        '[{"key":"sample-key","question":"Question text","choice_a":"A text"}]',
        encoding="utf-8",
    )
    validate_multitask.SLU_METADATA_PATH = metadata_path

    metadata = validate_multitask.load_slu_metadata()

    assert metadata["sample-key"]["question"] == "Question text"
    assert metadata["sample-key"]["choice_a"] == "A text"


def test_load_cases_slu_does_not_merge_external_metadata(tmp_path) -> None:
    validate_multitask = load_module("kimi_validate_multitask_slu_audio_only_cases", "validate_multitask.py")
    metadata_path = tmp_path / "mmsu_metadata.json"
    metadata_path.write_text(
        '[{"key":"idiom_reasoning_a390d080-34d0-43c5-8c7f-d847be0aaac9",'
        '"question":"Question text","choice_a":"A text"}]',
        encoding="utf-8",
    )
    validate_multitask.SLU_METADATA_PATH = metadata_path

    cases = validate_multitask.load_cases("SLU")

    matching_case = next(case for case in cases if case["key"].startswith("idiom_reasoning"))
    assert "question" not in matching_case
    assert "choice_a" not in matching_case


def test_main_accepts_task_subset(monkeypatch) -> None:
    validate_multitask = load_module("kimi_validate_multitask_task_subset", "validate_multitask.py")

    class Wrapper:
        def load(self):
            return None

        def healthcheck(self):
            return {"status": "ready"}

        def _resolve_model_path(self):
            return "/tmp/model"

    called = []

    def fake_task(wrapper, log_lines):
        called.append(log_lines)
        return {"status": "COMPLETE", "num_samples": 0, "metrics": {}, "outputs": []}

    monkeypatch.setenv("KIMI_AUDIO_VALIDATE_TASKS", "ASR,GR")
    monkeypatch.setattr(validate_multitask, "ModelWrapper", Wrapper)
    monkeypatch.setattr(validate_multitask, "run_asr", fake_task)
    monkeypatch.setattr(validate_multitask, "run_gr", fake_task)
    monkeypatch.setattr(validate_multitask, "ARTIFACTS", tmp_path)

    assert validate_multitask.main() == 0
    assert len(called) == 2


def test_main_completes_when_task_metrics_have_mismatches(monkeypatch, tmp_path) -> None:
    validate_multitask = load_module("kimi_validate_multitask_model_mismatch", "validate_multitask.py")

    class Wrapper:
        def load(self):
            return None

        def healthcheck(self):
            return {"status": "ready"}

        def _resolve_model_path(self):
            return "/tmp/model"

    def task_with_mismatch(wrapper, log_lines):
        log_lines.append("slu: MISMATCH key=sample expected=B got=C")
        return {"status": "COMPLETE", "num_samples": 1, "metrics": {"accuracy": 0.0}, "outputs": []}

    monkeypatch.setenv("KIMI_AUDIO_VALIDATE_TASKS", "SLU")
    monkeypatch.setattr(validate_multitask, "ModelWrapper", Wrapper)
    monkeypatch.setattr(validate_multitask, "run_slu", task_with_mismatch)
    monkeypatch.setattr(validate_multitask, "ARTIFACTS", tmp_path)

    assert validate_multitask.main() == 0
    output = (tmp_path / "multitask_sample_output.json").read_text(encoding="utf-8")
    assert '"status": "COMPLETE"' in output
    assert '"accuracy": 0.0' in output
    log = (tmp_path / "validation_multitask.log").read_text(encoding="utf-8")
    assert "MISMATCH" in log
    assert "FAIL" not in log


def test_run_slu_logs_mismatches_without_failing(monkeypatch, tmp_path) -> None:
    validate_multitask = load_module("kimi_validate_multitask_slu_mismatch", "validate_multitask.py")

    class Result:
        def __init__(self, text: str, label: str) -> None:
            self.text = text
            self.label = label

        def to_dict(self):
            return {"text": self.text, "task": "SLU", "label": self.label, "raw": {}}

    class Wrapper:
        def understand(self, audio_path: str, prompt: str | None = None):
            return Result("C", "C")

    monkeypatch.setattr(validate_multitask, "ARTIFACTS", tmp_path)
    log_lines: list[str] = []

    result = validate_multitask.run_slu(Wrapper(), log_lines)

    assert result["status"] == "COMPLETE"
    assert result["metrics"]["accuracy"] < 1.0
    assert any("MISMATCH" in line for line in log_lines)
    assert not any("FAIL" in line for line in log_lines)


def test_target_language_instruction_uses_requested_language() -> None:
    model = load_module("kimi_model", "model.py")

    instruction = model.build_translation_instruction(
        transcript="hello world",
        source_language="en",
        target_language="fr",
    )

    assert "French" in instruction
    assert "hello world" in instruction
    assert "Chinese" not in instruction


def test_clean_generated_text_removes_kimi_tail() -> None:
    model = load_module("kimi_model", "model.py")

    text = "罗彻斯特和简坠入爱河。<|im_msg_end|>\nYou are an AI assistant."

    assert model.clean_generated_text(text) == "罗彻斯特和简坠入爱河。"


def test_load_cases_asr_reads_only_asr_fixture() -> None:
    validate_multitask = load_module("kimi_validate_multitask_asr_cases", "validate_multitask.py")

    cases = validate_multitask.load_cases("ASR")

    assert cases
    assert {case["task"] for case in cases} == {"ASR"}
    assert {case["fixture"] for case in cases} == {"fixture/asr/aishell1-test"}


def test_asr_text_normalization_ignores_spaces_and_case() -> None:
    validate_multitask = load_module("kimi_validate_multitask_asr_text", "validate_multitask.py")

    reference = validate_multitask.normalize_text("今 天 Air")
    prediction = validate_multitask.normalize_text("今天 air")

    assert reference == prediction
    assert validate_multitask.edit_distance(reference, prediction) == 0


def test_run_gr_scores_ground_truth_labels() -> None:
    validate_multitask = load_module("kimi_validate_multitask_gr", "validate_multitask.py")

    class Result:
        def to_dict(self):
            return {"text": "female", "label": "female"}

    class Wrapper:
        def recognize_gender(self, audio_path: str):
            return Result()

    result = validate_multitask.run_gr(Wrapper(), [])

    assert result["status"] == "COMPLETE"
    assert result["metrics"]["accuracy"] == 1.0
    assert "contract smoke only" not in result["metrics"].get("note", "")


def test_understand_uses_direct_audio_not_asr_transcript() -> None:
    model = load_module("kimi_model_slu_direct", "model.py")

    class FakeKimiAudio:
        def __init__(self) -> None:
            self.messages = None

        def generate(self, messages, **kwargs):
            self.messages = messages
            return None, "B"

    wrapper = model.ModelWrapper()
    fake_model = FakeKimiAudio()
    wrapper._model = fake_model

    def fail_predict(input_data: str):
        raise AssertionError("SLU should not call ASR predict before understanding audio")

    wrapper.predict = fail_predict

    result = wrapper.understand("/tmp/example.wav", prompt="Answer with A, B, C, or D.")

    assert result.label == "B"
    assert result.raw["stage"] == "direct_audio_understand"
    assert fake_model.messages is not None
    assert any(message["message_type"] == "audio" for message in fake_model.messages)
