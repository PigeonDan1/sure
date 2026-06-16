from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xforge_sure_bridge.catalog import XForgeCatalog
from xforge_sure_bridge.modelscope_daily import task_match_score
from xforge_sure_bridge.modelscope_watcher import ModelScopeWatcher, _extract_items, _normalize_candidate, process_candidates


class FakeOpenAPIWatcher(ModelScopeWatcher):
    def __init__(self) -> None:
        super().__init__(api_base="https://modelscope.cn/openapi/v1")
        self.requests: list[tuple[str, dict[str, object]]] = []

    def _request_json(self, endpoint: str, params: dict[str, object]) -> object:
        self.requests.append((endpoint, dict(params)))
        if params["page_number"] == 1:
            return {
                "data": {
                    "models": [
                        {
                            "id": "iic/demo-asr-openapi",
                            "display_name": "Demo ASR OpenAPI",
                            "downloads": 42,
                            "tasks": ["automatic-speech-recognition"],
                            "last_modified": "2099-01-01T00:00:00Z",
                            "tags": ["task:automatic-speech-recognition"],
                        },
                        {
                            "id": "iic/demo-vision-openapi",
                            "display_name": "Demo Vision",
                            "downloads": 100,
                            "tasks": ["image-text-to-text"],
                            "last_modified": "2099-01-01T00:00:00Z",
                        },
                    ]
                }
            }
        return {"data": {"models": []}}


class XForgeModelScopeWatcherTest(unittest.TestCase):
    def test_asr_openapi_search_uses_modelscope_task_page_semantics(self) -> None:
        class FakeASRWatcher(ModelScopeWatcher):
            def __init__(self) -> None:
                super().__init__(api_base="https://modelscope.cn/openapi/v1")
                self.requests: list[tuple[str, dict[str, object]]] = []

            def _request_json(self, endpoint: str, params: dict[str, object]) -> object:
                self.requests.append((endpoint, dict(params)))
                if params["page_number"] != 1:
                    return {"data": {"datasets": []}}
                return {
                    "data": {
                        "datasets": [
                            {
                                "id": "lukeewin01/HeNan-Dialect-Datasets",
                                "display_name": "河南方言数据集500小时",
                                "downloads": 112,
                                "tasks": ["auto-speech-recognition"],
                                "last_modified": "2099-01-01T00:00:00Z",
                                "tags": ["task:auto-speech-recognition", "custom_tag:ASR数据集"],
                            }
                        ]
                    }
                }

        watcher = FakeASRWatcher()

        candidates = watcher.search(task="asr", resource_types=["dataset"], since_days=36500, max_items=5)

        self.assertEqual([candidate["resource_id"] for candidate in candidates], ["lukeewin01/HeNan-Dialect-Datasets"])
        self.assertEqual(watcher.requests[0][0], "datasets")
        self.assertEqual(watcher.requests[0][1]["search"], "auto-speech-recognition")
        self.assertEqual(watcher.requests[0][1]["sort"], "last_modified")
        self.assertEqual(candidates[0]["acquisition_filter"]["ui_params"]["Tags"], "auto-speech-recognition")
        self.assertEqual(candidates[0]["acquisition_filter"]["ui_params"]["dataType"], "audio")

    def test_openapi_search_merges_recent_and_download_sorted_candidates(self) -> None:
        class FakeMergedWatcher(ModelScopeWatcher):
            def __init__(self) -> None:
                super().__init__(api_base="https://modelscope.cn/openapi/v1")
                self.requests: list[tuple[str, dict[str, object]]] = []

            def _request_json(self, endpoint: str, params: dict[str, object]) -> object:
                self.requests.append((endpoint, dict(params)))
                if params["page_number"] != 1:
                    return {"data": {"models": []}}
                if params.get("sort") == "downloads":
                    return {
                        "data": {
                            "models": [
                                {
                                    "id": "iic/high-download-asr",
                                    "display_name": "High Download ASR",
                                    "downloads": 999999,
                                    "tasks": ["auto-speech-recognition"],
                                    "last_modified": "2026-01-01T00:00:00Z",
                                    "tags": ["task:auto-speech-recognition"],
                                }
                            ]
                        }
                    }
                return {
                    "data": {
                        "models": [
                            {
                                "id": "iic/recent-asr",
                                "display_name": "Recent ASR",
                                "downloads": 37,
                                "tasks": ["auto-speech-recognition"],
                                "last_modified": "2099-01-01T00:00:00Z",
                                "tags": ["task:auto-speech-recognition"],
                            }
                        ]
                    }
                }

        watcher = FakeMergedWatcher()

        candidates = watcher.search(task="asr", resource_types=["model"], since_days=36500, max_items=10)

        self.assertEqual(
            [candidate["resource_id"] for candidate in candidates],
            ["iic/recent-asr", "iic/high-download-asr"],
        )
        self.assertIn("downloads", [request[1]["sort"] for request in watcher.requests])
        self.assertEqual(candidates[1]["acquisition_filter"]["api_params"]["sort"], "downloads")

    def test_openapi_search_uses_custom_tag_fallback_for_models_and_datasets(self) -> None:
        class FakeFallbackWatcher(ModelScopeWatcher):
            def __init__(self) -> None:
                super().__init__(api_base="https://modelscope.cn/openapi/v1")
                self.requests: list[tuple[str, dict[str, object]]] = []

            def _request_json(self, endpoint: str, params: dict[str, object]) -> object:
                self.requests.append((endpoint, dict(params)))
                if params["page_number"] != 1:
                    return {"data": {endpoint: []}}
                if params.get("search") == "asr":
                    return {
                        "data": {
                            endpoint: [
                                {
                                    "id": "zhifeixie/Voices-in-the-Wild-test-v2"
                                    if endpoint == "datasets"
                                    else "custom/asr-model",
                                    "display_name": "Voices-in-the-Wild-test-v2"
                                    if endpoint == "datasets"
                                    else "custom-asr-model",
                                    "downloads": 172,
                                    "tasks": [],
                                    "last_modified": "2099-01-01T00:00:00Z",
                                    "tags": ["custom_tag:asr", "custom_tag:speech", "custom_tag:audio"],
                                }
                            ]
                        }
                    }
                return {"data": {endpoint: []}}

        watcher = FakeFallbackWatcher()

        candidates = watcher.search(task="asr", resource_types=["model", "dataset"], since_days=36500, max_items=5)

        self.assertEqual(
            [candidate["resource_id"] for candidate in candidates],
            ["custom/asr-model", "zhifeixie/Voices-in-the-Wild-test-v2"],
        )
        self.assertIn("asr", [request[1]["search"] for request in watcher.requests])
        self.assertTrue(all(candidate["acquisition_filter"]["match_source"] == "custom_tag_fallback" for candidate in candidates))

    def test_extract_items_reads_openapi_nested_data(self) -> None:
        payload = {"data": {"models": [{"id": "iic/demo"}], "total_count": 1}}

        self.assertEqual(_extract_items(payload, "model"), [{"id": "iic/demo"}])

    def test_normalize_candidate_reads_openapi_fields(self) -> None:
        candidate = _normalize_candidate(
            {
                "id": "iic/demo-asr",
                "display_name": "Demo ASR",
                "downloads": 12,
                "tasks": ["automatic-speech-recognition"],
                "last_modified": "2026-06-07T00:00:00Z",
                "tags": ["task:automatic-speech-recognition"],
            },
            resource_type="model",
            task="asr",
        )

        self.assertEqual(candidate["resource_id"], "iic/demo-asr")
        self.assertEqual(candidate["name"], "Demo ASR")
        self.assertEqual(candidate["downloads"], 12)
        self.assertEqual(candidate["task"], ["automatic-speech-recognition"])
        self.assertEqual(candidate["updated_at"], "2026-06-07T00:00:00Z")

    def test_openapi_search_paginates_and_filters_locally(self) -> None:
        watcher = FakeOpenAPIWatcher()

        candidates = watcher.search(task="asr", resource_types=["model"], since_days=36500, max_items=60)

        self.assertEqual([candidate["resource_id"] for candidate in candidates], ["iic/demo-asr-openapi"])
        self.assertEqual(watcher.requests[0][1]["page_size"], 50)
        self.assertEqual(watcher.requests[0][1]["page_number"], 1)

    def test_openapi_empty_tasks_are_not_backfilled_from_requested_task(self) -> None:
        candidate = _normalize_candidate(
            {
                "id": "iic/demo-general-model",
                "display_name": "Demo General Model",
                "tasks": [],
                "last_modified": "2026-06-07T00:00:00Z",
            },
            resource_type="model",
            task="asr",
        )

        self.assertEqual(candidate["task"], "")
        self.assertEqual(task_match_score(candidate, "asr"), 0)

    def test_task_match_score_does_not_substring_match_short_abbreviations(self) -> None:
        unrelated = {
            "resource_type": "model",
            "resource_id": "iic/license-transformer",
            "name": "license transformer",
            "task": "text-generation",
            "raw": {"tags": ["library:transformer", "license:apache-2.0"]},
        }

        self.assertEqual(task_match_score(unrelated, "ser"), 0)
        self.assertEqual(task_match_score(unrelated, "gr"), 0)

    def test_video_model_that_mentions_whisper_is_not_asr_candidate(self) -> None:
        video_model = {
            "resource_type": "model",
            "resource_id": "meituan-longcat/LongCat-Video-Avatar-1.5",
            "name": "LongCat-Video-Avatar-1.5",
            "task": ["image-to-video"],
            "description": "Audio-driven human video generation replaces Wav2Vec2 with Whisper-Large for lip synchronization.",
            "raw": {
                "tasks": ["image-to-video"],
                "tags": ["task:image-to-video", "custom_tag:audio-driven-video-continuation"],
            },
        }

        self.assertEqual(task_match_score(video_model, "asr"), 0)

    def test_task_match_score_accepts_canonical_task_names(self) -> None:
        cases = {
            "s2tt": "speech to text translation",
            "gr": "gender recognition",
            "ser": "speaker emotion recognition",
            "slu": "spoken language understanding",
        }

        for task, phrase in cases.items():
            with self.subTest(task=task):
                candidate = {
                    "resource_type": "model",
                    "resource_id": f"iic/demo-{task}",
                    "name": f"demo {phrase}",
                    "task": phrase,
                }
                self.assertGreater(task_match_score(candidate, task), 0)

    def test_process_candidates_emits_model_manifest_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = XForgeCatalog(root / "catalog.json")
            candidates = [
                {
                    "resource_type": "model",
                    "provider": "modelscope",
                    "resource_id": "iic/demo-asr-model",
                    "name": "demo-asr-model",
                    "task": "ASR",
                    "updated_at": "2026-06-06T00:00:00Z",
                }
            ]

            result = process_candidates(
                candidates=candidates,
                catalog=catalog,
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
                emit_manifests=True,
                emit_handoff=True,
            )

            self.assertEqual(result["new_count"], 1)
            manifest_path = Path(result["manifests"][0]["path"])
            handoff_path = Path(result["handoffs"][0]["path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"]["provider"], "modelscope")
            self.assertEqual(manifest["source"]["id"], "iic/demo-asr-model")
            self.assertEqual(handoff["target_agent"], "sure_tool_agent")
            self.assertEqual(handoff["next_state"], "FETCH_WEIGHTS")

            second = process_candidates(
                candidates=candidates,
                catalog=XForgeCatalog(root / "catalog.json"),
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
                emit_manifests=True,
                emit_handoff=True,
            )
            self.assertEqual(second["new_count"], 0)
            self.assertEqual(second["manifests"], [])

    def test_dataset_manifest_without_schema_is_marked_not_bridge_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = XForgeCatalog(root / "catalog.json")
            candidates = [
                {
                    "resource_type": "dataset",
                    "provider": "modelscope",
                    "resource_id": "speech/demo-asr-dataset",
                    "name": "demo-asr-dataset",
                    "task": "ASR",
                    "language": "zh",
                    "updated_at": "2026-06-06T00:00:00Z",
                }
            ]

            result = process_candidates(
                candidates=candidates,
                catalog=catalog,
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
                emit_manifests=True,
                emit_handoff=True,
            )

            manifest = json.loads(Path(result["manifests"][0]["path"]).read_text(encoding="utf-8"))
            handoff = json.loads(Path(result["handoffs"][0]["path"]).read_text(encoding="utf-8"))
            self.assertFalse(manifest["bridge_ready"])
            self.assertEqual(manifest["processing_status"], "requires_dataset_schema_mapping")
            self.assertEqual(handoff["target_agent"], "sure_main_agent")
            self.assertEqual(handoff["status"], "blocked_until_dataset_schema_mapping")


if __name__ == "__main__":
    unittest.main()
