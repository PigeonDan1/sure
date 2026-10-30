#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_predictions_via_server as gp  # noqa: E402
from validate_prediction_files import validate_prediction_file  # noqa: E402
from sure.runtime.evaluation.task_registry import (
    allows_empty_prediction,
    canonical_tasks,
    capabilities,
    normalize_task,
)  # noqa: E402


DOA_FRAME = {
    "recording_id": "recording-1", "frame_index": 0, "timestamp": 0.0,
    "num_channels": 2, "coordinate_system": "array_local_spherical", "angle_unit": "degree",
    "sources": [],
}
DIARIZATION = [{"start": 0.0, "end": 1.0, "speaker": "speaker-1"}]
TRANSCRIPTION = [{"start": 0.0, "end": 1.0, "speaker": "speaker-1", "text": "hello"}]
TASK_CASES = {
    "asr": ({"text": "hello\nworld"}, "hello world", {"text": "hello world"}),
    "classification": ({"label": 0}, "0", {"label": "0"}),
    "doa": (DOA_FRAME, DOA_FRAME, DOA_FRAME),
    "gr": ({"label": 0}, "0", {"label": "0"}),
    "kws": (
        {"detected": False, "keyword": None, "score": 0},
        {"detected": False, "keyword": None, "score": 0.0},
        {"detected": False, "keyword": None, "score": 0.0},
    ),
    "lid": ({"label": "en"}, "en", {"label": "en", "language": "en"}),
    "s2tt": ({"translation": "translated text"}, "translated text", {"translation": "translated text", "text": "translated text"}),
    "sa_asr": ({"segments": TRANSCRIPTION}, TRANSCRIPTION, {"segments": TRANSCRIPTION}),
    "sd": ({"segments": DIARIZATION}, DIARIZATION, {"segments": DIARIZATION}),
    "se": ({"audio_path": "generated.wav"}, "generated.wav", {"audio_path": "generated.wav", "enhanced_audio": "generated.wav"}),
    "ser": ({"label": "happy"}, "happy", {"label": "happy"}),
    "slu": ({"answer": "activate_lights"}, "activate_lights", {"answer": "activate_lights", "text": "activate_lights"}),
    "sv": ({"embedding": [0, 0.5]}, [0, 0.5], {"embedding": [0, 0.5]}),
    "tse": ({"audio_path": "generated.wav"}, "generated.wav", {"audio_path": "generated.wav", "prediction_audio": "generated.wav"}),
    "tta": ({"audio_path": "generated.wav"}, "generated.wav", {"audio_path": "generated.wav", "prediction_audio": "generated.wav"}),
    "tts": ({"audio_path": "generated.wav", "sample_rate": 16000}, "generated.wav", {"audio_path": "generated.wav", "sample_rate": 16000}),
    "vad": ({"speech_segments": []}, {"speech_segments": []}, {"speech_segments": []}),
    "vc": ({"audio_path": "generated.wav"}, "generated.wav", {"audio_path": "generated.wav", "converted_audio": "generated.wav"}),
}


class TaskProjectionContractTests(unittest.TestCase):
    def _assert_case(self, task, payload, expected_projection, expected_prediction) -> None:
        original = copy.deepcopy(payload)
        projection, prediction = gp._normalize_prediction_payload(payload, task=task)
        self.assertEqual(payload, original, "projection must not mutate the server response")
        if isinstance(expected_projection, (dict, list)):
            self.assertEqual(json.loads(projection), expected_projection)
        else:
            self.assertEqual(projection, expected_projection)
        self.assertEqual(prediction, expected_prediction)

    def test_all_registered_tasks_project_flat_and_nested_predictions(self) -> None:
        self.assertEqual(set(TASK_CASES), set(canonical_tasks()))
        for task, (payload, projection, prediction) in TASK_CASES.items():
            for nested in (False, True):
                with self.subTest(task=task, nested=nested):
                    response = {"prediction": payload} if nested else payload
                    self._assert_case(task.upper() if nested else task, response, projection, prediction)

    def test_registered_aliases_use_the_canonical_task_projection(self) -> None:
        for alias in capabilities()["aliases"]:
            with self.subTest(alias=alias):
                payload, projection, prediction = TASK_CASES[normalize_task(alias)]
                self._assert_case(alias, payload, projection, prediction)

    def test_plain_text_uses_the_tasks_standard_field(self) -> None:
        for task, field in (("ASR", "text"), ("S2TT", "translation"), ("SLU", "answer")):
            with self.subTest(task=task):
                self._assert_case(task, "hello\nworld", "hello world", {field: "hello world", "text": "hello world"})


class AsrPayloadNormalizationTests(unittest.TestCase):
    def test_asr_allows_empty_prediction_but_other_tasks_do_not(self) -> None:
        self.assertTrue(allows_empty_prediction("asr"))
        self.assertFalse(allows_empty_prediction("classification"))

    def test_asr_validation_accepts_empty_prediction_with_complete_coverage(self) -> None:
        class Manager:
            def normalize_dataset_name(self, name):
                return name

            def get_jsonl_path(self, name):
                return reference_path

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_path = root / "reference.jsonl"
            reference_path.write_text(
                '{"key":"empty","task":"ASR"}\n{"key":"text","task":"ASR"}\n',
                encoding="utf-8",
            )
            prediction_path = root / "asr.txt"
            prediction_path.write_text("empty\t\ntext\thello\n", encoding="utf-8")
            (root / "asr.jsonl").write_text(
                '{"key":"empty","task":"ASR","prediction":{},"normalized_prediction":""}\n'
                '{"key":"text","task":"ASR","prediction":{"text":"hello"},"normalized_prediction":"hello"}\n',
                encoding="utf-8",
            )
            result = validate_prediction_file(Manager(), "asr", prediction_path)
            self.assertTrue(result["is_valid"])
            self.assertEqual(result["empty_prediction_keys"], ["empty"])

    def test_asr_strict_validation_rejects_empty_prediction(self) -> None:
        class Manager:
            def normalize_dataset_name(self, name):
                return name

            def get_jsonl_path(self, name):
                return reference_path

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_path = root / "reference.jsonl"
            reference_path.write_text('{"key":"empty","task":"ASR"}\n', encoding="utf-8")
            prediction_path = root / "asr.txt"
            prediction_path.write_text("empty\t\n", encoding="utf-8")
            (root / "asr.jsonl").write_text(
                '{"key":"empty","task":"ASR","prediction":{"text":""},"normalized_prediction":""}\n',
                encoding="utf-8",
            )
            result = validate_prediction_file(Manager(), "asr", prediction_path, require_nonempty=True)
            self.assertFalse(result["is_valid"])
            self.assertEqual(result["empty_prediction_keys"], ["empty"])

    def test_text_pair_validation_rejects_empty_prediction_unless_task_allows_it(self) -> None:
        class Manager:
            def normalize_dataset_name(self, name):
                return name

            def get_jsonl_path(self, name):
                return reference_path

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_path = root / "reference.jsonl"
            reference_path.write_text('{"key":"empty","task":"classification"}\n', encoding="utf-8")
            prediction_path = root / "classification.txt"
            prediction_path.write_text("empty\t\n", encoding="utf-8")
            (root / "classification.jsonl").write_text(
                '{"key":"empty","task":"classification","prediction":{"label":""},"normalized_prediction":""}\n',
                encoding="utf-8",
            )
            result = validate_prediction_file(Manager(), "classification", prediction_path)
            self.assertFalse(result["is_valid"])
            self.assertEqual(result["contract_violation_keys"], ["empty"])
            self.assertEqual(result["empty_prediction_keys"], ["empty"])

    def test_standard_text_fields_take_precedence_over_aliases(self) -> None:
        for task, field in (("S2TT", "translation"), ("SLU", "answer")):
            for nested in (False, True):
                with self.subTest(task=task, nested=nested):
                    output = {field: "canonical answer", "text": "alias answer"}
                    payload = {"prediction": output} if nested else output
                    projection, prediction = gp._normalize_prediction_payload(payload, task=task)
                    self.assertEqual(projection, "canonical answer")
                    self.assertEqual(prediction[field], "canonical answer")

    def test_standard_text_fields_work_without_aliases(self) -> None:
        for task, field in (("S2TT", "translation"), ("SLU", "answer")):
            with self.subTest(task=task):
                projection, prediction = gp._normalize_prediction_payload({field: "answer"}, task=task)
                self.assertEqual(projection, "answer")
                self.assertEqual(prediction[field], "answer")

    def test_doa_projection_preserves_canonical_frame_metadata(self) -> None:
        frame = {
            "recording_id": "recording-1",
            "frame_index": 2,
            "timestamp": 0.2,
            "num_channels": 4,
            "coordinate_system": "array_local_spherical",
            "angle_unit": "degree",
            "sources": [{"source_id": "speaker-1", "azimuth": 30.0, "activity": True}],
        }
        for payload in (frame, {"prediction": frame}):
            with self.subTest(nested="prediction" in payload):
                projection, prediction = gp._normalize_prediction_payload(payload, task="DOA")
                self.assertEqual(json.loads(projection), frame)
                self.assertEqual(prediction, frame)

    def test_sv_rejects_missing_empty_or_nonfinite_embeddings(self) -> None:
        for payload in ({"ok": True}, {"embedding": []}, {"embedding": "vector"},
                        {"embedding": [True]}, {"embedding": [float("nan")]},
                        {"embedding": [float("inf")]}, [0.1, 0.2]):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "SV.*embedding"):
                    gp._normalize_prediction_payload(payload, task="SV")

    def test_sv_accepts_a_finite_embedding_with_zero_values(self) -> None:
        projection, prediction = gp._normalize_prediction_payload({"embedding": [0, -0.5, 0.25]}, task="SV")
        self.assertEqual(json.loads(projection), [0, -0.5, 0.25])
        self.assertEqual(prediction, {"embedding": [0, -0.5, 0.25]})

    def test_doa_requires_an_explicit_sources_array(self) -> None:
        for payload in ({"ok": True}, {"sources": "speaker"}, "speaker"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "DOA.*sources"):
                    gp._normalize_prediction_payload(payload, task="DOA")

    def test_snapshot_writer_keeps_text_and_jsonl_on_the_same_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            txt = root / "predictions.txt"
            structured = root / "predictions.jsonl"
            gp._write_prediction_snapshots(
                samples=[{"key": "sample-1"}],
                prediction_path=txt,
                structured_prediction_path=structured,
                prediction_map={"sample-1": "first\nsecond"},
                structured_map={},
                canonical_dataset="demo__v1",
                sample_task="ASR",
                sample_language="en",
            )
            self.assertEqual(txt.read_text(encoding="utf-8"), "sample-1\tfirst second\n")
            row = json.loads(structured.read_text(encoding="utf-8"))
            self.assertEqual(row["normalized_prediction"], "first second")

    def test_physical_cuda_request_is_remapped_when_one_card_is_visible(self) -> None:
        with patch.dict(gp.os.environ, {"CUDA_VISIBLE_DEVICES": "3"}, clear=False):
            self.assertEqual(gp._process_device("cuda:3"), "cuda:0")

    def test_cuda_request_stays_indexed_when_multiple_cards_are_visible(self) -> None:
        with patch.dict(gp.os.environ, {"CUDA_VISIBLE_DEVICES": "2,3"}, clear=False):
            self.assertEqual(gp._process_device("cuda:1"), "cuda:1")

    def test_text_newlines_are_folded_to_spaces_for_single_line_projections(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload(
            {"text": "first\r\nsecond\nthird\rfourth"}, task="ASR"
        )
        self.assertEqual(prediction, "first second third fourth")
        self.assertEqual(normalized, {"text": "first second third fourth"})

    def test_audio_path_newlines_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "audio_path cannot contain newline"):
            gp._normalize_prediction_payload({"audio_path": "generated\nfile.wav"}, task="TTS")

    def test_structured_predictions_keep_json_newlines_escaped(self) -> None:
        projection, normalized = gp._normalize_prediction_payload(
            {"detected": True, "keyword": "wake\nword", "score": 0.9}, task="KWS"
        )
        self.assertNotIn("\n", projection)
        self.assertEqual(json.loads(projection)["keyword"], "wake\nword")
        self.assertEqual(normalized["keyword"], "wake\nword")

    def test_annotation_path_newlines_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "annotation_path cannot contain newline"):
            gp._normalize_prediction_payload({"annotation_path": "segments\n.json"}, task="SD")

    def test_resume_does_not_fold_a_structured_path_newline_into_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "key": "sample-1",
                        "task": "TTS",
                        "normalized_prediction": "generated\nfile.wav",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            structured = gp._load_existing_structured_predictions(path)
            self.assertEqual(structured["sample-1"]["normalized_prediction"], "generated\nfile.wav")
            self.assertNotIn(
                "sample-1",
                gp._resume_complete_keys({"sample-1": "generated file.wav"}, structured),
            )

    def test_resume_skips_only_matching_text_and_structured_rows(self) -> None:
        predictions = {"complete": "ok", "missing": "old", "mismatch": "text"}
        structured = {
            "complete": {"normalized_prediction": "ok"},
            "missing": {},
            "mismatch": {"normalized_prediction": "different"},
        }
        self.assertEqual(gp._resume_complete_keys(predictions, structured), {"complete"})

    def test_single_element_text_list_is_unwrapped(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload(
            {"text": [" 二零二二年冬奥会在北京举行"]}, task="ASR"
        )
        self.assertEqual(prediction, " 二零二二年冬奥会在北京举行")
        self.assertEqual(normalized, {"text": " 二零二二年冬奥会在北京举行"})

    def test_single_element_text_tuple_is_unwrapped(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload({"text": ("hello",)}, task="S2TT")
        self.assertEqual(prediction, "hello")
        self.assertEqual(normalized, {"translation": "hello", "text": "hello"})

    def test_nested_prediction_text_list_is_unwrapped(self) -> None:
        prediction, _ = gp._normalize_prediction_payload(
            {"prediction": {"text": ["nested"]}}, task="ASR"
        )
        self.assertEqual(prediction, "nested")

    def test_plain_string_text_is_untouched(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload({"text": "严浩出演的电影有什么"}, task="ASR")
        self.assertEqual(prediction, "严浩出演的电影有什么")
        self.assertEqual(normalized, {"text": "严浩出演的电影有什么"})

    def test_empty_text_list_stays_empty(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload({"text": []}, task="ASR")
        self.assertEqual(prediction, "")
        self.assertEqual(normalized, {"text": ""})

    def test_asr_requires_a_standard_prediction_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "must provide text"):
            gp._normalize_prediction_payload({"ok": True, "result": {"text": "Arabic"}}, task="ASR")

    def test_asr_accepts_explicit_empty_text(self) -> None:
        for payload in ({"text": ""}, {"prediction": {"text": ""}}):
            with self.subTest(payload=payload):
                prediction, normalized = gp._normalize_prediction_payload(payload, task="ASR")
                self.assertEqual(prediction, "")
                self.assertEqual(normalized, {"text": ""})

    def test_structured_task_payloads_keep_engine_fields(self) -> None:
        cases = [
            ({"label": "happy"}, "SER", {"label": "happy"}),
            ({"language": "zh"}, "LID", {"label": "zh", "language": "zh"}),
            (
                {"lang": "zh mandarin"},
                "LID",
                {"label": "zh mandarin", "language": "zh mandarin"},
            ),
            ({"text": "activate_lights"}, "SLU", {"answer": "activate_lights", "text": "activate_lights"}),
            ({"detected": False, "score": 0.1}, "KWS", {"detected": False, "keyword": None, "score": 0.1}),
            ({"speech_segments": [{"start": 0.5, "end": 1.0}]}, "VAD", {"speech_segments": [{"start": 0.5, "end": 1.0}]}),
            (
                {"timestamps": [[0.5, 1], [1.25, 2.0]]},
                "VAD",
                {
                    "speech_segments": [
                        {"start": 0.5, "end": 1.0},
                        {"start": 1.25, "end": 2.0},
                    ]
                },
            ),
            ({"embedding": [0.1, 0.2]}, "SV", {"embedding": [0.1, 0.2]}),
        ]
        for payload, task, expected in cases:
            with self.subTest(task=task):
                _projection, normalized = gp._normalize_prediction_payload(payload, task=task)
                self.assertEqual(normalized, expected)

    def test_class_index_zero_is_written_as_a_prediction(self) -> None:
        # Class 0 is the first class of every binary task; dropping it as
        # "no prediction" scores every correct class-0 answer wrong.
        for task in ("CLASSIFICATION", "SER", "GR", "SLU"):
            with self.subTest(task=task):
                prediction, _normalized = gp._normalize_prediction_payload({"label": 0}, task=task)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    txt = root / "predictions.txt"
                    gp._write_prediction_snapshots(
                        samples=[{"key": "sample-1"}],
                        prediction_path=txt,
                        structured_prediction_path=root / "predictions.jsonl",
                        prediction_map={"sample-1": prediction},
                        structured_map={},
                        canonical_dataset="demo__v1",
                        sample_task=task,
                        sample_language="en",
                    )
                    self.assertEqual(txt.read_text(encoding="utf-8"), "sample-1\t0\n")

    def test_audio_task_payloads_use_task_specific_engine_fields(self) -> None:
        cases = [
            ("SE", "enhanced_audio"),
            ("TSE", "prediction_audio"),
            ("TTA", "prediction_audio"),
            ("VC", "converted_audio"),
        ]
        for task, field in cases:
            with self.subTest(task=task):
                prediction, normalized = gp._normalize_prediction_payload("generated.wav", task=task)
                self.assertEqual(prediction, "generated.wav")
                self.assertEqual(normalized["audio_path"], "generated.wav")
                self.assertEqual(normalized[field], "generated.wav")


_ECHO_SERVER = """
import json, sys

while True:
    line = sys.stdin.readline()
    if not line:
        break
    request = json.loads(line)
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": request["params"]}, ensure_ascii=False)
        + "\\n"
    )
    sys.stdout.flush()
"""


_RAW_JSONL_SERVER = """
import json, sys

for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    print(json.dumps({"speech_segments": [], "audio_path": request["audio_path"]}), flush=True)
"""


class ServerProtocolTests(unittest.TestCase):
    def test_missing_server_defaults_to_mcp(self) -> None:
        self.assertEqual(gp.resolve_server_protocol({}), gp.MCP_SERVER_PROTOCOL)
        self.assertEqual(gp.resolve_server_protocol(None), gp.MCP_SERVER_PROTOCOL)

    def test_bare_stdio_transport_defaults_to_mcp(self) -> None:
        # transport is the channel, not the framing: an unadorned stdio
        # server is MCP JSON-RPC unless it declares a jsonl protocol.
        self.assertEqual(
            gp.resolve_server_protocol({"transport": "stdio"}),
            gp.MCP_SERVER_PROTOCOL,
        )

    def test_explicit_jsonl_protocol_selects_jsonl(self) -> None:
        self.assertEqual(
            gp.resolve_server_protocol({"protocol": "jsonl"}),
            gp.JSONL_SERVER_PROTOCOL,
        )
        self.assertEqual(
            gp.resolve_server_protocol({"transport": "stdio", "protocol": "json_lines"}),
            gp.JSONL_SERVER_PROTOCOL,
        )

    def test_explicit_mcp_protocol_selects_jsonrpc(self) -> None:
        self.assertEqual(
            gp.resolve_server_protocol({"protocol": "mcp"}),
            gp.MCP_SERVER_PROTOCOL,
        )
        self.assertEqual(
            gp.resolve_server_protocol({"transport": "stdio", "protocol": "mcp"}),
            gp.MCP_SERVER_PROTOCOL,
        )

    def test_unknown_protocol_raises(self) -> None:
        with self.assertRaises(ValueError):
            gp.resolve_server_protocol({"protocol": "carrier_pigeon"})

    def test_raw_jsonl_request_round_trips_without_jsonrpc_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with open(root / "server.log", "w", encoding="utf-8") as log_handle:
                with gp._start_model_server(
                    [sys.executable, "-c", _RAW_JSONL_SERVER],
                    working_dir=root,
                    env=dict(os.environ),
                    log_handle=log_handle,
                ) as process:
                    response = gp._send_jsonl_request(
                        process,
                        {"audio_path": "/tmp/sample.wav"},
                    )
                    process.stdin.close()
                    process.wait(timeout=5)
        self.assertEqual(response, {"speech_segments": [], "audio_path": "/tmp/sample.wav"})


def _locale_text_pipes(encoding: str):
    """Pretend the host code page is `encoding` for every text pipe that names none.

    That is what `text=True` does on a non-UTF-8 Windows box, and forcing it
    here keeps the regression provable on a UTF-8 host too.
    """
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
            kwargs["encoding"] = encoding
        return real_popen(*args, **kwargs)

    return mock.patch("subprocess.Popen", popen)


class GenerationStatusDurabilityTests(unittest.TestCase):
    """prediction_generation_status.json is the only record of which datasets are already done."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "prediction_generation_status.json"
        self.addCleanup(self.tmp.cleanup)

    def test_completed_datasets_survive_a_write_that_dies_midway(self) -> None:
        # A lone surrogate is what a model server sends back as a response key when its own
        # decoder is broken; it reaches the payload through generation.observed_raw_response and
        # only fails once the text is being encoded, i.e. after the file is already open.
        done = {"datasets": [{"dataset": "done-1", "status": "completed"}]}
        gp._write_status_file(self.path, done)
        recorded = self.path.read_text(encoding="utf-8")
        with self.assertRaises(UnicodeEncodeError):
            gp._write_status_file(self.path, {"datasets": [{"dataset": "done-2", "key": "x\ud800"}]})
        self.assertEqual(self.path.read_text(encoding="utf-8"), recorded)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])

    def test_a_truncated_status_file_stops_the_run_instead_of_resetting_it(self) -> None:
        self.path.write_text('{"datasets": [{"dataset": "finished-1"}, {"dataset": "fin', encoding="utf-8")
        default = {"schema": "sure.eval.prediction_generation_status.v2", "datasets": []}
        try:
            payload, _current = gp._upsert_dataset_status(self.path, default, {"dataset": "next-one"})
        except ValueError as exc:
            self.assertIn(str(self.path), str(exc))
            return
        self.fail(
            "a truncated status file was silently reset: the file recorded "
            "['finished-1', 'fin...'] and the returned payload keeps "
            f"{[row.get('dataset') for row in payload['datasets']]}"
        )


class GenerationResumeTests(unittest.TestCase):
    DATASET = "demo__v1__asr"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        (self.model_dir / "config.yaml").write_text(
            "model:\n  name: demo\n  task: ASR\ntools:\n  - name: asr\n"
            "    input_schema:\n      type: object\n      properties:\n"
            "        audio_path:\n          type: string\n",
            encoding="utf-8",
        )
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.reference = self.root / "dataset.jsonl"
        samples = []
        for index in range(30):
            audio = self.root / f"audio-{index}.wav"
            audio.touch()
            samples.append({"key": f"key-{index}", "path": str(audio), "task": "ASR", "language": "ar"})
        self.reference.write_text(
            "".join(json.dumps(sample) + "\n" for sample in samples), encoding="utf-8"
        )
        self.predictions_dir = self.run_dir / "predictions"
        self.predictions_dir.mkdir()
        self.prediction_path = self.predictions_dir / f"{self.DATASET}.txt"
        self.structured_path = self.predictions_dir / f"{self.DATASET}.jsonl"
        self.result_log = self.predictions_dir / "logs" / f"{self.DATASET}_results.log"
        self.called: list[str] = []
        self.snapshots: list[int] = []
        self.status_counts: list[int] = []

    def run_generation(self, *, max_samples: int = 0, interrupt_at: str | None = None) -> None:
        manager = mock.Mock()
        manager.expand_dataset_names.return_value = [self.DATASET]
        manager.normalize_dataset_name.return_value = self.DATASET
        manager.get_jsonl_path.return_value = self.reference
        inventory = {"policy": {"eval_runtime": "python"}, "model_runtime": {"tool_names": ["asr"]}}
        process = mock.Mock()
        actual_snapshot = gp._write_prediction_snapshots
        actual_status_write = gp._write_status_file

        def respond(_process, request):
            if request["method"] != "tools/call":
                return {"result": {}}
            audio = Path(request["params"]["arguments"]["audio_path"]).stem
            self.called.append(audio)
            if audio == interrupt_at:
                raise KeyboardInterrupt()
            return {"result": {"text": "" if audio == "audio-0" else f"text-{audio}"}}

        def snapshot(**kwargs):
            self.snapshots.append(len(kwargs["prediction_map"]))
            actual_snapshot(**kwargs)

        def write_status(path, payload):
            row = (payload.get("datasets") or [{}])[-1]
            if "num_generated_samples" in row:
                # Only the dataset status carries the counter; the manifests do not.
                self.status_counts.append(row["num_generated_samples"])
            actual_status_write(path, payload)

        argv = [
            "generate_predictions_via_server.py", "--model-dir", str(self.model_dir),
            "--dataset", self.DATASET, "--run-dir", str(self.run_dir), "--resume",
        ]
        if max_samples:
            argv += ["--max-samples", str(max_samples)]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(gp.Config, "from_env", return_value=object()),
            mock.patch.object(gp, "DatasetManager", return_value=manager),
            mock.patch.object(gp, "_load_runtime_inventory", return_value=inventory),
            mock.patch.object(gp, "_resolve_server_command", return_value=[sys.executable, "server.py"]),
            mock.patch.object(gp, "_resolve_working_dir", return_value=self.model_dir),
            mock.patch.object(gp, "_resolve_protocol_parameters", return_value={"model_params": {}}),
            mock.patch.object(gp, "model_child_env", return_value={}),
            mock.patch.object(gp, "_start_model_server", return_value=process),
            mock.patch.object(gp, "_send_request", side_effect=respond),
            mock.patch.object(gp, "_extract_response_payload", side_effect=lambda response: response["result"]),
            mock.patch.object(gp, "_write_prediction_snapshots", side_effect=snapshot),
            mock.patch.object(gp, "_write_status_file", side_effect=write_status),
        ):
            gp.main()

    def test_blank_template_is_not_counted_as_inference_or_written_to_result_log(self) -> None:
        self.prediction_path.write_text(
            "".join(f"key-{index}\t\n" for index in range(30)), encoding="utf-8"
        )
        self.run_generation(max_samples=3)
        self.assertEqual(self.status_counts[0], 0)
        self.assertEqual(self.result_log.read_text(encoding="utf-8").count("\n"), 3)
        self.assertEqual(self.prediction_path.read_text(encoding="utf-8").count("\n"), 3)
        self.assertEqual(json.loads(self.structured_path.read_text(encoding="utf-8").splitlines()[0])["normalized_prediction"], "")

        self.status_counts.clear()
        self.snapshots.clear()
        self.run_generation()
        self.assertEqual(self.status_counts[0], 3)
        self.assertEqual(self.snapshots, [25, 30])
        self.assertEqual(self.result_log.read_text(encoding="utf-8").count("\n"), 30)
        self.assertEqual(len(self.called), 30)
        self.assertEqual(self.status_counts[-1], 30)
        self.assertEqual(self.prediction_path.read_text(encoding="utf-8").count("\n"), 30)

    def test_interruption_resumes_from_matching_snapshot_including_empty_text(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            self.run_generation(interrupt_at="audio-27")
        self.assertEqual(self.snapshots, [25])
        self.assertEqual(self.structured_path.read_text(encoding="utf-8").count("\n"), 25)
        self.assertEqual(self.result_log.read_text(encoding="utf-8").count("\n"), 27)

        self.snapshots.clear()
        self.status_counts.clear()
        self.run_generation()
        self.assertEqual(self.status_counts[0], 25)
        self.assertEqual(self.snapshots, [30])
        self.assertEqual(self.called.count("audio-0"), 1)
        self.assertEqual(self.called.count("audio-26"), 2)
        self.assertEqual(self.result_log.read_text(encoding="utf-8").count("\n"), 30)
        status = json.loads((self.run_dir / "prediction_generation_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["datasets"][0]["empty_prediction_count"], 1)


class ServerPipeEncodingTests(unittest.TestCase):
    """Both ends of the MCP stdio bridge serialise with ensure_ascii=False."""

    def test_a_non_ascii_request_round_trips_under_a_non_utf8_host_encoding(self) -> None:
        text = "今日は naïve café"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with open(root / "server.log", "w", encoding="utf-8") as log_handle:
                with mock.patch.dict(os.environ, {"PYTHONIOENCODING": "ascii"}), _locale_text_pipes("ascii"):
                    with gp._start_model_server(
                        [sys.executable, "-c", _ECHO_SERVER],
                        working_dir=root,
                        env=dict(os.environ),
                        log_handle=log_handle,
                    ) as process:
                        try:
                            response = gp._send_request(
                                process,
                                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"text": text}},
                            )
                        finally:
                            process.stdin.close()
        self.assertEqual(response["result"]["text"], text)


if __name__ == "__main__":
    unittest.main()
