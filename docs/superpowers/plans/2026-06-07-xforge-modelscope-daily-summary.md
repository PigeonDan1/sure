# XForge ModelScope Daily Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first-version semi-automatic ModelScope workflow: daily local summaries for `asr`, `s2tt`, `slu`, `gr`, and `ser`, followed by explicit human-selected fetch commands.

**Architecture:** Add focused bridge modules for task/ranking/summary and fetch orchestration, then expose them through two scripts. Existing watcher and bridge primitives remain compatible; new scripts reuse them instead of folding everything into `modelscope_watcher.py`.

**Tech Stack:** Python 3.10+, standard library `argparse/json/datetime/pathlib/subprocess`, existing `xforge_sure_bridge` package, existing `unittest` test style, optional `modelscope` package at runtime for remote fetches.

---

## File Structure

- Create `xforge_sure_bridge/modelscope_daily.py`: task configuration, candidate scoring, Top K grouping, Markdown rendering, JSON artifact writing.
- Create `xforge_sure_bridge/modelscope_fetch.py`: selected-resource candidate construction, manifest/handoff emission, model collection delegation, dataset raw collection policy, fetch summary writing.
- Modify `scripts/xforge_collect_model.py`: expose remote model collection as an importable helper without changing CLI behavior.
- Create `scripts/xforge_daily_modelscope_summary.py`: daily summary CLI.
- Create `scripts/xforge_modelscope_fetch.py`: human-selected fetch CLI.
- Create `tests/test_xforge_modelscope_daily.py`: summary/ranking/Markdown/failure-isolation tests.
- Create `tests/test_xforge_modelscope_fetch.py`: selected model/dataset fetch behavior tests.
- Modify `docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md`: document daily summary and manual fetch flow.
- Modify `xforge_sure_bridge/__init__.py`: export new public helpers used by scripts and tests.

Implementation should keep all new code ASCII-only and should not edit XForge skills or SURE agent flow files.

## Environment Note

The default `.git` in this workspace is mounted read-only. During execution, use either a fixed writable `.git` mount or the already-created writable gitdir pattern:

```bash
git --git-dir=.git.codex-writable-20260606 --work-tree=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox <command>
```

If the default `.git` becomes writable later, ordinary `git <command>` is acceptable.

---

### Task 1: Daily Summary Unit Tests

**Files:**
- Create: `tests/test_xforge_modelscope_daily.py`
- Later create: `xforge_sure_bridge/modelscope_daily.py`

- [ ] **Step 1: Write failing tests for task config, ranking, grouping, and Markdown commands**

Create `tests/test_xforge_modelscope_daily.py` with:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xforge_sure_bridge.modelscope_daily import (
    SUPPORTED_TASKS,
    build_daily_summary,
    rank_candidates,
    render_markdown_summary,
    write_daily_summary,
)


class XForgeModelScopeDailyTest(unittest.TestCase):
    def test_supported_tasks_are_first_version_scope(self) -> None:
        self.assertEqual(SUPPORTED_TASKS, ("asr", "s2tt", "slu", "gr", "ser"))

    def test_rank_candidates_prefers_report_date_then_downloads_then_match(self) -> None:
        candidates = [
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

        self.assertEqual([item["resource_id"] for item in ranked], [
            "iic/today-high",
            "iic/today-low",
            "iic/yesterday-popular",
        ])
        self.assertGreater(ranked[0]["ranking"]["download_count"], ranked[1]["ranking"]["download_count"])
        self.assertTrue(ranked[0]["ranking"]["updated_on_report_date"])

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
                    },
                ]
            },
            errors=[{"task": "ser", "resource_type": "model", "error": "api timeout"}],
            report_date="2026-06-07",
            top_k=3,
        )

        markdown = render_markdown_summary(summary)

        self.assertIn("# ModelScope Daily Summary - 2026-06-07", markdown)
        self.assertIn("python scripts/xforge_modelscope_fetch.py --resource model --task asr --id iic/demo-asr", markdown)
        self.assertIn("python scripts/xforge_modelscope_fetch.py --resource dataset --task asr --id speech/demo-asr-data", markdown)
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
```

- [ ] **Step 2: Run tests to verify they fail because the module does not exist**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_daily
```

Expected: FAIL with `ModuleNotFoundError: No module named 'xforge_sure_bridge.modelscope_daily'`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_xforge_modelscope_daily.py
git commit -m "test: cover modelscope daily summary flow"
```

If `.git` remains read-only, use:

```bash
git --git-dir=.git.codex-writable-20260606 --work-tree=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox add tests/test_xforge_modelscope_daily.py
git --git-dir=.git.codex-writable-20260606 --work-tree=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox commit -m "test: cover modelscope daily summary flow"
```

---

### Task 2: Daily Summary Library

**Files:**
- Create: `xforge_sure_bridge/modelscope_daily.py`
- Modify: `xforge_sure_bridge/__init__.py`
- Test: `tests/test_xforge_modelscope_daily.py`

- [ ] **Step 1: Implement scoring, grouping, rendering, and artifact writing**

Create `xforge_sure_bridge/modelscope_daily.py`:

```python
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_TASKS = ("asr", "s2tt", "slu", "gr", "ser")

TASK_KEYWORDS = {
    "asr": ("asr", "speech recognition", "automatic speech recognition", "paraformer", "whisper"),
    "s2tt": ("s2tt", "speech translation", "speech-to-text translation", "covost"),
    "slu": ("slu", "spoken language understanding", "intent", "slot filling"),
    "gr": ("gr", "gender recognition", "gender classification"),
    "ser": ("ser", "speech emotion recognition", "emotion recognition", "emotion"),
}


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _report_date(value: str) -> date:
    return date.fromisoformat(value)


def extract_download_count(candidate: dict[str, Any]) -> int:
    raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
    values = [
        candidate.get("downloads"),
        candidate.get("download_count"),
        candidate.get("downloadCount"),
        raw.get("downloads"),
        raw.get("download_count"),
        raw.get("downloadCount"),
        raw.get("downloadsCount"),
    ]
    for value in values:
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def task_match_score(candidate: dict[str, Any], task: str) -> int:
    task = task.lower()
    keywords = TASK_KEYWORDS.get(task, (task,))
    raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
    tags = raw.get("tags") or raw.get("Tags") or candidate.get("tags") or []
    if isinstance(tags, str):
        tags_text = tags
    elif isinstance(tags, list):
        tags_text = " ".join(str(item) for item in tags)
    else:
        tags_text = ""
    searchable = " ".join(
        str(value or "")
        for value in (
            candidate.get("task"),
            candidate.get("name"),
            candidate.get("description"),
            candidate.get("summary"),
            raw.get("task"),
            raw.get("pipeline_tag"),
            raw.get("pipeline"),
            raw.get("name"),
            raw.get("description"),
            raw.get("summary"),
            tags_text,
        )
    ).lower()
    score = 0
    if task in searchable:
        score += 5
    for keyword in keywords:
        if keyword.lower() in searchable:
            score += 3
    return score


def _ranking(candidate: dict[str, Any], task: str, report_date: str) -> dict[str, Any]:
    parsed = parse_datetime(candidate.get("updated_at"))
    updated_on_report_date = parsed is not None and parsed.date() == _report_date(report_date)
    recency_ts = parsed.timestamp() if parsed else 0.0
    return {
        "updated_on_report_date": updated_on_report_date,
        "download_count": extract_download_count(candidate),
        "task_match_score": task_match_score(candidate, task),
        "recency_timestamp": recency_ts,
    }


def rank_candidates(candidates: list[dict[str, Any]], task: str, report_date: str) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_copy = dict(candidate)
        candidate_copy["ranking"] = _ranking(candidate_copy, task, report_date)
        ranked.append(candidate_copy)
    return sorted(
        ranked,
        key=lambda item: (
            item["ranking"]["updated_on_report_date"],
            item["ranking"]["download_count"],
            item["ranking"]["task_match_score"],
            item["ranking"]["recency_timestamp"],
        ),
        reverse=True,
    )


def _empty_resource_group() -> dict[str, list[dict[str, Any]]]:
    return {"recommended": [], "other": []}


def build_daily_summary(
    candidates_by_task: dict[str, list[dict[str, Any]]],
    errors: list[dict[str, Any]],
    report_date: str,
    top_k: int,
) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for task in SUPPORTED_TASKS:
        task_candidates = candidates_by_task.get(task, [])
        task_summary = {"model": _empty_resource_group(), "dataset": _empty_resource_group()}
        for resource_type in ("model", "dataset"):
            resource_candidates = [
                item for item in task_candidates if item.get("resource_type") == resource_type
            ]
            ranked = rank_candidates(resource_candidates, task=task, report_date=report_date)
            task_summary[resource_type]["recommended"] = ranked[:top_k]
            task_summary[resource_type]["other"] = ranked[top_k:]
        tasks[task] = task_summary
    return {
        "version": 1,
        "provider": "modelscope",
        "report_date": report_date,
        "top_k": top_k,
        "tasks": tasks,
        "errors": errors,
    }


def _candidate_line(candidate: dict[str, Any], task: str) -> str:
    ranking = candidate.get("ranking", {})
    resource_type = str(candidate["resource_type"])
    resource_id = str(candidate["resource_id"])
    name = str(candidate.get("name") or resource_id)
    downloads = int(ranking.get("download_count", 0))
    updated = str(candidate.get("updated_at") or "")
    command = (
        "python scripts/xforge_modelscope_fetch.py "
        f"--resource {resource_type} --task {task} --id {resource_id}"
    )
    return (
        f"- `{resource_id}` | {name} | downloads={downloads} | updated={updated}\n"
        f"  - Fetch: `{command}`"
    )


def _render_candidate_section(title: str, candidates: list[dict[str, Any]], task: str) -> list[str]:
    lines = [f"### {title}", ""]
    if not candidates:
        lines.extend(["No candidates.", ""])
        return lines
    for candidate in candidates:
        lines.append(_candidate_line(candidate, task))
    lines.append("")
    return lines


def render_markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"# ModelScope Daily Summary - {summary['report_date']}",
        "",
        f"Top K recommendations per task/resource: {summary['top_k']}",
        "",
    ]
    for task in SUPPORTED_TASKS:
        task_summary = summary["tasks"][task]
        lines.extend([f"## Task: {task}", ""])
        lines.extend(_render_candidate_section("Recommended Top 3 Models", task_summary["model"]["recommended"], task))
        lines.extend(_render_candidate_section("Other Model Candidates", task_summary["model"]["other"], task))
        lines.extend(_render_candidate_section("Recommended Top 3 Datasets", task_summary["dataset"]["recommended"], task))
        lines.extend(_render_candidate_section("Other Dataset Candidates", task_summary["dataset"]["other"], task))
    if summary.get("errors"):
        lines.extend(["## Failures", ""])
        for error in summary["errors"]:
            lines.append(
                f"- task={error.get('task')} resource={error.get('resource_type')} error={error.get('error')}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _flatten_candidates(summary: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for task_summary in summary["tasks"].values():
        for resource_summary in task_summary.values():
            candidates.extend(resource_summary["recommended"])
            candidates.extend(resource_summary["other"])
    return candidates


def write_daily_summary(summary: dict[str, Any], output_root: str | Path) -> dict[str, str]:
    day_dir = Path(output_root) / str(summary["report_date"])
    day_dir.mkdir(parents=True, exist_ok=True)
    summary_md = day_dir / "summary.md"
    summary_json = day_dir / "summary.json"
    candidates_json = day_dir / "candidates.json"
    summary_md.write_text(render_markdown_summary(summary), encoding="utf-8")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidates_json.write_text(
        json.dumps({"candidates": _flatten_candidates(summary)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "summary_md": str(summary_md),
        "summary_json": str(summary_json),
        "candidates_json": str(candidates_json),
    }
```

- [ ] **Step 2: Export public helpers**

Modify `xforge_sure_bridge/__init__.py` to include:

```python
from xforge_sure_bridge.modelscope_daily import (
    SUPPORTED_TASKS,
    build_daily_summary,
    rank_candidates,
    render_markdown_summary,
    write_daily_summary,
)
```

and add these names to `__all__`:

```python
    "SUPPORTED_TASKS",
    "build_daily_summary",
    "rank_candidates",
    "render_markdown_summary",
    "write_daily_summary",
```

- [ ] **Step 3: Run daily summary tests**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_daily
```

Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 4: Run existing xforge watcher tests**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_watcher
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit daily summary library**

```bash
git add xforge_sure_bridge/modelscope_daily.py xforge_sure_bridge/__init__.py tests/test_xforge_modelscope_daily.py
git commit -m "feat: add modelscope daily summary helpers"
```

Use the writable gitdir variant if needed.

---

### Task 3: Daily Summary CLI

**Files:**
- Create: `scripts/xforge_daily_modelscope_summary.py`
- Modify: `tests/test_xforge_modelscope_daily.py`

- [ ] **Step 1: Add CLI integration test with offline candidates**

Append to `tests/test_xforge_modelscope_daily.py`:

```python
import subprocess
import sys
```

Add this method to `XForgeModelScopeDailyTest`:

```python
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
```

- [ ] **Step 2: Run the new CLI test to verify it fails**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_daily.XForgeModelScopeDailyTest.test_daily_summary_cli_uses_offline_candidates
```

Expected: FAIL because `scripts/xforge_daily_modelscope_summary.py` does not exist.

- [ ] **Step 3: Implement the CLI**

Create `scripts/xforge_daily_modelscope_summary.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.modelscope_daily import SUPPORTED_TASKS, build_daily_summary, write_daily_summary
from xforge_sure_bridge.modelscope_watcher import ModelScopeWatcher


def _today_string() -> str:
    return datetime.now().date().isoformat()


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        value = data.get("candidates") or data.get("items") or data.get("data")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"cannot load candidates from {path}")


def _matches_task(candidate: dict[str, Any], task: str) -> bool:
    candidate_task = str(candidate.get("task") or "").lower()
    resource_id = str(candidate.get("resource_id") or "").lower()
    name = str(candidate.get("name") or "").lower()
    return task.lower() in " ".join((candidate_task, resource_id, name))


def _offline_candidates_by_task(candidates: list[dict[str, Any]], tasks: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {
        task: [candidate for candidate in candidates if _matches_task(candidate, task)]
        for task in tasks
    }


def _online_candidates_by_task(
    tasks: list[str],
    resource_types: list[str],
    since_days: int,
    max_items: int,
    api_base: str | None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    watcher = ModelScopeWatcher(api_base=api_base) if api_base else ModelScopeWatcher()
    candidates_by_task: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    for task in tasks:
        task_candidates: list[dict[str, Any]] = []
        for resource_type in resource_types:
            try:
                task_candidates.extend(
                    watcher.search(
                        task=task,
                        resource_types=[resource_type],
                        since_days=since_days,
                        max_items=max_items,
                    )
                )
            except Exception as exc:
                errors.append({"task": task, "resource_type": resource_type, "error": str(exc)})
        candidates_by_task[task] = task_candidates
    return candidates_by_task, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Write daily ModelScope model/dataset summaries for human review")
    parser.add_argument("--tasks", nargs="+", default=list(SUPPORTED_TASKS), choices=SUPPORTED_TASKS)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--date", default="today")
    parser.add_argument("--output-root", default="reports/xforge/modelscope")
    parser.add_argument("--candidates-json", help="Use offline candidate JSON instead of querying ModelScope")
    parser.add_argument("--since-days", type=int, default=1)
    parser.add_argument("--max-items", type=int, default=50)
    parser.add_argument("--resource", choices=["model", "dataset", "all"], default="all")
    parser.add_argument("--api-base", default=None)
    args = parser.parse_args()

    report_date = _today_string() if args.date == "today" else args.date
    resource_types = ["model", "dataset"] if args.resource == "all" else [args.resource]

    try:
        if args.candidates_json:
            candidates = _load_candidates(Path(args.candidates_json))
            candidates_by_task = _offline_candidates_by_task(candidates, list(args.tasks))
            errors: list[dict[str, Any]] = []
        else:
            candidates_by_task, errors = _online_candidates_by_task(
                tasks=list(args.tasks),
                resource_types=resource_types,
                since_days=args.since_days,
                max_items=args.max_items,
                api_base=args.api_base,
            )

        summary = build_daily_summary(
            candidates_by_task=candidates_by_task,
            errors=errors,
            report_date=report_date,
            top_k=args.top_k,
        )
        output = write_daily_summary(summary, args.output_root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI help and integration test**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_daily_modelscope_summary.py --help
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_daily
```

Expected: help exits `0`; all daily summary tests pass.

- [ ] **Step 5: Commit the daily summary CLI**

```bash
git add scripts/xforge_daily_modelscope_summary.py tests/test_xforge_modelscope_daily.py
git commit -m "feat: add modelscope daily summary cli"
```

Use the writable gitdir variant if needed.

---

### Task 4: Fetch Unit Tests

**Files:**
- Create: `tests/test_xforge_modelscope_fetch.py`
- Later create: `xforge_sure_bridge/modelscope_fetch.py`

- [ ] **Step 1: Write failing fetch tests**

Create `tests/test_xforge_modelscope_fetch.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    write_fetch_failure,
)


class XForgeModelScopeFetchTest(unittest.TestCase):
    def test_build_selected_model_candidate(self) -> None:
        candidate = build_selected_candidate(resource_type="model", task="asr", resource_id="iic/demo-asr")

        self.assertEqual(candidate["provider"], "modelscope")
        self.assertEqual(candidate["resource_type"], "model")
        self.assertEqual(candidate["resource_id"], "iic/demo-asr")
        self.assertEqual(candidate["task"], "asr")

    def test_emit_selected_model_manifest_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = build_selected_candidate(resource_type="model", task="asr", resource_id="iic/demo-asr")

            result = emit_selected_resource_artifacts(
                candidate=candidate,
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            handoff = json.loads(Path(result["handoff_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["resource_type"], "model")
            self.assertEqual(manifest["source"]["provider"], "modelscope")
            self.assertEqual(manifest["source"]["id"], "iic/demo-asr")
            self.assertEqual(handoff["target_agent"], "sure_tool_agent")
            self.assertEqual(handoff["status"], "ready_for_model_collect")

    def test_emit_selected_dataset_manifest_is_blocked_without_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = build_selected_candidate(resource_type="dataset", task="ser", resource_id="speech/demo-ser")

            result = emit_selected_resource_artifacts(
                candidate=candidate,
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            handoff = json.loads(Path(result["handoff_path"]).read_text(encoding="utf-8"))
            self.assertFalse(manifest["bridge_ready"])
            self.assertEqual(manifest["processing_status"], "requires_dataset_schema_mapping")
            self.assertEqual(handoff["target_agent"], "sure_main_agent")
            self.assertEqual(handoff["status"], "blocked_until_dataset_schema_mapping")

    def test_write_fetch_failure_records_audit_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failure_path = write_fetch_failure(
                fetch_run_dir=Path(tmp),
                resource_type="model",
                task="asr",
                resource_id="iic/demo-asr",
                command=["python", "scripts/xforge_modelscope_fetch.py"],
                error="modelscope is required",
            )

            failure = json.loads(Path(failure_path).read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["resource_type"], "model")
            self.assertEqual(failure["task"], "asr")
            self.assertEqual(failure["resource_id"], "iic/demo-asr")
            self.assertEqual(failure["error"], "modelscope is required")
```

- [ ] **Step 2: Run tests to verify they fail because the module does not exist**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_fetch
```

Expected: FAIL with `ModuleNotFoundError: No module named 'xforge_sure_bridge.modelscope_fetch'`.

- [ ] **Step 3: Commit failing fetch tests**

```bash
git add tests/test_xforge_modelscope_fetch.py
git commit -m "test: cover selected modelscope fetch flow"
```

Use the writable gitdir variant if needed.

---

### Task 5: Fetch Library and Collect Refactor

**Files:**
- Create: `xforge_sure_bridge/modelscope_fetch.py`
- Modify: `scripts/xforge_collect_model.py`
- Modify: `xforge_sure_bridge/__init__.py`
- Test: `tests/test_xforge_modelscope_fetch.py`

- [ ] **Step 1: Extract importable model collection helper**

Modify `scripts/xforge_collect_model.py` by renaming `_collect_remote_source` to `collect_remote_model_source` and updating the call in `main()`:

```python
def collect_remote_model_source(manifest: dict[str, Any], model_dir: Path) -> dict[str, Any]:
    source = manifest.get("source", {})
    if not isinstance(source, dict):
        raise BridgeError("model source must be a JSON object")
    provider = source.get("provider")
    source_id = source.get("id")
    if not provider or not source_id:
        raise BridgeError("model source requires provider and id")

    download_root = model_dir / ".runtime" / "xforge_downloads"
    download_root.mkdir(parents=True, exist_ok=True)

    if provider == "local":
        return manifest

    if provider == "huggingface":
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise BridgeError("huggingface_hub is required for provider='huggingface'") from exc
        local_path = snapshot_download(
            repo_id=str(source_id),
            local_dir=str(download_root / str(source_id).replace("/", "__")),
            local_dir_use_symlinks=False,
        )
    elif provider == "modelscope":
        try:
            from modelscope import snapshot_download
        except ImportError as exc:
            raise BridgeError("modelscope is required for provider='modelscope'") from exc
        local_path = snapshot_download(
            model_id=str(source_id),
            cache_dir=str(download_root),
        )
    else:
        raise BridgeError(f"unsupported model provider: {provider}")

    collected = dict(manifest)
    collected["source"] = {"provider": "local", "id": str(local_path), "original_source": source}
    return collected
```

Update `main()`:

```python
manifest = collect_remote_model_source(load_manifest(args.manifest), model_dir)
```

- [ ] **Step 2: Implement fetch helpers**

Create `xforge_sure_bridge/modelscope_fetch.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xforge_sure_bridge.modelscope_watcher import (
    _dataset_manifest,
    _handoff_event,
    _model_manifest,
    slugify,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_selected_candidate(
    resource_type: str,
    task: str,
    resource_id: str,
    name: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    if resource_type not in ("model", "dataset"):
        raise ValueError("resource_type must be 'model' or 'dataset'")
    return {
        "resource_type": resource_type,
        "provider": "modelscope",
        "resource_id": resource_id,
        "name": name or resource_id.split("/")[-1],
        "task": task,
        "language": language,
        "url": f"https://modelscope.cn/{resource_type}s/{resource_id}",
        "selected_at": _utc_now(),
    }


def emit_selected_resource_artifacts(
    candidate: dict[str, Any],
    manifest_dir: str | Path,
    handoff_dir: str | Path,
) -> dict[str, str]:
    manifest_root = Path(manifest_dir)
    handoff_root = Path(handoff_dir)
    resource_type = str(candidate["resource_type"])
    stem = slugify(str(candidate["resource_id"]))
    manifest_path = manifest_root / f"{stem}.{resource_type}.json"
    if resource_type == "model":
        manifest = _model_manifest(candidate)
    else:
        manifest = _dataset_manifest(candidate, manifest_root)
    _write_json(manifest_path, manifest)
    handoff_path = handoff_root / f"{stem}.handoff.json"
    _write_json(handoff_path, _handoff_event(candidate, manifest_path))
    return {"manifest_path": str(manifest_path), "handoff_path": str(handoff_path)}


def write_fetch_success(fetch_run_dir: str | Path, payload: dict[str, Any]) -> str:
    fetch_root = Path(fetch_run_dir)
    stem = slugify(str(payload["resource_id"]))
    path = fetch_root / f"{stem}.success.json"
    _write_json(path, {"status": "succeeded", "created_at": _utc_now(), **payload})
    return str(path)


def write_fetch_failure(
    fetch_run_dir: str | Path,
    resource_type: str,
    task: str,
    resource_id: str,
    command: list[str],
    error: str,
) -> str:
    fetch_root = Path(fetch_run_dir)
    stem = slugify(resource_id)
    path = fetch_root / f"{stem}.failed.json"
    _write_json(
        path,
        {
            "status": "failed",
            "provider": "modelscope",
            "resource_type": resource_type,
            "task": task,
            "resource_id": resource_id,
            "command": command,
            "error": error,
            "created_at": _utc_now(),
        },
    )
    return str(path)
```

- [ ] **Step 3: Export fetch helpers**

Modify `xforge_sure_bridge/__init__.py`:

```python
from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    write_fetch_failure,
    write_fetch_success,
)
```

Add these to `__all__`:

```python
    "build_selected_candidate",
    "emit_selected_resource_artifacts",
    "write_fetch_failure",
    "write_fetch_success",
```

- [ ] **Step 4: Run fetch and existing bridge tests**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_fetch tests.test_xforge_sure_bridge
```

Expected: all tests pass.

- [ ] **Step 5: Commit fetch library**

```bash
git add xforge_sure_bridge/modelscope_fetch.py xforge_sure_bridge/__init__.py scripts/xforge_collect_model.py tests/test_xforge_modelscope_fetch.py
git commit -m "feat: add selected modelscope fetch helpers"
```

Use the writable gitdir variant if needed.

---

### Task 6: Fetch CLI

**Files:**
- Create: `scripts/xforge_modelscope_fetch.py`
- Modify: `tests/test_xforge_modelscope_fetch.py`

- [ ] **Step 1: Add CLI tests for dataset and local-source model paths**

Append to `tests/test_xforge_modelscope_fetch.py`:

```python
import subprocess
import sys
```

Add methods:

```python
    def test_fetch_cli_emits_dataset_blocked_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "dataset",
                    "--task",
                    "ser",
                    "--id",
                    "speech/demo-ser",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(Path(payload["handoff_path"]).exists())
            self.assertEqual(payload["resource_type"], "dataset")

    def test_fetch_cli_collects_local_model_source_for_offline_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "downloaded" / "model.bin"
            source.parent.mkdir()
            source.write_bytes(b"weights")
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "model",
                    "--task",
                    "asr",
                    "--id",
                    "iic/demo-asr",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--model-root",
                    str(root / "models"),
                    "--source-provider",
                    "local",
                    "--source-path",
                    str(source),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["resource_type"], "model")
            self.assertTrue(Path(payload["collect_summary"]["weights_manifest"]).exists())
```

- [ ] **Step 2: Run new CLI tests to verify failure**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_fetch.XForgeModelScopeFetchTest.test_fetch_cli_emits_dataset_blocked_handoff tests.test_xforge_modelscope_fetch.XForgeModelScopeFetchTest.test_fetch_cli_collects_local_model_source_for_offline_verification
```

Expected: FAIL because `scripts/xforge_modelscope_fetch.py` does not exist.

- [ ] **Step 3: Implement fetch CLI**

Create `scripts/xforge_modelscope_fetch.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.xforge_collect_model import collect_remote_model_source
from xforge_sure_bridge.bridge import BridgeError, materialize_model_manifest, write_summary
from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    write_fetch_failure,
    write_fetch_success,
)
from xforge_sure_bridge.modelscope_watcher import slugify


def _load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _model_dir(model_root: Path, resource_id: str) -> Path:
    return model_root / slugify(resource_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a human-selected ModelScope model or dataset")
    parser.add_argument("--resource", choices=["model", "dataset"], required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--language")
    parser.add_argument("--manifest-dir", default="data/artifacts/xforge/modelscope/manifests")
    parser.add_argument("--handoff-dir", default="data/artifacts/xforge/modelscope/handoff")
    parser.add_argument("--fetch-run-dir", default="data/artifacts/xforge/modelscope/fetch_runs")
    parser.add_argument("--model-root", default="src/sure_eval/models")
    parser.add_argument("--source-provider", choices=["modelscope", "local"], default="modelscope")
    parser.add_argument("--source-path", help="Local model path when --source-provider local")
    parser.add_argument("--schema-mapping", help="YAML or JSON mapping for dataset conversion to SURE JSONL")
    parser.add_argument("--no-download", action="store_true", help="Emit manifest/handoff only")
    args = parser.parse_args()

    command = sys.argv[:]
    try:
        candidate = build_selected_candidate(
            resource_type=args.resource,
            task=args.task,
            resource_id=args.id,
            name=args.name,
            language=args.language,
        )
        artifacts = emit_selected_resource_artifacts(
            candidate=candidate,
            manifest_dir=args.manifest_dir,
            handoff_dir=args.handoff_dir,
        )
        result = {
            "provider": "modelscope",
            "resource_type": args.resource,
            "task": args.task,
            "resource_id": args.id,
            **artifacts,
        }

        if args.resource == "model" and not args.no_download:
            manifest = _load_json(artifacts["manifest_path"])
            if args.source_provider == "local":
                if not args.source_path:
                    raise BridgeError("--source-path is required when --source-provider local")
                manifest["source"] = {
                    "provider": "local",
                    "id": args.source_path,
                    "original_source": manifest["source"],
                }
            model_dir = _model_dir(Path(args.model_root), args.id)
            collected_manifest = collect_remote_model_source(manifest, model_dir)
            collect_summary = materialize_model_manifest(collected_manifest, model_dir)
            summary_path = model_dir / "artifacts" / "xforge_collect_summary.json"
            write_summary(summary_path, collect_summary)
            result["collect_summary"] = collect_summary
            result["collect_summary_path"] = str(summary_path)

        success_path = write_fetch_success(args.fetch_run_dir, result)
        result["fetch_summary_path"] = success_path
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        failure_path = write_fetch_failure(
            fetch_run_dir=args.fetch_run_dir,
            resource_type=args.resource,
            task=args.task,
            resource_id=args.id,
            command=command,
            error=str(exc),
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        print(json.dumps({"fetch_summary_path": failure_path}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run fetch CLI help and tests**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_modelscope_fetch.py --help
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_fetch
```

Expected: help exits `0`; fetch tests pass.

- [ ] **Step 5: Commit fetch CLI**

```bash
git add scripts/xforge_modelscope_fetch.py tests/test_xforge_modelscope_fetch.py
git commit -m "feat: add selected modelscope fetch cli"
```

Use the writable gitdir variant if needed.

---

### Task 7: Dataset Schema Mapping Conversion

**Files:**
- Modify: `scripts/xforge_modelscope_fetch.py`
- Modify: `tests/test_xforge_modelscope_fetch.py`

- [ ] **Step 1: Add a test for dataset conversion with explicit schema mapping**

Append this method to `XForgeModelScopeFetchTest` in `tests/test_xforge_modelscope_fetch.py`:

```python
    def test_fetch_cli_converts_dataset_with_schema_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw"
            audio = raw_root / "audio" / "sample.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"RIFF")
            raw_jsonl = raw_root / "samples.jsonl"
            raw_jsonl.write_text(
                '{"id":"utt1","audio":"audio/sample.wav","text":"hello","language":"en"}\n',
                encoding="utf-8",
            )
            mapping = root / "mapping.yaml"
            mapping.write_text(
                "\n".join(
                    [
                        "raw_root: " + str(raw_root),
                        "raw_jsonl: samples.jsonl",
                        "sure_name: demo_ser",
                        "language: en",
                        "field_mapping:",
                        "  key: id",
                        "  path: audio",
                        "  target: text",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "dataset",
                    "--task",
                    "ser",
                    "--id",
                    "speech/demo-ser",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--sure-dataset-dir",
                    str(root / "sure"),
                    "--schema-mapping",
                    str(mapping),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["dataset_processing_summary"]["samples_written"], 1)
            self.assertTrue(Path(payload["dataset_processing_summary"]["jsonl_path"]).exists())
```

- [ ] **Step 2: Run the new mapping test to verify it fails**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_fetch.XForgeModelScopeFetchTest.test_fetch_cli_converts_dataset_with_schema_mapping
```

Expected: FAIL because `scripts/xforge_modelscope_fetch.py` does not yet process `--schema-mapping`.

- [ ] **Step 3: Add mapping loading and dataset conversion to the fetch CLI**

Modify imports in `scripts/xforge_modelscope_fetch.py`:

```python
from xforge_sure_bridge.bridge import BridgeError, materialize_model_manifest, process_dataset_manifest, write_summary
```

Add helper functions near `_load_json`:

```python
def _load_mapping(path: str | Path) -> dict:
    mapping_path = Path(path)
    if mapping_path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise BridgeError("pyyaml is required for YAML schema mappings") from exc
        data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    else:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BridgeError("schema mapping must be a JSON/YAML object")
    return data


def _dataset_output_path(sure_dataset_dir: Path, sure_name: str) -> Path:
    return sure_dataset_dir / f"{sure_name}.jsonl"
```

Add this parser argument after `--model-root`:

```python
    parser.add_argument("--sure-dataset-dir", default="data/datasets/xforge_sure")
```

Add this block after the model collection block and before `write_fetch_success(...)`:

```python
        if args.resource == "dataset" and args.schema_mapping:
            manifest = _load_json(artifacts["manifest_path"])
            mapping = _load_mapping(args.schema_mapping)
            manifest["raw_root"] = str(mapping["raw_root"])
            manifest["raw_jsonl"] = str(mapping["raw_jsonl"])
            manifest["sure_name"] = str(mapping.get("sure_name") or manifest["sure_name"])
            manifest["language"] = str(mapping.get("language") or manifest["language"])
            manifest["field_mapping"] = dict(mapping["field_mapping"])
            output_path = _dataset_output_path(Path(args.sure_dataset_dir), manifest["sure_name"])
            dataset_summary = process_dataset_manifest(manifest, output_path)
            result["dataset_processing_summary"] = dataset_summary
```

- [ ] **Step 4: Run dataset mapping and fetch tests**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest tests.test_xforge_modelscope_fetch
```

Expected: all fetch tests pass.

- [ ] **Step 5: Commit dataset mapping support**

```bash
git add scripts/xforge_modelscope_fetch.py tests/test_xforge_modelscope_fetch.py
git commit -m "feat: convert selected datasets with schema mapping"
```

Use the writable gitdir variant if needed.

---

### Task 8: Documentation Update

**Files:**
- Modify: `docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md`

- [ ] **Step 1: Add the daily summary section**

Insert this section after `## Daily ModelScope Watch Flow` in `docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md`:

```markdown
## Daily ModelScope Summary Flow

The first-version daily workflow does not auto-download recommendations. It
writes a local Markdown report for human review.

```bash
python scripts/xforge_daily_modelscope_summary.py \
  --tasks asr s2tt slu gr ser \
  --top-k 3 \
  --date today \
  --output-root reports/xforge/modelscope
```

The command writes:

```text
reports/xforge/modelscope/YYYY-MM-DD/summary.md
reports/xforge/modelscope/YYYY-MM-DD/summary.json
reports/xforge/modelscope/YYYY-MM-DD/candidates.json
```

Read `summary.md`, choose a model or dataset, then run the fetch command shown
beside the candidate.

Model example:

```bash
python scripts/xforge_modelscope_fetch.py \
  --resource model \
  --task asr \
  --id iic/example-modelscope-model
```

Dataset example:

```bash
python scripts/xforge_modelscope_fetch.py \
  --resource dataset \
  --task asr \
  --id speech/example-modelscope-dataset \
  --no-download
```

Datasets remain blocked until a schema mapping or known adapter is available.
```
```

- [ ] **Step 2: Run Markdown grep verification**

Run:

```bash
rg -n "Daily ModelScope Summary Flow|xforge_daily_modelscope_summary.py|xforge_modelscope_fetch.py" docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md
```

Expected: all three terms are present.

- [ ] **Step 3: Commit docs**

```bash
git add docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md
git commit -m "docs: document modelscope daily summary workflow"
```

Use the writable gitdir variant if needed.

---

### Task 9: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run all xforge bridge tests**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python -m unittest \
  tests.test_xforge_modelscope_watcher \
  tests.test_xforge_sure_bridge \
  tests.test_xforge_modelscope_daily \
  tests.test_xforge_modelscope_fetch
```

Expected: all tests pass.

- [ ] **Step 2: Run CLI help checks**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_watch_modelscope.py --help
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_collect_model.py --help
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_collect_dataset.py --help
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_process_to_sure.py --help
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_daily_modelscope_summary.py --help
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_modelscope_fetch.py --help
```

Expected: every command exits `0`.

- [ ] **Step 3: Run offline daily summary smoke**

Create `/tmp/xforge-smoke-candidates.json`:

```json
{
  "candidates": [
    {
      "resource_type": "model",
      "provider": "modelscope",
      "resource_id": "iic/demo-asr-smoke",
      "name": "demo-asr-smoke",
      "task": "asr",
      "updated_at": "2026-06-07T00:00:00Z",
      "raw": {"downloads": 10}
    },
    {
      "resource_type": "dataset",
      "provider": "modelscope",
      "resource_id": "speech/demo-ser-smoke",
      "name": "demo-ser-smoke",
      "task": "ser",
      "updated_at": "2026-06-07T00:00:00Z",
      "raw": {"downloads": 5}
    }
  ]
}
```

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_daily_modelscope_summary.py \
  --tasks asr ser \
  --top-k 3 \
  --date 2026-06-07 \
  --output-root /tmp/xforge-daily-smoke \
  --candidates-json /tmp/xforge-smoke-candidates.json
```

Expected:

```text
/tmp/xforge-daily-smoke/2026-06-07/summary.md
/tmp/xforge-daily-smoke/2026-06-07/summary.json
/tmp/xforge-daily-smoke/2026-06-07/candidates.json
```

- [ ] **Step 4: Run offline fetch smoke**

Run:

```bash
/tmp/sure-eval-uv-venv/bin/python scripts/xforge_modelscope_fetch.py \
  --resource dataset \
  --task ser \
  --id speech/demo-ser-smoke \
  --manifest-dir /tmp/xforge-fetch-smoke/manifests \
  --handoff-dir /tmp/xforge-fetch-smoke/handoff \
  --fetch-run-dir /tmp/xforge-fetch-smoke/fetch_runs \
  --no-download
```

Expected:

```text
/tmp/xforge-fetch-smoke/manifests/speech__demo-ser-smoke.dataset.json
/tmp/xforge-fetch-smoke/handoff/speech__demo-ser-smoke.handoff.json
/tmp/xforge-fetch-smoke/fetch_runs/speech__demo-ser-smoke.success.json
```

- [ ] **Step 5: Check worktree for intended files**

Run:

```bash
git status --short
```

Expected: only files from this plan are newly modified by this implementation. Existing unrelated dirty files may remain and must not be reverted.

- [ ] **Step 6: Commit final verification note if needed**

If verification required a small docs or test adjustment, commit it:

```bash
git add <verified files>
git commit -m "test: verify modelscope daily summary workflow"
```

Use the writable gitdir variant if needed.

## Self-Review

Spec coverage:

- Daily local Markdown/JSON summary: Tasks 1-3 and Task 9.
- Covered tasks `asr/s2tt/slu/gr/ser`: Tasks 1-3.
- Top 3 recommendations, not auto-download: Tasks 1-3.
- Human-selected fetch: Tasks 4-6.
- Model SURE artifacts and handoff: Tasks 5-6.
- Dataset blocked without schema mapping: Tasks 4-6.
- Dataset conversion with explicit schema mapping: Task 7.
- Error/failure audit summaries: Tasks 1, 4, 6.
- HPC/cron documentation: Task 8.

Known implementation constraint:

- Online ModelScope API details still need runtime validation on AISpeech/HPC with a valid CA bundle and `modelscope` package installed. This plan provides offline tests and CLI structure first, then leaves online API validation as operational verification.
