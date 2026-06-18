"""Converters from user_spec_query to downstream SURE dry-run artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractor import extract_training_signals


def _task_io_contract(task: str) -> dict[str, Any]:
    if task == "ASR":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "text",
            "required_fields": ["text"],
            "nonempty_fields": ["text"],
            "json_serializable": True,
        }
    if task == "S2TT":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "translation",
            "required_fields": ["translation"],
            "nonempty_fields": ["translation"],
            "json_serializable": True,
        }
    if task in {"SD", "VAD"}:
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "segments",
            "required_fields": ["segments"],
            "nonempty_fields": ["segments"],
            "json_serializable": True,
        }
    if task == "SA-ASR":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "segments",
            "required_fields": ["segments"],
            "nonempty_fields": ["segments"],
            "json_serializable": True,
        }
    if task == "SER":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "label",
            "required_fields": ["label"],
            "nonempty_fields": ["label"],
            "json_serializable": True,
        }
    if task == "SV":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "decision",
            "required_fields": ["decision"],
            "nonempty_fields": ["decision"],
            "json_serializable": True,
        }
    if task == "TTS":
        return {
            "input_type": "json",
            "output_type": "audio_path",
            "primary_field": "audio_path",
            "required_fields": ["audio_path"],
            "nonempty_fields": ["audio_path"],
            "json_serializable": True,
        }
    if task == "Speech Enhancement":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "enhanced_audio_path",
            "required_fields": ["enhanced_audio_path"],
            "nonempty_fields": ["enhanced_audio_path"],
            "json_serializable": True,
        }
    if task == "Music IR":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "features",
            "required_fields": ["features"],
            "nonempty_fields": ["features"],
            "json_serializable": True,
        }
    if task == "GR":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "label",
            "required_fields": ["label"],
            "nonempty_fields": ["label"],
            "json_serializable": True,
        }
    if task == "SLU":
        return {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "answer",
            "required_fields": ["answer"],
            "nonempty_fields": ["answer"],
            "json_serializable": True,
        }
    if task == "VLM":
        return {
            "input_type": "json",
            "output_type": "json",
            "primary_field": "answer",
            "required_fields": ["answer"],
            "nonempty_fields": ["answer"],
            "json_serializable": True,
        }
    return {
        "input_type": "json",
        "output_type": "json",
        "primary_field": "result",
        "required_fields": ["result"],
        "nonempty_fields": ["result"],
        "json_serializable": True,
    }


def user_spec_to_model_input(user_spec: dict[str, Any]) -> dict[str, Any]:
    task = user_spec["task"].get("primary_task", "unknown")
    repo_url = user_spec["source"].get("repo_url")
    model_name = user_spec["model"].get("name") or user_spec["case_id"]
    local_name = user_spec["model"].get("local_sure_model_name") or user_spec["case_id"]
    backend = user_spec["runtime"].get("backend_hint") or "unknown"
    if backend == "unknown" and user_spec["model"].get("deployment_type") == "api":
        backend = "api"

    evidence_refs = {
        span.get("field"): {
            "source": span.get("source"),
            "quote": span.get("quote"),
            "confidence": span.get("confidence"),
        }
        for span in user_spec.get("evidence_spans", [])
        if isinstance(span, dict) and span.get("field")
    }
    for card in user_spec.get("_evidence_cards", []):
        if not isinstance(card, dict) or not card.get("field"):
            continue
        evidence_refs.setdefault(
            card["field"],
            {
                "source": card.get("source_type"),
                "quote": card.get("evidence_text"),
                "confidence": card.get("confidence"),
                "evidence_card_id": card.get("id"),
                "source_url": card.get("source_url"),
            },
        )
    confidence = user_spec.get("confidence", {})
    confidence_summary = {
        "overall_percent": confidence.get("overall_percent"),
        "decision_hint": confidence.get("decision_hint"),
        "paper_confidence_score": confidence.get("paper_confidence_score"),
        "paper_evidence_score": confidence.get("paper_evidence_score"),
        "declared_availability_score": confidence.get("declared_availability_score"),
        "human_review_required": confidence.get("human_review_required"),
        "scoring_version": confidence.get("scoring_version"),
    }
    return {
        "model_id": user_spec["model"].get("checkpoint_source") or model_name,
        "model_name": model_name,
        "task_type": task,
        "deployment_type": user_spec["model"].get("deployment_type", "unknown"),
        "repo": {"url": repo_url, "commit": None},
        "weights": {
            "source": user_spec["model"].get("checkpoint_source") or "unknown",
            "local_path": None,
            "required": user_spec["model"].get("deployment_type") != "api",
            "cache_policy": "model_local_first",
            "local_dir_name": "checkpoints",
        },
        "environment_hint": {
            "preferred_backend": backend,
            "python_version": user_spec["runtime"].get("python_version") or "unknown",
            "requires_gpu": bool(user_spec["runtime"].get("requires_gpu")),
            "system_packages": user_spec["runtime"].get("system_packages", []),
        },
        "phase1_runtime_target": (
            "Validate the minimal repo-native callable path only: import, load, "
            "infer on a task-specific fixture, and verify the io_contract. "
            "Do not run benchmarks or claim reproduction."
        ),
        "entrypoints": {
            "import_test": f"import {local_name}",
            "load_test": "Instantiate or load the minimal repo-native model path documented by the repository.",
            "infer_test": "Run one dry-run planned infer test on the selected fixture after onboarding.",
        },
        "fixture": {
            "audio": _default_fixture(task),
            "task_specific": task not in {"unknown", "utility", "VLM"},
            "fallback_allowed": True,
        },
        "io_contract": _task_io_contract(task),
        "confidence": confidence_summary,
        "evidence_refs": evidence_refs,
        "missing_fields": user_spec.get("missing_fields", []),
    }


def _default_fixture(task: str) -> str | None:
    if task == "VLM":
        return None
    mapping = {
        "ASR": "tests/fixtures/shared/asr/en_16k_10s.wav",
        "S2TT": "tests/fixtures/CoVoST2/sample_1_common_voice_en_670098.wav",
        "SD": "tests/fixtures/SD/Ses05F_script01_1_F033.wav",
        "SA-ASR": "tests/fixtures/SD/Ses05F_script01_1_F033.wav",
        "SER": "tests/fixtures/IEMOCAP/manifest.json",
        "VAD": "tests/fixtures/shared/vad/en_16k_10s.wav",
        "SV": "tests/fixtures/shared/speaker_verification/spk1_trial.wav",
        "TTS": "tests/fixtures/librispeech/manifest.json",
        "Speech Enhancement": "tests/fixtures/shared/se/noisy_48k.wav",
        "Music IR": "tests/fixtures/shared/mir/rhythm_22k_15s.wav",
        "GR": "tests/fixtures/shared/asr/en_16k_10s.wav",
        "SLU": "tests/fixtures/shared/asr/en_16k_10s.wav",
    }
    return mapping.get(task)


def model_input_to_preview(model_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_id": model_input.get("model_id"),
        "model_name": model_input.get("model_name"),
        "task_type": model_input.get("task_type"),
        "deployment_type": model_input.get("deployment_type"),
        "repo": model_input.get("repo", {}),
        "weights": model_input.get("weights", {}),
        "environment": model_input.get("environment_hint", {}),
        "entrypoints": model_input.get("entrypoints", {}),
        "fixture": model_input.get("fixture", {}),
        "io_contract": model_input.get("io_contract", {}),
        "confidence": model_input.get("confidence", {}),
        "acceptance": {"must_import": True, "must_load": True, "must_infer": True},
    }


def model_input_to_onboarding_prompt(model_input: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# SURE Tool Onboarding Prompt Preview",
            "",
            "Use this MODEL_INPUT as a draft for the SURE Tool Onboarding Workflow.",
            "This preview is dry-run only; it does not download weights or run inference.",
            "",
            "Required boundary:",
            "- Validate import/load/infer/contract during onboarding.",
            "- Do not claim paper reproduction from this artifact.",
            "- Keep checkpoints model-local when weights are required.",
            "",
            "MODEL_INPUT summary:",
            f"- model_name: {model_input.get('model_name')}",
            f"- task_type: {model_input.get('task_type')}",
            f"- deployment_type: {model_input.get('deployment_type')}",
            f"- repo.url: {model_input.get('repo', {}).get('url')}",
            f"- backend hint: {model_input.get('environment_hint', {}).get('preferred_backend')}",
            "",
            "Pass the full MODEL_INPUT.yaml to the onboarding workflow.",
            "",
        ]
    )


def user_spec_to_main_flow_input(user_spec: dict[str, Any], routing_decision: dict[str, Any]) -> dict[str, Any]:
    model_dir = routing_decision.get("registry_lookup", {}).get("model_dir")
    model_dir_path = Path(model_dir) if model_dir else None
    readme_path = model_dir_path / "README.md" if model_dir_path else None
    config_path = model_dir_path / "config.yaml" if model_dir_path else None
    spec_path = model_dir_path / "model.spec.yaml" if model_dir_path else None

    return {
        "MAIN_FLOW_INPUT": {
            "user_goal": "evaluate_existing_model",
            "target": {
                "model_name": user_spec["model"].get("local_sure_model_name"),
                "model_dir": model_dir,
                "tool_workflow_ready": bool(model_dir_path and (model_dir_path / "server.py").exists()),
            },
            "constraints": {
                "allow_tool_workflow": False,
                "allowed_tasks": [user_spec["task"].get("primary_task")],
                "allowed_datasets": user_spec["data"].get("eval_datasets", []),
                "blocked_datasets": [],
                "dry_run": True,
            },
            "evidence": {
                "readme_path": str(readme_path) if readme_path and readme_path.exists() else None,
                "config_path": str(config_path) if config_path and config_path.exists() else None,
                "artifacts_dir": str(model_dir_path / "artifacts") if model_dir_path and (model_dir_path / "artifacts").exists() else None,
                "model_spec_path": str(spec_path) if spec_path and spec_path.exists() else None,
            },
            "runtime_context": {
                "available_scripts": [],
                "output_dir": "runs/main_flow",
            },
        }
    }


def user_spec_to_training_request(user_spec: dict[str, Any]) -> dict[str, Any]:
    return extract_training_signals(user_spec)


def missing_information_request(user_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": user_spec.get("case_id"),
        "route": "needs_human_input",
        "missing_fields": user_spec.get("missing_fields", []),
        "conflict_fields": user_spec.get("conflict_fields", []),
        "questions": [
            f"Please provide {field}." for field in user_spec.get("missing_fields", [])
        ],
        "evidence_spans": user_spec.get("evidence_spans", []),
    }
