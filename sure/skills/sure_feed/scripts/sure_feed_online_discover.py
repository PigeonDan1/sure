#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_feed.providers import (  # noqa: E402
    GitHubProvider,
    HuggingFaceProvider,
    ModelScopeProvider,
    ProviderError,
    ProviderRequest,
    infer_task,
    slugify,
    synthesize_model_input,
    to_yaml,
)
from sure_feed.providers.modelscope import DEFAULT_MODELSCOPE_API_BASE  # noqa: E402

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None


INTERNAL_ARTIFACTS = [
    "scan_result.json",
    "match_task_result.json",
    "metadata_result.json",
    "oref_result.json",
    "model_input_result.json",
    "rank_select_result.json",
    "handoff_manifest.json",
]


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_root_from(start: Path) -> Path:
    base = start.resolve() if start.exists() else start.resolve().parent
    for path in (base, *base.parents):
        if (path / "sure").is_dir() and (path / "packages").is_dir():
            return path
    return Path.cwd()


def repo_root() -> Path:
    return repo_root_from(Path(__file__).resolve())


def normalize_run_dir(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (repo_root() / path).resolve()


def default_handoff_root(run_dir: Path) -> Path:
    return repo_root_from(run_dir) / "sure" / "handoffs"


def reference_model_roots() -> list[Path]:
    roots: list[Path] = []
    env_value = os.environ.get("SURE_FEED_REFERENCE_MODEL_ROOTS", "")
    for raw in env_value.split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw).expanduser())
    roots.extend(
        [
            Path("/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src/sure_eval/models"),
            Path("/mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox/src/sure_eval/models"),
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def read_yaml_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or yaml is None:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def nested_get(data: dict[str, Any] | None, *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def reference_name_candidates(candidate: dict[str, Any]) -> list[str]:
    names = {
        slugify(str(candidate.get("model_id") or "")),
        slugify(str(candidate.get("model_name") or "")),
    }
    repo = str(candidate.get("repo") or candidate.get("source_url") or "")
    parsed = urlparse(repo)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2:
        names.add(slugify("/".join(parts[:2])))
    return [name for name in names if name and name != "model"]


def find_reference_model(candidate: dict[str, Any]) -> dict[str, Any] | None:
    for root in reference_model_roots():
        if not root.exists():
            continue
        for name in reference_name_candidates(candidate):
            model_dir = root / name
            if not model_dir.is_dir():
                continue
            spec = read_yaml_if_exists(model_dir / "model.spec.yaml")
            weights_manifest = read_json_if_exists(model_dir / "artifacts" / "weights_manifest.json")
            verdict = read_json_if_exists(model_dir / "artifacts" / "verdict.json")
            if spec or weights_manifest or verdict:
                return {
                    "model_dir": str(model_dir),
                    "model_name": name,
                    "spec": spec or {},
                    "weights_manifest": weights_manifest or {},
                    "verdict": verdict or {},
                }
    return None


def reference_local_path(reference: dict[str, Any]) -> str | None:
    model_dir = Path(str(reference["model_dir"]))
    spec = reference.get("spec") if isinstance(reference.get("spec"), dict) else {}
    weights_manifest = reference.get("weights_manifest") if isinstance(reference.get("weights_manifest"), dict) else {}
    candidates = [
        nested_get(spec, "weights", "local_path"),
        weights_manifest.get("resolved_local_model_path"),
        weights_manifest.get("runtime_load_identity"),
        weights_manifest.get("local_path"),
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = model_dir / path
        if path.exists():
            return str(path)
    return None


def seed_candidate_from_reference(candidate: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    spec = reference.get("spec") if isinstance(reference.get("spec"), dict) else {}
    entrypoints = spec.get("entrypoints") if isinstance(spec.get("entrypoints"), dict) else {}
    seeded = dict(candidate)
    for field in ("import_test", "load_test", "infer_test"):
        value = entrypoints.get(field)
        if isinstance(value, str) and value.strip():
            seeded[field] = value
    local_path = reference_local_path(reference)
    if local_path:
        seeded["local_path"] = local_path
    weights_source = nested_get(spec, "weights", "source")
    if isinstance(weights_source, str) and weights_source.strip():
        seeded["weights_source"] = weights_source.strip()
    repo_url = nested_get(spec, "repo", "url")
    if isinstance(repo_url, str) and repo_url.strip():
        seeded["repo"] = repo_url.strip()
    repo_commit = nested_get(spec, "repo", "commit")
    if isinstance(repo_commit, str) and repo_commit.strip():
        seeded["commit"] = repo_commit.strip()
    seeded["reference_model_dir"] = reference["model_dir"]
    return seeded


def append_reference_evidence(
    evidence_items: list[dict[str, Any]],
    field: str,
    value: Any,
    reference: dict[str, Any],
) -> None:
    evidence_items.append(
        {
            "source": "local",
            "field": "reference_model",
            "value": value,
            "strength": "strong",
            "url": None,
            "model_input_field": field,
            "evidence_path": reference["model_dir"],
        }
    )


def apply_reference_to_model_input(
    model_input: dict[str, Any],
    missing_or_weak: list[str],
    evidence_items: list[dict[str, Any]],
    reference: dict[str, Any] | None,
) -> None:
    if not reference:
        return
    spec = reference.get("spec") if isinstance(reference.get("spec"), dict) else {}
    weights = spec.get("weights") if isinstance(spec.get("weights"), dict) else {}
    env = spec.get("environment") if isinstance(spec.get("environment"), dict) else {}
    entrypoints = spec.get("entrypoints") if isinstance(spec.get("entrypoints"), dict) else {}

    model_input.setdefault("reference", {})["original_model_dir"] = reference["model_dir"]
    append_reference_evidence(evidence_items, "reference.original_model_dir", reference["model_dir"], reference)

    repo_url = nested_get(spec, "repo", "url")
    if isinstance(repo_url, str) and repo_url.strip():
        model_input.setdefault("repo", {})["url"] = repo_url.strip()
        append_reference_evidence(evidence_items, "repo.url", repo_url.strip(), reference)
    repo_commit = nested_get(spec, "repo", "commit")
    if repo_commit is not None:
        model_input.setdefault("repo", {})["commit"] = repo_commit
        append_reference_evidence(evidence_items, "repo.commit", repo_commit, reference)

    if isinstance(weights.get("source"), str) and weights["source"].strip():
        model_input.setdefault("weights", {})["source"] = weights["source"].strip()
        append_reference_evidence(evidence_items, "weights.source", weights["source"].strip(), reference)
    if isinstance(weights.get("repo_id"), str) and weights["repo_id"].strip():
        model_input.setdefault("weights", {})["repo_id"] = weights["repo_id"].strip()
        append_reference_evidence(evidence_items, "weights.repo_id", weights["repo_id"].strip(), reference)
    local_path = reference_local_path(reference)
    if local_path:
        model_input.setdefault("weights", {})["local_path"] = local_path
        model_input["weights"]["reference_model_dir"] = reference["model_dir"]
        model_input["weights"]["reuse_policy"] = "asset_level_symlink_or_copy"
        append_reference_evidence(evidence_items, "weights.local_path", local_path, reference)
        missing_or_weak[:] = [item for item in missing_or_weak if item != "missing:weights.local_path"]

    environment_hint = model_input.setdefault("environment_hint", {})
    for field in ("preferred_backend", "python_version", "requires_gpu", "system_packages"):
        value = env.get(field)
        if value is not None:
            environment_hint[field] = value
            append_reference_evidence(evidence_items, f"environment_hint.{field}", value, reference)
            missing_or_weak[:] = [item for item in missing_or_weak if item != f"missing:environment_hint.{field}"]
    if env:
        environment_hint["source"] = "reference_model_spec"

    for field in ("import_test", "load_test", "infer_test"):
        value = entrypoints.get(field)
        if isinstance(value, str) and value.strip():
            model_input.setdefault("entrypoints", {})[field] = value
            append_reference_evidence(evidence_items, f"entrypoints.{field}", value, reference)
            missing_or_weak[:] = [item for item in missing_or_weak if item != f"missing:entrypoints.{field}"]

    target = spec.get("phase1_runtime_target")
    if isinstance(target, str) and target.strip():
        model_input["phase1_runtime_target"] = target
        append_reference_evidence(evidence_items, "phase1_runtime_target", target, reference)
        missing_or_weak[:] = [item for item in missing_or_weak if item != "missing:phase1_runtime_target"]
    elif isinstance(target, dict):
        values: list[str] = []
        description = target.get("description")
        if isinstance(description, str) and description.strip():
            values.append(description.strip())
        includes = target.get("includes")
        if isinstance(includes, list):
            values.extend(str(item) for item in includes if str(item).strip())
        if values:
            model_input["phase1_runtime_target"] = values
            append_reference_evidence(evidence_items, "phase1_runtime_target", values, reference)
            missing_or_weak[:] = [item for item in missing_or_weak if item != "missing:phase1_runtime_target"]


def prepare_artifact_dirs(artifacts: Path) -> Path:
    artifacts.mkdir(parents=True, exist_ok=True)
    debug = artifacts / "debug"
    debug.mkdir(parents=True, exist_ok=True)
    # New runs expose only model_input.yaml and feed_report.json at the top
    # level. Remove stale root-level skeleton JSONs from older reruns so hooks do
    # not prefer obsolete artifacts over artifacts/debug/*.json.
    for name in INTERNAL_ARTIFACTS:
        legacy = artifacts / name
        if legacy.exists() and legacy.is_file():
            legacy.unlink()
    return debug


def source_list(source: str) -> list[str]:
    return ["modelscope", "huggingface", "github"] if source == "multi" else [source]


def parse_model_url(value: str) -> dict[str, str] | None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"huggingface.co", "hf-mirror.com"} and len(parts) >= 2:
        return {
            "source": "huggingface",
            "model_id": "/".join(parts[:2]),
            "canonical_url": f"https://huggingface.co/{'/'.join(parts[:2])}",
            "input_url": value,
        }
    if host == "modelscope.cn" and len(parts) >= 3 and parts[0] == "models":
        return {
            "source": "modelscope",
            "model_id": "/".join(parts[1:3]),
            "canonical_url": f"https://modelscope.cn/models/{'/'.join(parts[1:3])}",
            "input_url": value,
        }
    if host == "github.com" and len(parts) >= 2:
        return {
            "source": "github",
            "model_id": "/".join(parts[:2]),
            "canonical_url": f"https://github.com/{'/'.join(parts[:2])}",
            "input_url": value,
        }
    raise ValueError(f"unsupported direct model URL: {value}")


def build_provider(name: str, args: argparse.Namespace):
    if name == "modelscope":
        return ModelScopeProvider(api_base=args.modelscope_api_base, since_days=args.modelscope_since_days)
    if name == "huggingface":
        return HuggingFaceProvider(endpoint_mode=args.hf_endpoint, timeout=args.timeout)
    if name == "github":
        return GitHubProvider(token=args.github_token, timeout=args.timeout)
    raise ValueError(f"unsupported source: {name}")


def discover_direct(args: argparse.Namespace, parsed_url: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = parsed_url["source"]
    provider = build_provider(source, args)
    try:
        if source == "huggingface":
            candidate = provider.direct(parsed_url["model_id"])
        elif source == "modelscope":
            candidate = provider.direct(parsed_url["model_id"], task_hint=args.task)
        elif source == "github":
            candidate = provider.direct(parsed_url["model_id"])
        else:
            raise ValueError(f"unsupported direct source: {source}")
        candidate["repo"] = parsed_url["canonical_url"]
        candidate["source_url"] = parsed_url["canonical_url"]
        candidate["input_url"] = parsed_url["input_url"]
        return [candidate], [
            {
                "source": source,
                "status": "ok",
                "candidate_count": 1,
                "endpoint_used": getattr(provider, "last_endpoint_used", None) or candidate.get("endpoint_used"),
                "fallback_reason": getattr(provider, "fallback_reason", None),
                "mode": "direct_url",
            }
        ]
    except ProviderError as exc:
        if args.fail_fast:
            raise
        return [], [{"source": source, "status": "error", "message": str(exc), "mode": "direct_url"}]


def discover(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    per_source_limit = max(1, args.max_models)
    for name in source_list(args.source):
        provider = build_provider(name, args)
        request = ProviderRequest(source=name, query=args.query or args.task, task=args.task, max_models=per_source_limit)
        try:
            found = provider.search(request)
            candidates.extend(found)
            diagnostics.append(
                {
                    "source": name,
                    "status": "ok",
                    "candidate_count": len(found),
                    "endpoint_used": getattr(provider, "last_endpoint_used", None),
                    "fallback_reason": getattr(provider, "fallback_reason", None),
                }
            )
        except ProviderError as exc:
            diagnostics.append({"source": name, "status": "error", "message": str(exc)})
            if args.fail_fast:
                raise
    return candidates[: args.max_models] if args.source != "multi" else candidates, diagnostics


def scan_result(args: argparse.Namespace, candidates: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    source = getattr(args, "effective_source", args.source)
    mode = "direct_url" if getattr(args, "direct_url", None) else "search"
    return {
        "source": source,
        "candidates": [
            {
                "model_id": item["model_id"],
                "source": item.get("source"),
                "repo": item.get("repo"),
                "source_url": item.get("source_url"),
                "tasks": item.get("tasks") or [],
                "custom_tag": None,
                "pipeline_tag": item.get("pipeline_tag"),
                "tags": item.get("tags") or [],
                "description": item.get("description"),
                "license": item.get("license"),
                "download_count": item.get("download_count") or item.get("stars") or 0,
            }
            for item in candidates
        ],
        "scan_summary": f"Discovered {len(candidates)} candidate(s) from {source} in {mode} mode without downloading weights.",
        "diagnostics": diagnostics,
    }


def match_result(args: argparse.Namespace, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for item in candidates:
        matched, task_type, score, evidence_items, match_source = infer_task(item, args.task)
        row = {
            "model_id": item["model_id"],
            "source": item.get("source"),
            "repo": item.get("repo"),
            "match": {
                "matched": matched,
                "task_type": task_type,
                "score": score,
            },
        }
        if matched:
            row["match"]["match_source"] = match_source
            row["match"]["evidence"] = evidence_items
        rows.append(row)
        by_id[item["model_id"]] = {"matched": matched, "task_type": task_type, "score": score, "evidence": evidence_items}
    return {"candidates": rows}, by_id


def metadata_result(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "models": [
            {
                "model_id": item["model_id"],
                "source": item.get("source"),
                "repo": item.get("repo"),
                "source_url": item.get("source_url"),
                "weights_source": item.get("weights_source") or item.get("github_weights_source"),
                "license": item.get("license"),
                "frameworks": item.get("frameworks") or [],
                "entrypoint_hints": {
                    "import_test": item.get("import_test"),
                    "load_test": item.get("load_test"),
                    "infer_test": item.get("infer_test"),
                },
                "environment_hints": {
                    "preferred_backend": item.get("preferred_backend") or "uv",
                    "requires_gpu": item.get("requires_gpu", True),
                },
                "metadata_summary": str(item.get("description") or item.get("github_weights_source_reason") or "")[:500],
            }
            for item in candidates
        ]
    }


def oref_result(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "converted": [
            {
                "model_id": item["model_id"],
                "oref_path": f"unmaterialized://{item.get('source')}/{item['model_id']}",
                "weights_path": None,
            }
            for item in candidates
        ]
    }


def model_input_result(
    args: argparse.Namespace,
    run_dir: Path,
    candidates: list[dict[str, Any]],
    match_by_id: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    model_input_path = run_dir / "artifacts" / "model_input.yaml"
    entries: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in candidates:
        match = match_by_id.get(item["model_id"], {})
        if not match.get("matched"):
            continue
        task_type = str(match.get("task_type") or args.task)
        reference = find_reference_model(item)
        synthesis_item = seed_candidate_from_reference(item, reference) if reference else item
        model_input, weak_fields, field_evidence = synthesize_model_input(synthesis_item, task_type)
        apply_reference_to_model_input(model_input, weak_fields, field_evidence, reference)
        model_input_path.parent.mkdir(parents=True, exist_ok=True)
        model_input_path.write_text(to_yaml(model_input) + "\n", encoding="utf-8")
        rel_path = str(model_input_path.relative_to(run_dir))
        evidence_items = list(match.get("evidence") or [])
        endpoint_used = item.get("endpoint_used")
        if endpoint_used:
            evidence_items.append(
                {
                    "source": item.get("source"),
                    "field": "endpoint_used",
                    "value": endpoint_used,
                    "strength": "medium",
                }
            )
        if reference:
            evidence_items.append(
                {
                    "source": "local",
                    "field": "reference_model_dir",
                    "value": reference["model_dir"],
                    "strength": "strong",
                    "model_input_field": "reference.original_model_dir",
                }
            )
        evidence_items.extend(field_evidence)
        entry = {
            "model_id": item["model_id"],
            "source": item.get("source"),
            "model_input_path": rel_path,
            "model_input": model_input,
            "evidence": evidence_items,
            "confidence": {
                "task_match": match.get("score", 0),
                "weights_source": 0.6 if any(field.startswith("weak:weights.source") for field in weak_fields) else 0.9,
            },
            "missing_or_weak_fields": weak_fields,
        }
        entries.append(entry)
        by_id[item["model_id"]] = entry
    return {
        "source_query": {
            "source": getattr(args, "effective_source", args.source),
            "query": args.query,
            "url": getattr(args, "direct_url", None),
            "task": args.task,
            "max_models": args.max_models,
            "download": False,
        },
        "model_inputs": entries,
        "summary": f"Synthesized {len(entries)} MODEL_INPUT object(s) without downloading weights.",
    }, by_id


def rank_score(candidate: dict[str, Any], match: dict[str, Any]) -> float:
    popularity = candidate.get("download_count") or candidate.get("stars") or 0
    try:
        popularity_score = math.log10(float(popularity) + 1.0) / 10.0
    except (TypeError, ValueError):
        popularity_score = 0.0
    return round(float(match.get("score") or 0.0) + popularity_score, 4)


def rank_select_result(
    candidates: list[dict[str, Any]],
    match_by_id: dict[str, Any],
    input_by_id: dict[str, dict[str, Any]],
    max_selected: int,
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for item in candidates:
        model_input_entry = input_by_id.get(item["model_id"])
        match = match_by_id.get(item["model_id"], {})
        if not model_input_entry:
            continue
        model_input = model_input_entry["model_input"]
        selected.append(
            {
                "model_id": item["model_id"],
                "repo": item.get("repo"),
                "weights_source": model_input["weights"]["source"],
                "task_type": model_input["task_type"],
                "score": rank_score(item, match),
                "rank_reason": "online no-download metadata match",
                "model_input_path": model_input_entry["model_input_path"],
                "model_input": model_input,
            }
        )
    selected.sort(key=lambda row: row["score"], reverse=True)
    return {"selected": selected[:max_selected], "ranking_summary": "Ranked by task match plus log popularity."}


def handoff_manifest(
    args: argparse.Namespace,
    run_dir: Path,
    rank_result: dict[str, Any],
    handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_path = run_dir / "artifacts" / "debug" / "handoff_manifest.json"
    model_input_path = (
        handoff.get("handoff_model_input_path")
        if handoff
        else str(run_dir / "artifacts" / "model_input.yaml")
    )
    handoff_model = Path(handoff["handoff_dir"]).name if handoff and handoff.get("handoff_dir") else None
    next_action = (
        f"run /sure_onboard model={handoff_model}"
        if handoff_model
        else f"run /sure_onboard model_input_path={model_input_path}"
    )
    return {
        "manifest_path": str(manifest_path),
        "source": getattr(args, "effective_source", args.source),
        "models": rank_result.get("selected", []),
        "next_action": next_action,
    }


def publish_handoff(
    args: argparse.Namespace,
    run_dir: Path,
    model_inputs: dict[str, Any],
    rank: dict[str, Any],
    debug_artifacts: Path,
) -> dict[str, Any] | None:
    selected = rank.get("selected") or []
    if not selected:
        return None
    entry = (model_inputs.get("model_inputs") or [None])[0]
    if not isinstance(entry, dict) or entry.get("missing_or_weak_fields"):
        return None
    model_input = entry.get("model_input")
    if not isinstance(model_input, dict):
        return None
    model_name = str(model_input.get("model_name") or slugify(str(model_input.get("model_id") or "model")))
    handoff_root = Path(args.handoff_root)
    if not handoff_root.is_absolute():
        handoff_root = repo_root_from(run_dir) / handoff_root
    handoff_dir = handoff_root / model_name
    handoff_artifacts = handoff_dir / "artifacts"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_artifacts.mkdir(parents=True, exist_ok=True)

    handoff_model_input = handoff_dir / "model_input.yaml"
    handoff_model_input.write_text(to_yaml(model_input) + "\n", encoding="utf-8")
    for name in INTERNAL_ARTIFACTS:
        source_path = debug_artifacts / name
        if source_path.exists():
            shutil.copy2(source_path, handoff_artifacts / name)
    source_run = {
        "run_dir": str(run_dir),
        "run_artifacts_dir": str(run_dir / "artifacts"),
        "source": getattr(args, "effective_source", args.source),
        "query": args.query,
        "url": getattr(args, "direct_url", None),
        "model_id": model_input.get("model_id"),
        "model_name": model_name,
    }
    write_json(handoff_artifacts / "source_run.json", source_run)
    return {
        "handoff_dir": str(handoff_dir),
        "handoff_model_input_path": str(handoff_model_input),
        "handoff_artifacts_dir": str(handoff_artifacts),
    }


def feed_report(
    args: argparse.Namespace,
    run_dir: Path,
    candidates: list[dict[str, Any]],
    matched_candidates: list[dict[str, Any]],
    model_inputs: dict[str, Any],
    rank: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = rank.get("selected") or []
    selected_model = selected[0] if selected else None
    model_input_entries = model_inputs.get("model_inputs") or []
    model_input_entry = model_input_entries[0] if model_input_entries else None
    model_input = model_input_entry.get("model_input") if isinstance(model_input_entry, dict) else {}
    confidence = model_input_entry.get("confidence") if isinstance(model_input_entry, dict) else None
    missing_or_weak = model_input_entry.get("missing_or_weak_fields") if isinstance(model_input_entry, dict) else None
    evidence = model_input_entry.get("evidence") if isinstance(model_input_entry, dict) else None
    ready = bool(selected_model and model_input and not missing_or_weak)
    resolved_model_input_path = (
        handoff.get("handoff_model_input_path")
        if ready and handoff
        else str(run_dir / "artifacts" / "model_input.yaml")
        if ready
        else None
    )
    handoff_model = Path(handoff["handoff_dir"]).name if ready and handoff and handoff.get("handoff_dir") else None
    next_action = (
        f"/sure_onboard model={handoff_model}"
        if handoff_model
        else f"/sure_onboard model_input_path={resolved_model_input_path}"
        if ready
        else None
    )
    selected_summary = None
    if selected_model:
        selected_summary = {
            "model_id": selected_model.get("model_id"),
            "model_name": model_input.get("model_name") if isinstance(model_input, dict) else None,
            "source": model_input_entry.get("source") if isinstance(model_input_entry, dict) else None,
            "repo": selected_model.get("repo"),
            "task_type": selected_model.get("task_type"),
            "deployment_type": model_input.get("deployment_type") if isinstance(model_input, dict) else None,
            "weights_source": selected_model.get("weights_source"),
            "score": selected_model.get("score"),
            "confidence": confidence,
            "missing_or_weak_fields": missing_or_weak or [],
            "evidence": evidence or [],
            "handoff": handoff,
        }
    return {
        "status": "ready_for_onboard" if ready else "blocked_needs_research" if selected_model else "no_selection",
        "model_input_path": resolved_model_input_path if ready else None,
        "resolved_model_input_path": resolved_model_input_path,
        "next_action": next_action,
        "source": getattr(args, "effective_source", args.source),
        "query": args.query,
        "url": getattr(args, "direct_url", None),
        "task": args.task,
        "counts": {
            "candidates": len(candidates),
            "matched": len(matched_candidates),
            "model_inputs": len(model_input_entries),
            "selected": len(selected),
        },
        "selected": selected_summary,
        "handoff": handoff,
        "diagnostics": diagnostics,
        "debug_artifacts": [f"artifacts/debug/{name}" for name in INTERNAL_ARTIFACTS],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Online no-download discovery for /sure_feed")
    parser.add_argument("input", nargs="?", default="", help="Direct model URL or search query")
    parser.add_argument("--url", default="", help="Direct HuggingFace, ModelScope, or GitHub model URL")
    parser.add_argument("--source", choices=["modelscope", "huggingface", "github", "multi"], default="multi")
    parser.add_argument("--query", default="")
    parser.add_argument("--task", default="auto")
    parser.add_argument("--max-models", type=int, default=3)
    parser.add_argument("--max-selected", type=int, default=1)
    parser.add_argument("--run-dir", default="sure/compat/sure_feed_online_discover")
    parser.add_argument("--handoff-root", default="", help="Directory for /sure_onboard handoff folders. Default: sure/handoffs")
    parser.add_argument("--hf-endpoint", default="auto", help="auto | huggingface | hf-mirror | explicit URL")
    parser.add_argument("--github-token")
    parser.add_argument("--modelscope-api-base", default=DEFAULT_MODELSCOPE_API_BASE)
    parser.add_argument("--modelscope-since-days", type=int, default=3650)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--summary")
    args = parser.parse_args()
    if args.input and not args.query and not args.url:
        parsed_input = parse_model_url(args.input)
        if parsed_input:
            args.url = args.input
        else:
            args.query = args.input
    parsed_url = parse_model_url(args.url) if args.url else None
    if parsed_url:
        args.effective_source = parsed_url["source"]
        args.direct_url = parsed_url["canonical_url"]
        args.max_selected = 1
    else:
        args.effective_source = args.source
        args.direct_url = None
    if args.task == "auto" and not parsed_url:
        args.task = args.query or "asr"

    run_dir = normalize_run_dir(args.run_dir)
    args.run_dir = str(run_dir)
    if not args.handoff_root:
        args.handoff_root = str(default_handoff_root(run_dir))
    artifacts = run_dir / "artifacts"
    debug_artifacts = prepare_artifact_dirs(artifacts)

    candidates, diagnostics = discover_direct(args, parsed_url) if parsed_url else discover(args)
    scan = scan_result(args, candidates, diagnostics)
    write_json(debug_artifacts / "scan_result.json", scan)
    if not candidates and not args.allow_empty:
        write_json(
            artifacts / "feed_report.json",
            feed_report(args, run_dir, candidates, [], {"model_inputs": []}, {"selected": []}, diagnostics),
        )
        print("ERROR: no candidates discovered; wrote artifacts/debug/scan_result.json", file=sys.stderr)
        return 3

    match, match_by_id = match_result(args, candidates)
    write_json(debug_artifacts / "match_task_result.json", match)
    matched_candidates = [item for item in candidates if match_by_id.get(item["model_id"], {}).get("matched")]
    matched_candidates.sort(key=lambda item: rank_score(item, match_by_id.get(item["model_id"], {})), reverse=True)
    selected_candidates = matched_candidates[:1]
    write_json(debug_artifacts / "metadata_result.json", metadata_result(selected_candidates))
    write_json(debug_artifacts / "oref_result.json", oref_result(selected_candidates))
    model_inputs, input_by_id = model_input_result(args, run_dir, selected_candidates, match_by_id)
    write_json(debug_artifacts / "model_input_result.json", model_inputs)
    if not model_inputs["model_inputs"] and not args.allow_empty:
        write_json(
            artifacts / "feed_report.json",
            feed_report(args, run_dir, candidates, matched_candidates, model_inputs, {"selected": []}, diagnostics),
        )
        print("ERROR: no matched candidates could produce MODEL_INPUT; wrote partial artifacts", file=sys.stderr)
        return 4

    rank = rank_select_result(selected_candidates, match_by_id, input_by_id, 1)
    write_json(debug_artifacts / "rank_select_result.json", rank)
    published_handoff = publish_handoff(args, run_dir, model_inputs, rank, debug_artifacts)
    if published_handoff:
        for item in rank.get("selected") or []:
            if isinstance(item, dict):
                item["model_input_path"] = published_handoff["handoff_model_input_path"]
        write_json(debug_artifacts / "rank_select_result.json", rank)
        shutil.copy2(
            debug_artifacts / "rank_select_result.json",
            Path(published_handoff["handoff_artifacts_dir"]) / "rank_select_result.json",
        )
    handoff = handoff_manifest(args, run_dir, rank, published_handoff)
    write_json(debug_artifacts / "handoff_manifest.json", handoff)
    if published_handoff:
        shutil.copy2(debug_artifacts / "handoff_manifest.json", Path(published_handoff["handoff_artifacts_dir"]) / "handoff_manifest.json")
    report = feed_report(args, run_dir, candidates, matched_candidates, model_inputs, rank, diagnostics, published_handoff)
    write_json(artifacts / "feed_report.json", report)
    if published_handoff:
        shutil.copy2(artifacts / "feed_report.json", Path(published_handoff["handoff_artifacts_dir"]) / "feed_report.json")

    summary = {
        "run_dir": str(run_dir),
        "artifacts_dir": str(artifacts),
        "model_input_path": published_handoff["handoff_model_input_path"] if published_handoff else str(artifacts / "model_input.yaml") if rank["selected"] else None,
        "handoff_dir": published_handoff["handoff_dir"] if published_handoff else None,
        "feed_report_path": str(artifacts / "feed_report.json"),
        "source": getattr(args, "effective_source", args.source),
        "query": args.query,
        "url": getattr(args, "direct_url", None),
        "task": args.task,
        "candidate_count": len(candidates),
        "matched_count": len(matched_candidates),
        "model_input_count": len(model_inputs["model_inputs"]),
        "selected_count": len(rank["selected"]),
        "diagnostics": diagnostics,
    }
    if args.summary:
        write_json(Path(args.summary), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
