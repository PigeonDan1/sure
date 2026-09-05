from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_TASKS = ("asr", "s2tt", "slu", "gr", "ser")

TASK_KEYWORDS = {
    "asr": (
        "asr",
        "automatic speech recognition",
        "automatic-speech-recognition",
        "auto-speech-recognition",
        "speech-to-text",
        "speech to text",
        "audio-to-text",
        "audio to text",
        "paraformer",
    ),
    "s2tt": ("s2tt", "speech to text translation", "speech-to-text translation", "speech-to-text-translation"),
    "slu": ("slu", "spoken language understanding", "spoken-language-understanding"),
    "gr": ("gr", "gender recognition", "gender-recognition"),
    "ser": (
        "ser",
        "speaker emotion recognition",
        "speaker-emotion-recognition",
        "speech emotion recognition",
        "speech-emotion-recognition",
    ),
}

SHORT_TASK_ABBREVIATION_GATES = {
    "gr": {
        "semantic": ("gender", "gender recognition", "gender-recognition"),
        "domain": ("audio", "speech", "voice", "speaker", "acoustic", "utterance"),
    },
}

MODELSCOPE_TASK_FILTERS = {
    "asr": {
        "model": {
            "api_params": {"search": "auto-speech-recognition", "sort": "last_modified"},
            "ui_params": {"tabKey": "task", "tasks": "auto-speech-recognition", "type": "audio"},
            "fallback_searches": ("asr", "speech"),
        },
        "dataset": {
            "api_params": {"search": "auto-speech-recognition", "sort": "last_modified"},
            "ui_params": {"Tags": "auto-speech-recognition", "dataType": "audio"},
            "fallback_searches": ("asr", "speech"),
        },
    },
    "s2tt": {
        "model": {
            "api_params": {"search": "speech-to-text-translation", "sort": "last_modified"},
            "ui_params": {"tabKey": "task", "tasks": "speech-to-text-translation", "type": "audio"},
            "fallback_searches": ("s2tt", "speech translation"),
        },
        "dataset": {
            "api_params": {"search": "speech-to-text-translation", "sort": "last_modified"},
            "ui_params": {"Tags": "speech-to-text-translation", "dataType": "audio"},
            "fallback_searches": ("s2tt", "speech translation"),
        },
    },
    "slu": {
        "model": {
            "api_params": {"search": "spoken-language-understanding", "sort": "last_modified"},
            "ui_params": {"tabKey": "task", "tasks": "spoken-language-understanding", "type": "audio"},
            "fallback_searches": ("slu", "spoken language"),
        },
        "dataset": {
            "api_params": {"search": "spoken-language-understanding", "sort": "last_modified"},
            "ui_params": {"Tags": "spoken-language-understanding", "dataType": "audio"},
            "fallback_searches": ("slu", "spoken language"),
        },
    },
    "gr": {
        "model": {
            "api_params": {"search": "gender-recognition", "sort": "last_modified"},
            "ui_params": {"tabKey": "task", "tasks": "gender-recognition", "type": "audio"},
            "fallback_searches": ("gr", "gender recognition"),
        },
        "dataset": {
            "api_params": {"search": "gender-recognition", "sort": "last_modified"},
            "ui_params": {"Tags": "gender-recognition", "dataType": "audio"},
            "fallback_searches": ("gr", "gender recognition"),
        },
    },
    "ser": {
        "model": {
            "api_params": {"search": "speaker-emotion-recognition", "sort": "last_modified"},
            "ui_params": {"tabKey": "task", "tasks": "speaker-emotion-recognition", "type": "audio"},
            "fallback_searches": ("ser", "speech emotion", "speaker emotion"),
        },
        "dataset": {
            "api_params": {"search": "speaker-emotion-recognition", "sort": "last_modified"},
            "ui_params": {"Tags": "speaker-emotion-recognition", "dataType": "audio"},
            "fallback_searches": ("ser", "speech emotion", "speaker emotion"),
        },
    },
}


def modelscope_task_filter(task: str, resource_type: str) -> dict[str, dict[str, str]]:
    task_filters = MODELSCOPE_TASK_FILTERS.get(task.lower(), {})
    resource_filter = task_filters.get(resource_type, {})
    return {
        "api_params": dict(resource_filter.get("api_params", {})),
        "ui_params": dict(resource_filter.get("ui_params", {})),
    }


def modelscope_task_queries(task: str, resource_type: str) -> list[dict[str, Any]]:
    task_filters = MODELSCOPE_TASK_FILTERS.get(task.lower(), {})
    resource_filter = task_filters.get(resource_type, {})
    api_params = dict(resource_filter.get("api_params", {}))
    ui_params = dict(resource_filter.get("ui_params", {}))
    queries: list[dict[str, Any]] = []
    if api_params:
        queries.append(
            {
                "match_source": "official_task",
                "api_params": api_params,
                "ui_params": ui_params,
            }
        )
    for search in resource_filter.get("fallback_searches", ()):
        fallback_params = dict(api_params)
        fallback_params["search"] = str(search)
        queries.append(
            {
                "match_source": "custom_tag_fallback",
                "api_params": fallback_params,
                "ui_params": ui_params,
            }
        )
    return queries or [{"match_source": "task_keyword", "api_params": {"search": task}, "ui_params": {}}]


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
    searchable = _candidate_searchable_text(candidate)

    abbreviation_gate = SHORT_TASK_ABBREVIATION_GATES.get(task)
    if abbreviation_gate:
        semantic_hit = any(
            _keyword_matches(searchable, keyword.lower())
            for keyword in abbreviation_gate["semantic"]
        )
        abbreviation_hit = _keyword_matches(searchable, task)
        domain_hit = any(
            _keyword_matches(searchable, keyword.lower())
            for keyword in abbreviation_gate["domain"]
        )
        score = 0
        if semantic_hit:
            score += 8
        if abbreviation_hit and domain_hit:
            score += 5
        return score

    score = 0
    if _keyword_matches(searchable, task):
        score += 5
    for keyword in keywords:
        if _keyword_matches(searchable, keyword.lower()):
            score += 3
    return score


def _candidate_searchable_text(candidate: dict[str, Any]) -> str:
    raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
    tags = raw.get("tags") or raw.get("Tags") or candidate.get("tags") or []
    if isinstance(tags, str):
        tags_text = tags
    elif isinstance(tags, list):
        tags_text = " ".join(str(item) for item in tags)
    else:
        tags_text = ""
    return " ".join(
        str(value or "")
        for value in (
            candidate.get("task"),
            candidate.get("name"),
            candidate.get("description"),
            candidate.get("summary"),
            raw.get("task"),
            raw.get("pipeline_tag"),
            raw.get("pipeline"),
            raw.get("tasks"),
            raw.get("name"),
            raw.get("display_name"),
            raw.get("description"),
            raw.get("summary"),
            tags_text,
        )
    ).lower()


def _keyword_matches(searchable: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    if len(keyword) <= 3 and keyword.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", searchable) is not None
    return keyword in searchable


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
        if candidate_copy["ranking"]["task_match_score"] <= 0:
            continue
        ranked.append(candidate_copy)
    return sorted(
        ranked,
        key=lambda item: (
            item["ranking"]["updated_on_report_date"],
            item["ranking"]["download_count"],
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
    lines = [
        f"- `{resource_id}` | {name} | downloads={downloads} | updated={updated}\n"
        f"  - Fetch: `{command}`"
    ]
    acquisition_filter = candidate.get("acquisition_filter")
    if isinstance(acquisition_filter, dict):
        api_params = acquisition_filter.get("api_params")
        ui_params = acquisition_filter.get("ui_params")
        if isinstance(api_params, dict) and api_params:
            lines.append(f"  - OpenAPI: `{_format_query_params(api_params)}`")
        if isinstance(ui_params, dict) and ui_params:
            lines.append(f"  - ModelScope page: `{_format_query_params(ui_params)}`")
        match_source = acquisition_filter.get("match_source")
        if match_source:
            lines.append(f"  - Match source: `{match_source}`")
    return "\n".join(lines)


def _format_query_params(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode([(str(key), str(value)) for key, value in params.items()])


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
        lines.extend(
            _render_candidate_section("Recommended Top 3 Datasets", task_summary["dataset"]["recommended"], task)
        )
        lines.extend(_render_candidate_section("Other Model Candidates", task_summary["model"]["other"], task))
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
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (str(candidate.get("resource_type")), str(candidate.get("resource_id")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


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
