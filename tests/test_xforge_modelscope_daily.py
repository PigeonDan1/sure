from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xforge_sure_bridge.modelscope_daily import (
    SUPPORTED_TASKS,
    build_daily_summary,
    rank_candidates,
    render_markdown_summary,
    task_match_score,
    write_daily_summary,
)


class XForgeModelScopeDailyTest(unittest.TestCase):
    def test_supported_tasks_are_first_version_scope(self) -> None:
        self.assertEqual(SUPPORTED_TASKS, ("asr", "s2tt", "slu", "gr", "ser"))

    def test_rank_candidates_requires_task_match_then_prefers_report_date_then_downloads(self) -> None:
        candidates = [
            {
                "resource_type": "model",
                "provider": "modelscope",
                "resource_id": "iic/today-popular-unrelated",
                "name": "popular image model",
                "task": "image-to-video",
                "updated_at": "2026-06-07T02:00:00Z",
                "raw": {"downloads": 99999},
            },
            {
                "resource_type": "model",
                "provider": "modelscope",
                "resource_id": "iic/yesterday-popular",
                "name": "asr yesterday",
                "task": "asr",
                "updated_at": "2026-06-06T23:00:00Z",
                "raw": {"downloads": 9999},
            },
            {
                "resource_type": "model",
                "provider": "modelscope",
                "resource_id": "iic/today-low",
                "name": "asr today low",
                "task": "asr",
                "updated_at": "2026-06-07T01:00:00Z",
                "raw": {"downloads": 5},
            },
            {
                "resource_type": "model",
                "provider": "modelscope",
                "resource_id": "iic/today-high",
                "name": "automatic speech recognition today high",
                "task": "automatic-speech-recognition",
                "updated_at": "2026-06-07T00:30:00Z",
                "raw": {"downloadCount": 100},
            },
        ]

        ranked = rank_candidates(candidates, task="asr", report_date="2026-06-07")

        self.assertEqual(
            [item["resource_id"] for item in ranked],
            [
                "iic/today-high",
                "iic/today-low",
                "iic/yesterday-popular",
            ],
        )
        self.assertGreater(ranked[0]["ranking"]["download_count"], ranked[1]["ranking"]["download_count"])
        self.assertTrue(ranked[0]["ranking"]["updated_on_report_date"])
        self.assertNotIn("iic/today-popular-unrelated", [item["resource_id"] for item in ranked])

    def test_gr_short_abbreviation_requires_gender_or_speech_domain_evidence(self) -> None:
        protein_model = {
            "resource_type": "model",
            "provider": "modelscope",
            "resource_id": "microsoft/Dayhoff-3b-GR-HM",
            "name": "Dayhoff-3b-GR-HM",
            "task": ["text-generation"],
            "updated_at": "2026-06-09T18:20:21Z",
            "raw": {
                "downloads": 360,
                "tasks": ["text-generation"],
                "tags": [
                    "task:text-generation",
                    "custom_tag:protein-generation",
                    "custom_tag:jamba",
                ],
            },
        }
        speech_gender_model = {
            "resource_type": "model",
            "provider": "modelscope",
            "resource_id": "speech/gender-demo",
            "name": "speaker GR demo",
            "task": [],
            "updated_at": "2026-06-09T12:00:00Z",
            "raw": {
                "downloads": 10,
                "tasks": [],
                "tags": ["custom_tag:gr", "custom_tag:audio", "custom_tag:speech"],
            },
        }

        self.assertEqual(task_match_score(protein_model, "gr"), 0)
        self.assertGreater(task_match_score(speech_gender_model, "gr"), 0)

        ranked = rank_candidates(
            [protein_model, speech_gender_model],
            task="gr",
            report_date="2026-06-10",
        )

        self.assertEqual([item["resource_id"] for item in ranked], ["speech/gender-demo"])

    def test_build_daily_summary_groups_top_k_per_task_and_resource(self) -> None:
        candidates = [
            {
                "resource_type": "model",
                "provider": "modelscope",
                "resource_id": f"iic/asr-model-{index}",
                "name": f"asr model {index}",
                "task": "asr",
                "updated_at": "2026-06-07T00:00:00Z",
                "raw": {"downloads": index},
            }
            for index in range(5)
        ]
        candidates += [
            {
                "resource_type": "dataset",
                "provider": "modelscope",
                "resource_id": f"speech/asr-dataset-{index}",
                "name": f"asr dataset {index}",
                "task": "asr",
                "updated_at": "2026-06-07T00:00:00Z",
                "raw": {"downloads": index},
            }
            for index in range(4)
        ]

        summary = build_daily_summary(
            candidates_by_task={"asr": candidates},
            errors=[],
            report_date="2026-06-07",
            top_k=3,
        )

        self.assertEqual(summary["report_date"], "2026-06-07")
        self.assertEqual(len(summary["tasks"]["asr"]["model"]["recommended"]), 3)
        self.assertEqual(len(summary["tasks"]["asr"]["model"]["other"]), 2)
        self.assertEqual(summary["tasks"]["asr"]["model"]["recommended"][0]["resource_id"], "iic/asr-model-4")
        self.assertNotIn("high_download", summary["tasks"]["asr"]["model"])
        self.assertEqual(len(summary["tasks"]["asr"]["dataset"]["recommended"]), 3)
        self.assertEqual(len(summary["tasks"]["asr"]["dataset"]["other"]), 1)

    def test_render_markdown_contains_fetch_commands_and_failure_section(self) -> None:
        summary = build_daily_summary(
            candidates_by_task={
                "asr": [
                    {
                        "resource_type": "model",
                        "provider": "modelscope",
                        "resource_id": "iic/demo-asr",
                        "name": "demo-asr",
                        "task": "asr",
                        "updated_at": "2026-06-07T00:00:00Z",
                        "raw": {"downloads": 7},
                    },
                    {
                        "resource_type": "dataset",
                        "provider": "modelscope",
                        "resource_id": "speech/demo-asr-data",
                        "name": "demo-asr-data",
                        "task": "asr",
                        "updated_at": "2026-06-07T00:00:00Z",
                        "raw": {"downloads": 3},
                        "acquisition_filter": {
                            "api_params": {"search": "auto-speech-recognition", "sort": "last_modified"},
                            "ui_params": {"Tags": "auto-speech-recognition", "dataType": "audio"},
                            "match_source": "official_task",
                        },
                    },
                ]
            },
            errors=[{"task": "ser", "resource_type": "model", "error": "api timeout"}],
            report_date="2026-06-07",
            top_k=3,
        )

        markdown = render_markdown_summary(summary)

        self.assertIn("# ModelScope Daily Summary - 2026-06-07", markdown)
        self.assertIn(
            "python scripts/xforge_modelscope_fetch.py --resource model --task asr --id iic/demo-asr",
            markdown,
        )
        self.assertIn(
            "python scripts/xforge_modelscope_fetch.py --resource dataset --task asr --id speech/demo-asr-data",
            markdown,
        )
        self.assertIn("OpenAPI: `search=auto-speech-recognition&sort=last_modified`", markdown)
        self.assertIn("ModelScope page: `Tags=auto-speech-recognition&dataType=audio`", markdown)
        self.assertIn("Match source: `official_task`", markdown)
        self.assertNotIn("### High Download Models", markdown)
        self.assertNotIn("### High Download Datasets", markdown)
        self.assertLess(
            markdown.index("### Recommended Top 3 Datasets"),
            markdown.index("### Other Model Candidates"),
        )
        self.assertIn("## Failures", markdown)
        self.assertIn("ser", markdown)
        self.assertIn("api timeout", markdown)

    def test_write_daily_summary_writes_markdown_json_and_candidates(self) -> None:
        summary = build_daily_summary(
            candidates_by_task={
                "asr": [
                    {
                        "resource_type": "model",
                        "provider": "modelscope",
                        "resource_id": "iic/demo-asr",
                        "name": "demo-asr",
                        "task": "asr",
                        "updated_at": "2026-06-07T00:00:00Z",
                        "raw": {"downloads": 7},
                    }
                ]
            },
            errors=[],
            report_date="2026-06-07",
            top_k=3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = write_daily_summary(summary, Path(tmp))

            self.assertTrue(Path(output["summary_md"]).exists())
            self.assertTrue(Path(output["summary_json"]).exists())
            self.assertTrue(Path(output["candidates_json"]).exists())
            loaded = json.loads(Path(output["summary_json"]).read_text(encoding="utf-8"))
            self.assertEqual(loaded["report_date"], "2026-06-07")

    def test_daily_summary_cli_uses_offline_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates_path = root / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "resource_type": "model",
                                "provider": "modelscope",
                                "resource_id": "iic/demo-asr",
                                "name": "demo-asr",
                                "task": "asr",
                                "updated_at": "2026-06-07T00:00:00Z",
                                "raw": {"downloads": 7},
                            },
                            {
                                "resource_type": "dataset",
                                "provider": "modelscope",
                                "resource_id": "speech/demo-asr-data",
                                "name": "demo-asr-data",
                                "task": "asr",
                                "updated_at": "2026-06-07T00:00:00Z",
                                "raw": {"downloads": 3},
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_root = root / "reports"
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_daily_modelscope_summary.py",
                    "--tasks",
                    "asr",
                    "s2tt",
                    "--top-k",
                    "3",
                    "--date",
                    "2026-06-07",
                    "--output-root",
                    str(output_root),
                    "--candidates-json",
                    str(candidates_path),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((output_root / "2026-06-07" / "summary.md").exists())
            self.assertIn("summary.md", completed.stdout)


if __name__ == "__main__":
    unittest.main()
