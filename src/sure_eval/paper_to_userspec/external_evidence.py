"""External evidence collection for Paper_to_UserSpec.

Collectors in this module only emit evidence backed by observed local files or
real remote responses. They do not infer capabilities from paper text or from
model names.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .io import write_json

ExternalEvidenceMode = str

README_NAMES = {"readme.md", "readme.rst", "readme.txt", "readme"}
ENVIRONMENT_FILES = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "environment.yml", "environment.yaml"}
INFERENCE_FILES = {"demo.py", "infer.py", "inference.py", "predict.py", "cli.py", "app.py"}
WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth", ".ckpt"}
MODEL_CARD_DOMAINS = ("huggingface.co", "modelscope.cn", "modelscope.ai")
KIND_TO_FIELD = {
    "repo_access": "repo_metadata.url",
    "repo_commit": "repo.commit",
    "repo_readme": "repo.has_readme_or_docs",
    "repo_docs": "repo.has_readme_or_docs",
    "repo_install_instructions": "repo.has_install_or_environment_files",
    "repo_dependency_file": "repo.has_requirements_or_dependency_lock",
    "repo_inference_entrypoint": "repo.has_inference_entrypoint_or_example",
    "repo_inference_example": "repo.has_inference_entrypoint_or_example",
    "repo_training_script": "repo.has_training_script",
    "repo_eval_script": "repo.has_eval_script_or_metric_claim_mapping",
    "repo_metric_mapping": "repo.has_eval_script_or_metric_claim_mapping",
    "repo_license": "repo.has_license",
    "repo_checkpoint_declared": "repo.checkpoint",
    "repo_model_card_declared": "repo.checkpoint",
    "repo_weight_file_observed": "repo.checkpoint",
    "repo_dataset_preparation": "repo.dataset_preparation",
    "repo_dataset_download": "repo.dataset_download",
    "repo_feature_extraction": "repo.feature_extraction",
    "repo_demo": "repo.has_inference_entrypoint_or_example",
    "repo_example": "repo.has_readme_or_docs",
    "model_card_access": "source.model_card_url",
    "model_card_usage": "source.model_card_url",
    "model_card_weight_file": "model.checkpoint_source",
    "model_card_license": "model.license",
    "model_card_task_tag": "source.model_card_url",
    "unknown": "repo.unknown",
}
KIND_TO_CRITERIA = {
    "repo_access": ["repo_url_verified"],
    "repo_commit": ["repo_url_verified", "repo_has_version_or_commit_pin"],
    "repo_readme": ["repo_has_readme_or_docs"],
    "repo_docs": ["repo_has_readme_or_docs"],
    "repo_install_instructions": ["repo_has_install_or_environment_files"],
    "repo_dependency_file": ["repo_has_install_or_environment_files", "repo_has_requirements_or_dependency_lock"],
    "repo_inference_entrypoint": ["repo_has_inference_entrypoint_or_example"],
    "repo_inference_example": ["repo_has_inference_entrypoint_or_example"],
    "repo_demo": ["repo_has_inference_entrypoint_or_example"],
    "repo_eval_script": ["repo_has_eval_script_or_metric_claim_mapping"],
    "repo_metric_mapping": ["repo_has_eval_script_or_metric_claim_mapping"],
    "repo_license": ["repo_has_license"],
    "repo_checkpoint_declared": ["repo_has_checkpoint_or_model_card"],
    "repo_model_card_declared": ["repo_has_checkpoint_or_model_card"],
    "repo_weight_file_observed": ["repo_has_checkpoint_or_model_card"],
    "model_card_access": ["repo_has_checkpoint_or_model_card"],
    "model_card_weight_file": ["repo_has_checkpoint_or_model_card"],
    "model_card_license": ["repo_has_license"],
}
DATASET_NAMES = r"common voice|librispeech|iemocap|voxceleb|musan|dns|aishell|ami|callhome|audioset|covost"


@dataclass
class ExternalEvidenceResult:
    items: list[dict[str, Any]]
    repo_summary: dict[str, Any]
    model_card_summary: dict[str, Any]
    review_summary: dict[str, Any]
    warnings: list[str]
    failed: bool = False


def collect_external_evidence(
    *,
    user_spec: dict[str, Any],
    mode: ExternalEvidenceMode,
    cache_dir: str | Path,
    repo_local_path: str | Path | None = None,
    timeout_sec: int = 20,
) -> ExternalEvidenceResult:
    """Collect repo/model-card evidence and write summary JSON files."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    repo_summary: dict[str, Any] = _empty_repo_summary(user_spec, mode)
    model_card_summary: dict[str, Any] = _empty_model_card_summary(user_spec, mode)
    review_summary: dict[str, Any] = {
        "source_type": "review_summary",
        "access_status": "not_collected",
        "warnings": ["review collection is not implemented; use --review-evidence-json"],
        "items": [],
    }

    if mode == "off":
        warnings.append("external evidence collection disabled")
    else:
        repo_result = _collect_repo_evidence(
            repo_url=user_spec.get("source", {}).get("repo_url"),
            mode=mode,
            cache_dir=cache,
            repo_local_path=repo_local_path,
            timeout_sec=timeout_sec,
        )
        repo_summary = repo_result["summary"]
        items.extend(repo_result["items"])
        warnings.extend(repo_result["warnings"])

        model_urls = _model_card_urls(user_spec, repo_summary)
        if model_urls:
            card_result = _collect_model_card_evidence(
                model_card_url=model_urls[0],
                mode=mode,
                cache_dir=cache,
                timeout_sec=timeout_sec,
            )
            model_card_summary = card_result["summary"]
            items.extend(card_result["items"])
            warnings.extend(card_result["warnings"])
        else:
            model_card_summary["access_status"] = "not_found"
            model_card_summary["warnings"] = ["no model_card_url or model-card link discovered"]

    write_json(cache / "external_evidence.json", {"items": items, "warnings": warnings})
    write_json(cache / "repo_summary.json", repo_summary)
    write_json(cache / "model_card_summary.json", model_card_summary)
    write_json(cache / "review_summary.json", review_summary)
    return ExternalEvidenceResult(
        items=items,
        repo_summary=repo_summary,
        model_card_summary=model_card_summary,
        review_summary=review_summary,
        warnings=_unique(warnings),
        failed=_collection_failed(mode, repo_summary, model_card_summary),
    )


def _collect_repo_evidence(
    *,
    repo_url: str | None,
    mode: str,
    cache_dir: Path,
    repo_local_path: str | Path | None,
    timeout_sec: int,
) -> dict[str, Any]:
    retrieved_at = _now()
    warnings: list[str] = []
    if repo_local_path:
        local_path = Path(repo_local_path)
        if local_path.exists():
            return _scan_local_repo(local_path, repo_url, retrieved_at)
        warnings.append(f"repo local path does not exist: {local_path}")
        if mode in {"local-only", "required"}:
            return {
                "summary": _repo_fail(repo_url, "local_path", retrieved_at, warnings),
                "items": [],
                "warnings": warnings,
            }
    if mode == "local-only":
        warnings.append("external evidence local-only mode has no usable --repo-local-path")
        return {"summary": _repo_fail(repo_url, "local_path", retrieved_at, warnings), "items": [], "warnings": warnings}
    if not repo_url:
        warnings.append("no repo_url available for external evidence collection")
        return {"summary": _repo_fail(repo_url, "none", retrieved_at, warnings), "items": [], "warnings": warnings}
    if "github.com" in repo_url.lower():
        api = _github_api_summary(repo_url, timeout_sec, retrieved_at)
        if api["summary"].get("access_status") == "ok":
            return api
        warnings.extend(api["warnings"])
        clone = _try_shallow_clone(repo_url, cache_dir / "repo", timeout_sec)
        if clone["ok"]:
            return _scan_local_repo(cache_dir / "repo", repo_url, retrieved_at, method="git_clone")
        warnings.extend(clone["warnings"])
    warnings.append("remote repo collection failed or unsupported")
    return {"summary": _repo_fail(repo_url, "remote", retrieved_at, warnings), "items": [], "warnings": _unique(warnings)}


def _scan_local_repo(
    path: Path,
    repo_url: str | None,
    retrieved_at: str,
    *,
    method: str = "local_path",
) -> dict[str, Any]:
    warnings: list[str] = []
    files = [file for file in path.rglob("*") if file.is_file() and ".git" not in file.parts]
    names = {file.name.lower(): file for file in files}
    readme = _first_existing_name(names, README_NAMES)
    license_file = _first_name_contains(names, "license")
    snippets: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    source_scope = _source_scope(path, repo_url)

    file_flags = {
        "readme": readme is not None,
        "requirements": "requirements.txt" in names,
        "pyproject": "pyproject.toml" in names,
        "environment_yml": any(name in names for name in {"environment.yml", "environment.yaml"}),
        "dockerfile": "dockerfile" in names,
        "license": license_file is not None,
        "inference_entrypoint": any(name in names for name in INFERENCE_FILES),
        "examples_dir": any(part in {"examples", "demos", "scripts"} for file in files for part in file.relative_to(path).parts),
        "eval_script": any(re.search(r"eval|benchmark|metric", str(file.relative_to(path)), re.IGNORECASE) for file in files),
        "training_script": any(re.search(r"train|finetune|fine_tune", str(file.relative_to(path)), re.IGNORECASE) for file in files),
        "weight_file": any(file.suffix.lower() in WEIGHT_SUFFIXES for file in files),
        "docs": any(str(file.relative_to(path)).lower().startswith("docs/") for file in files),
    }
    signals = {
        "has_install_instructions": False,
        "has_inference_example": file_flags["inference_entrypoint"],
        "has_checkpoint_link": file_flags["weight_file"],
        "has_model_card_link": False,
        "has_eval_instructions": file_flags["eval_script"],
        "has_training_instructions": file_flags["training_script"],
    }
    discovered_model_card_urls: list[str] = []
    if readme:
        readme_text = _read_text_safely(readme)
        signals.update(_readme_signals(readme_text, file_flags))
        discovered_model_card_urls = _extract_model_card_urls(readme_text)
        snippets.extend(_readme_snippets(readme_text, readme.relative_to(path)))

    metadata = _git_metadata(path)
    has_verifiable_repo_access = method == "git_clone" or (path / ".git").exists() or bool(metadata.get("commit"))
    if has_verifiable_repo_access:
        snippets.append(_typed_snippet(
            kind="repo_access",
            evidence_text=f"Local repository access verified: {path}",
            source_file=str(path),
            source_type="local_path",
            source_scope=source_scope,
        ))
    if metadata.get("commit"):
        snippets.append(_typed_snippet(
            kind="repo_commit",
            evidence_text=f"Git commit hash observed: {metadata['commit']}",
            source_file=str(path / ".git"),
            source_type="local_path",
            source_scope=source_scope,
        ))
    snippets.extend(_file_presence_snippets(path, files, source_scope))
    summary = {
        "repo_url": repo_url,
        "source_type": "github_repo" if repo_url and "github.com" in repo_url.lower() else "local_repo",
        "retrieved_at": retrieved_at,
        "access_status": "ok",
        "method": method,
        "commit": metadata.get("commit"),
        "default_branch": metadata.get("default_branch"),
        "stars": None,
        "forks": None,
        "open_issues": None,
        "license": license_file.name if license_file else None,
        "last_updated": None,
        "files": file_flags,
        "signals": signals,
        "model_card_urls": discovered_model_card_urls,
        "snippets": snippets,
        "warnings": warnings,
        "source_scope": source_scope,
    }
    items.extend(_repo_items_from_summary(summary))
    return {"summary": summary, "items": items, "warnings": warnings}


def _github_api_summary(repo_url: str, timeout_sec: int, retrieved_at: str) -> dict[str, Any]:
    warnings: list[str] = []
    match = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", repo_url, re.IGNORECASE)
    if not match:
        warnings.append(f"not a GitHub repo URL: {repo_url}")
        return {"summary": _repo_fail(repo_url, "github_api", retrieved_at, warnings), "items": [], "warnings": warnings}
    owner, repo = match.group(1), re.sub(r"\.git$", "", match.group(2))
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        data = _http_json(api_url, timeout_sec)
    except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        warnings.append(f"GitHub API repo metadata failed: {exc}")
        return {"summary": _repo_fail(repo_url, "github_api", retrieved_at, warnings), "items": [], "warnings": warnings}
    summary = {
        "repo_url": repo_url,
        "source_type": "github_repo",
        "retrieved_at": retrieved_at,
        "access_status": "ok",
        "method": "github_api",
        "commit": None,
        "default_branch": data.get("default_branch"),
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "open_issues": data.get("open_issues_count"),
        "license": (data.get("license") or {}).get("spdx_id") if isinstance(data.get("license"), dict) else None,
        "last_updated": data.get("updated_at"),
        "files": {},
        "signals": {},
        "model_card_urls": [],
        "snippets": [
            _typed_snippet(
                kind="repo_access",
                evidence_text=f"GitHub API metadata for {owner}/{repo}",
                source_file=api_url,
                source_type="github_api",
                source_scope="upstream_repo",
            )
        ],
        "warnings": warnings,
        "source_scope": "upstream_repo",
    }
    return {"summary": summary, "items": _repo_items_from_summary(summary), "warnings": warnings}


def _collect_model_card_evidence(
    *,
    model_card_url: str,
    mode: str,
    cache_dir: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    retrieved_at = _now()
    warnings: list[str] = []
    if mode == "local-only":
        warnings.append("model card remote collection skipped in local-only mode")
        return {
            "summary": _model_card_fail(model_card_url, "local-only", retrieved_at, warnings),
            "items": [],
            "warnings": warnings,
        }
    if "huggingface.co" in model_card_url.lower():
        return _hf_model_card_summary(model_card_url, timeout_sec, retrieved_at)
    warnings.append(f"unsupported model card host: {model_card_url}")
    return {"summary": _model_card_fail(model_card_url, "remote", retrieved_at, warnings), "items": [], "warnings": warnings}


def _hf_model_card_summary(model_card_url: str, timeout_sec: int, retrieved_at: str) -> dict[str, Any]:
    warnings: list[str] = []
    model_id = _hf_model_id(model_card_url)
    if not model_id:
        warnings.append(f"could not parse Hugging Face model id: {model_card_url}")
        return {"summary": _model_card_fail(model_card_url, "hf_api", retrieved_at, warnings), "items": [], "warnings": warnings}
    try:
        info = _http_json(f"https://huggingface.co/api/models/{model_id}", timeout_sec)
    except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        warnings.append(f"Hugging Face model API failed: {exc}")
        return {"summary": _model_card_fail(model_card_url, "hf_api", retrieved_at, warnings), "items": [], "warnings": warnings}
    siblings = info.get("siblings") if isinstance(info.get("siblings"), list) else []
    filenames = [str(item.get("rfilename") or "") for item in siblings if isinstance(item, dict)]
    card_data = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    tags = info.get("tags") if isinstance(info.get("tags"), list) else []
    files = {
        "has_weight_file": any(Path(name).suffix.lower() in WEIGHT_SUFFIXES for name in filenames),
        "has_config": any(Path(name).name.lower() == "config.json" for name in filenames),
        "has_readme": any(Path(name).name.lower() == "readme.md" for name in filenames),
    }
    signals = {
        "has_usage_example": bool(info.get("pipeline_tag")) or any("usage" in str(tag).lower() for tag in tags),
        "has_task_tag": bool(info.get("pipeline_tag") or tags),
        "has_license": bool(card_data.get("license") or info.get("license")),
        "has_downloadable_weights": files["has_weight_file"],
    }
    snippets = [
        _typed_snippet(
            kind="model_card_access",
            evidence_text=f"Hugging Face model metadata exists for {model_id}.",
            source_file=f"https://huggingface.co/api/models/{model_id}",
            source_type="hf_model_card",
            source_scope="official_model_card",
        )
    ]
    if files["has_weight_file"]:
        snippets.append(_typed_snippet(
            kind="model_card_weight_file",
            evidence_text=f"Hugging Face model metadata lists downloadable weight files for {model_id}.",
            source_file=f"https://huggingface.co/api/models/{model_id}",
            source_type="hf_model_card",
            source_scope="official_model_card",
        ))
    if signals["has_license"]:
        snippets.append(_typed_snippet(
            kind="model_card_license",
            evidence_text=f"Hugging Face model metadata declares license for {model_id}.",
            source_file=f"https://huggingface.co/api/models/{model_id}",
            source_type="hf_model_card",
            source_scope="official_model_card",
        ))
    summary = {
        "model_card_url": model_card_url,
        "source_type": "hf_model_card",
        "retrieved_at": retrieved_at,
        "access_status": "ok",
        "method": "hf_api",
        "model_id": model_id,
        "license": card_data.get("license") or info.get("license"),
        "pipeline_tag": info.get("pipeline_tag"),
        "task_tags": tags,
        "files": files,
        "signals": signals,
        "snippets": snippets,
        "warnings": warnings,
        "source_scope": "official_model_card",
    }
    return {"summary": summary, "items": _model_card_items_from_summary(summary), "warnings": warnings}


def _repo_items_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    retrieved_at = summary.get("retrieved_at")
    for idx, snippet in enumerate(summary.get("snippets") or [], start=1):
        if not isinstance(snippet, dict):
            continue
        items.append(
            {
                "id": f"repo_{idx:04d}",
                "kind": snippet.get("kind") or "unknown",
                "field": snippet.get("field") or KIND_TO_FIELD.get(str(snippet.get("kind") or "unknown"), "repo.unknown"),
                "source_type": snippet.get("source_type") or ("repo_file" if summary.get("method") in {"local_path", "git_clone"} else "repo_metadata"),
                "source_name": str(snippet.get("source_file") or "repo"),
                "source_scope": snippet.get("source_scope") or summary.get("source_scope"),
                "url": summary.get("repo_url"),
                "title": snippet.get("field") or "repo evidence",
                "snippet": snippet.get("evidence_text") or "",
                "evidence_text": snippet.get("evidence_text") or "",
                "retrieved_at": retrieved_at,
                "reliability": "official_repo_or_model_card",
                "stance": "neutral",
                "evidence_fields": [snippet.get("field") or KIND_TO_FIELD.get(str(snippet.get("kind") or "unknown"), "repo.unknown")],
                "allowed_score_criteria": _allowed_score_criteria(str(snippet.get("kind") or "unknown")),
                "source_file": snippet.get("source_file"),
                "line_start": snippet.get("start_line"),
                "line_end": snippet.get("end_line"),
            }
        )
    return items


def _model_card_items_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    fields = ["source.model_card_url"]
    if summary.get("files", {}).get("has_weight_file"):
        fields.append("model.checkpoint_source")
    if summary.get("signals", {}).get("has_license"):
        fields.append("model.license")
    for idx, snippet in enumerate(summary.get("snippets") or [], start=1):
        items.append(
            {
                "id": f"model_card_{idx:04d}",
                "kind": snippet.get("kind") or "model_card_access",
                "field": snippet.get("field") or KIND_TO_FIELD.get(str(snippet.get("kind") or "model_card_access"), "source.model_card_url"),
                "source_type": summary.get("source_type") or "model_card",
                "source_name": summary.get("model_id"),
                "source_scope": snippet.get("source_scope") or summary.get("source_scope") or "official_model_card",
                "url": summary.get("model_card_url"),
                "title": "model card metadata",
                "snippet": snippet.get("evidence_text") or "",
                "evidence_text": snippet.get("evidence_text") or "",
                "retrieved_at": summary.get("retrieved_at"),
                "reliability": "official_repo_or_model_card",
                "stance": "neutral",
                "evidence_fields": [snippet.get("field") or KIND_TO_FIELD.get(str(snippet.get("kind") or "model_card_access"), "source.model_card_url")],
                "allowed_score_criteria": _allowed_score_criteria(str(snippet.get("kind") or "model_card_access")),
                "source_file": snippet.get("source_file"),
            }
        )
    return items


def _repo_metadata_fields(summary: dict[str, Any]) -> list[str]:
    fields = []
    files = summary.get("files") or {}
    signals = summary.get("signals") or {}
    if files.get("readme") or files.get("docs"):
        fields.append("repo.has_readme_or_docs")
    if any(files.get(key) for key in ["requirements", "pyproject", "environment_yml", "dockerfile"]):
        fields.append("repo.has_install_or_environment_files")
        fields.append("repo.has_requirements_or_dependency_lock")
    if files.get("inference_entrypoint") or signals.get("has_inference_example"):
        fields.append("repo.has_inference_entrypoint_or_example")
    if files.get("license"):
        fields.append("repo.has_license")
    if summary.get("commit"):
        fields.append("repo.commit")
    if files.get("eval_script") or signals.get("has_eval_instructions"):
        fields.append("repo.has_eval_script_or_metric_claim_mapping")
    if files.get("weight_file") or signals.get("has_checkpoint_link") or signals.get("has_model_card_link"):
        fields.append("repo.checkpoint")
    return fields


def _readme_signals(readme_text: str, file_flags: dict[str, bool]) -> dict[str, bool]:
    lowered = readme_text.lower()
    return {
        "has_install_instructions": bool(re.search(r"\b(?:install|pip install|conda|requirements|environment\.ya?ml)\b", lowered)),
        "has_inference_example": file_flags["inference_entrypoint"] or bool(re.search(r"\b(?:inference|predict|quick start|usage|demo)\b", lowered)),
        "has_checkpoint_link": file_flags["weight_file"] or bool(re.search(r"\b(?:checkpoint|pretrained|pre-trained|weights?|download)\b", lowered)),
        "has_model_card_link": any(domain in lowered for domain in MODEL_CARD_DOMAINS),
        "has_eval_instructions": file_flags["eval_script"] or bool(re.search(r"\b(?:evaluate|evaluation|benchmark|wer|cer|accuracy|f1)\b", lowered)),
        "has_training_instructions": file_flags["training_script"] or bool(re.search(r"\b(?:train|training|fine-tune|finetune)\b", lowered)),
    }


def classify_repo_snippet(evidence_text: str, source_file: str | None, surrounding_context: str | None = None) -> str:
    """Classify repo evidence into a typed, deterministic EvidenceKind."""
    blob = " ".join([evidence_text or "", source_file or "", surrounding_context or ""]).lower()
    file_name = Path(source_file or "").name.lower()
    if re.search(r"download\s+(?:the\s+)?(?:dataset|corpus|data)|prepare\s+(?:the\s+)?(?:dataset|corpus|data)|data preparation|official dataset website|dataset path|_full_release", blob):
        return "repo_dataset_download" if "download" in blob else "repo_dataset_preparation"
    if re.search(DATASET_NAMES, blob) and re.search(r"download|prepare|dataset|corpus|data path|official", blob):
        return "repo_dataset_download" if "download" in blob else "repo_dataset_preparation"
    if file_name in {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "environment.yml", "environment.yaml"}:
        return "repo_dependency_file"
    if file_name == "dockerfile" or re.search(r"pip install|conda install|install dependencies|docker build|requirements|environment\.ya?ml", blob):
        return "repo_install_instructions"
    if re.search(r"\blicen[cs]e\b|apache|mit\b|bsd|gpl|cc-by", blob) and (
        "license" in file_name or re.search(r"#+\s*licen[cs]e|licen[cs]ed under|license:", blob)
    ):
        return "repo_license"
    if re.search(r"huggingface\.co/[^/\s]+/[^/\s]+|modelscope\.(?:cn|ai)/", blob) and not re.search(r"dataset|corpus|baseline|upstream", blob):
        return "repo_model_card_declared"
    if re.search(r"\.(?:pt|pth|bin|safetensors|ckpt|onnx)\b", blob):
        return "repo_weight_file_observed"
    if re.search(r"download\s+(?:model|checkpoint|pretrained model)|model weights?|checkpoint|ckpt|pretrained model|pre-trained model", blob) and not re.search(r"dataset|corpus|baseline|upstream", blob):
        return "repo_checkpoint_declared"
    if file_name in {"inference.py", "infer.py", "predict.py", "transcribe.py", "diarize.py", "vad.py", "enhance.py", "demo.py", "app.py", "inference.ipynb"}:
        return "repo_demo" if file_name in {"demo.py", "app.py"} else "repo_inference_entrypoint"
    if re.search(r"quickstart inference|run inference|\binference\b|inference\.ipynb|load model and predict|automodel\.generate|model\.forward|pipeline usage|transcribe|diarize|voice activity|enhance", blob):
        return "repo_inference_example"
    if re.search(r"train\.py|finetune\.py|fine_tune\.py|training scripts?|run_train", blob):
        return "repo_training_script"
    if re.search(r"eval\.py|run_eval|benchmark|reproduce table|\bwer\b|\bcer\b|\bder\b|\buar\b|\bwa\b|\bua\b|\bf1\b|\beer\b|\bbleu\b|pesq|stoi|si-sdr", blob):
        return "repo_eval_script" if re.search(r"eval\.py|run_eval|benchmark", blob) else "repo_metric_mapping"
    if "readme" in file_name:
        return "repo_readme"
    if str(source_file or "").lower().startswith("docs/"):
        return "repo_docs"
    if "example" in blob:
        return "repo_example"
    return "unknown"


def _file_presence_snippets(path: Path, files: list[Path], source_scope: str) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for file in files:
        rel = file.relative_to(path)
        rel_str = str(rel)
        name = file.name.lower()
        kind = "unknown"
        evidence = f"Observed repository file: {rel_str}"
        if name in README_NAMES:
            kind = "repo_readme"
        elif rel_str.lower().startswith("docs/"):
            kind = "repo_docs"
        elif name in ENVIRONMENT_FILES or name == "dockerfile":
            kind = "repo_dependency_file"
        elif name in INFERENCE_FILES or name in {"transcribe.py", "diarize.py", "vad.py", "enhance.py"}:
            kind = "repo_demo" if name in {"demo.py", "app.py"} else "repo_inference_entrypoint"
        elif re.search(r"eval|benchmark|metric|test", rel_str, re.IGNORECASE):
            kind = "repo_eval_script"
        elif re.search(r"train|finetune|fine_tune", rel_str, re.IGNORECASE):
            kind = "repo_training_script"
        elif "license" in name:
            kind = "repo_license"
        elif file.suffix.lower() in WEIGHT_SUFFIXES:
            kind = "repo_weight_file_observed"
        if kind != "unknown":
            snippets.append(_typed_snippet(kind=kind, evidence_text=evidence, source_file=rel_str, source_scope=source_scope))
    return snippets


def _typed_snippet(
    *,
    kind: str,
    evidence_text: str,
    source_file: str,
    source_type: str = "repo_file",
    source_scope: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "field": KIND_TO_FIELD.get(kind, "repo.unknown"),
        "source_type": source_type,
        "source_scope": source_scope,
        "source_file": source_file,
        "evidence_text": evidence_text,
        "start_line": start_line,
        "end_line": end_line,
        "allowed_score_criteria": _allowed_score_criteria(kind),
    }


def _allowed_score_criteria(kind: str) -> list[str]:
    return list(KIND_TO_CRITERIA.get(kind, []))


def _source_scope(path: Path, repo_url: str | None) -> str:
    lowered = str(path).lower()
    skill_markers = {"model.spec.yaml", "server.py", "artifacts", "verdict.json"}
    names = {item.name.lower() for item in path.rglob("*")}
    if "src/sure_eval/models" in lowered or skill_markers & names:
        return "sure_local_skill"
    if repo_url and "github.com" in repo_url.lower():
        return "upstream_repo"
    return "unknown_local_dir"


def _readme_snippets(readme_text: str, readme_rel: Path) -> list[dict[str, Any]]:
    patterns = [
        ("repo.has_readme_or_docs", r"\b(?:readme|documentation|overview)\b"),
        ("repo.has_install_or_environment_files", r"\b(?:install|pip install|conda|requirements|environment\.ya?ml)\b"),
        ("repo.has_inference_entrypoint_or_example", r"\b(?:inference|predict|quick start|usage|demo)\b"),
        ("repo.checkpoint", r"\b(?:checkpoint|pretrained|pre-trained|weights?|download|huggingface|modelscope)\b"),
        ("repo.has_eval_script_or_metric_claim_mapping", r"\b(?:evaluate|evaluation|benchmark|wer|cer|accuracy|f1)\b"),
        ("repo.has_license", r"\b(?:license|mit|apache|bsd|gpl)\b"),
    ]
    lines = readme_text.splitlines()
    snippets: list[dict[str, Any]] = []
    for field, pattern in patterns:
        for idx, line in enumerate(lines):
            if re.search(pattern, line, re.IGNORECASE):
                start = max(0, idx - 1)
                end = min(len(lines), idx + 2)
                evidence_text = " ".join(line.strip() for line in lines[start:end] if line.strip())[:500]
                kind = classify_repo_snippet(evidence_text, str(readme_rel), evidence_text)
                snippets.append(_typed_snippet(
                    kind=kind,
                    evidence_text=evidence_text,
                    source_file=str(readme_rel),
                    start_line=start + 1,
                    end_line=end,
                ))
                break
    return snippets


def _try_shallow_clone(repo_url: str, target: Path, timeout_sec: int) -> dict[str, Any]:
    if target.exists():
        return {"ok": True, "warnings": []}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(target)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "warnings": [f"git clone timed out after {timeout_sec} seconds"]}
    if proc.returncode == 0:
        return {"ok": True, "warnings": []}
    return {"ok": False, "warnings": [f"git clone failed: {(proc.stderr or proc.stdout).strip()[:500]}"]}


def _http_json(url: str, timeout_sec: int) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "sure-paper-to-userspec/0.1"})
    with urlopen(request, timeout=timeout_sec) as response:  # noqa: S310 - user-requested collector
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("expected JSON object", payload, 0)
    return data


def _empty_repo_summary(user_spec: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "repo_url": user_spec.get("source", {}).get("repo_url"),
        "source_type": "github_repo",
        "retrieved_at": _now(),
        "access_status": "not_collected",
        "method": mode,
        "files": {},
        "signals": {},
        "snippets": [],
        "warnings": [],
    }


def _empty_model_card_summary(user_spec: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "model_card_url": user_spec.get("source", {}).get("model_card_url"),
        "source_type": "model_card",
        "retrieved_at": _now(),
        "access_status": "not_collected",
        "method": mode,
        "files": {},
        "signals": {},
        "snippets": [],
        "warnings": [],
    }


def _repo_fail(repo_url: str | None, method: str, retrieved_at: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "repo_url": repo_url,
        "source_type": "github_repo",
        "retrieved_at": retrieved_at,
        "access_status": "fail",
        "method": method,
        "files": {},
        "signals": {},
        "snippets": [],
        "warnings": _unique(warnings),
    }


def _model_card_fail(url: str | None, method: str, retrieved_at: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "model_card_url": url,
        "source_type": "model_card",
        "retrieved_at": retrieved_at,
        "access_status": "fail",
        "method": method,
        "files": {},
        "signals": {},
        "snippets": [],
        "warnings": _unique(warnings),
    }


def _model_card_urls(user_spec: dict[str, Any], repo_summary: dict[str, Any]) -> list[str]:
    urls = []
    source_url = user_spec.get("source", {}).get("model_card_url")
    if source_url:
        urls.append(str(source_url))
    for url in repo_summary.get("model_card_urls") or []:
        if url not in urls:
            urls.append(url)
    return urls


def _extract_model_card_urls(text: str) -> list[str]:
    pattern = r"https?://(?:www\.)?(?:huggingface\.co|modelscope\.cn|modelscope\.ai)/[^\s)>\]\"']+"
    return _unique([url.rstrip(".,") for url in re.findall(pattern, text, flags=re.IGNORECASE)])


def _git_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"commit": None, "default_branch": None}
    for key, command in [
        ("commit", ["git", "-C", str(path), "rev-parse", "HEAD"]),
        ("default_branch", ["git", "-C", str(path), "branch", "--show-current"]),
    ]:
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0:
            value = proc.stdout.strip()
            metadata[key] = value or None
    return metadata


def _repo_metadata_snippet(summary: dict[str, Any]) -> str:
    files = summary.get("files") or {}
    observed = sorted(key for key, value in files.items() if value)
    parts = [f"Observed repository files/signals: {', '.join(observed) or 'none'}."]
    if summary.get("commit"):
        parts.append(f"Commit: {summary['commit']}.")
    if summary.get("license"):
        parts.append(f"License metadata/file: {summary['license']}.")
    return " ".join(parts)


def _first_existing_name(names: dict[str, Path], candidates: set[str]) -> Path | None:
    for candidate in candidates:
        if candidate in names:
            return names[candidate]
    return None


def _first_name_contains(names: dict[str, Path], needle: str) -> Path | None:
    for name, path in names.items():
        if needle in name:
            return path
    return None


def _read_text_safely(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _hf_model_id(url: str) -> str | None:
    match = re.search(r"huggingface\.co/([^/\s]+/[^/\s#?]+)", url, re.IGNORECASE)
    return match.group(1).rstrip("/") if match else None


def _collection_failed(mode: str, repo_summary: dict[str, Any], model_card_summary: dict[str, Any]) -> bool:
    if mode == "off":
        return False
    repo_failed = repo_summary.get("access_status") == "fail"
    model_card_failed = (
        model_card_summary.get("model_card_url")
        and model_card_summary.get("access_status") == "fail"
    )
    return bool(repo_failed or model_card_failed)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
