"""Deterministic validators for Paper_to_UserSpec artifacts."""

from __future__ import annotations

from typing import Any

from .converters import model_input_to_onboarding_prompt, model_input_to_preview
from .io import dotted_get
from .schema import (
    BACKEND_HINTS,
    DEPLOYMENT_TYPES,
    HIGH_CONFIDENCE_FIELDS,
    METRIC_COMPATIBILITY,
    REQUIRED_MODEL_INPUT_FIELDS,
    REQUIRED_SOURCE_FIELDS,
    ROUTES,
    TASK_IO_COMPATIBILITY,
    TASK_TYPES,
    USER_GOAL_INTENTS,
    USER_SPEC_TOP_LEVEL_FIELDS,
)


def validate_user_spec(user_spec: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []

    for field in USER_SPEC_TOP_LEVEL_FIELDS:
        if field not in user_spec:
            errors.append(f"missing top-level field: {field}")
            missing.append(field)

    for field in REQUIRED_SOURCE_FIELDS:
        if "source" not in user_spec or field not in user_spec["source"]:
            errors.append(f"missing source field: {field}")
            missing.append(f"source.{field}")

    intent = dotted_get(user_spec, "user_goal.intent")
    if intent not in USER_GOAL_INTENTS:
        errors.append(f"illegal user_goal.intent: {intent}")

    deployment = dotted_get(user_spec, "model.deployment_type")
    if deployment not in DEPLOYMENT_TYPES:
        errors.append(f"illegal model.deployment_type: {deployment}")

    task = dotted_get(user_spec, "task.primary_task")
    if task not in TASK_TYPES:
        errors.append(f"illegal task.primary_task: {task}")

    backend = dotted_get(user_spec, "runtime.backend_hint")
    if backend not in BACKEND_HINTS:
        errors.append(f"illegal runtime.backend_hint: {backend}")

    route = dotted_get(user_spec, "sure_routing.route")
    if route not in ROUTES:
        errors.append(f"illegal sure_routing.route: {route}")

    if task == "unknown":
        warnings.append("task.primary_task is unknown; downstream route should request human input")

    confidence = user_spec.get("confidence", {})
    if isinstance(confidence, dict):
        extraction_warnings = confidence.get("extraction_warnings", [])
        if isinstance(extraction_warnings, list):
            warnings.extend(str(warning) for warning in extraction_warnings)
        confidence_errors, confidence_warnings, confidence_validation = _validate_confidence(user_spec)
        errors.extend(confidence_errors)
        warnings.extend(confidence_warnings)
    else:
        confidence_validation = {"present": False}
        errors.append("confidence must be an object")

    quality_errors, quality_warnings = _validate_identity_and_evidence_quality(user_spec)
    errors.extend(quality_errors)
    warnings.extend(quality_warnings)

    coverage = evidence_coverage(user_spec)
    for field in HIGH_CONFIDENCE_FIELDS:
        value = dotted_get(user_spec, field)
        if value in (None, "", [], "unknown"):
            continue
        if not coverage.get(field, {}).get("covered"):
            warnings.append(f"field has value but no evidence span: {field}")

    status = "fail" if errors else ("warning" if warnings else "pass")
    return {
        "status": status,
        "can_route_to": route if not errors else "",
        "blocking_errors": errors,
        "warnings": warnings,
        "missing_fields": sorted(set(missing + list(user_spec.get("missing_fields", [])))),
        "conflict_fields": user_spec.get("conflict_fields", []),
        "evidence_coverage": coverage,
        "confidence_validation": confidence_validation,
        "model_input_validation": {},
        "main_flow_input_validation": {},
        "next_steps": _next_steps(status, route, missing),
    }


def evidence_coverage(user_spec: dict[str, Any]) -> dict[str, Any]:
    spans = user_spec.get("evidence_spans", [])
    covered = {span.get("field") for span in spans if isinstance(span, dict)}
    result: dict[str, Any] = {}
    for field in sorted(HIGH_CONFIDENCE_FIELDS):
        result[field] = {"covered": field in covered}
    return result


def validate_model_input(model_input: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []

    for field in REQUIRED_MODEL_INPUT_FIELDS:
        value = dotted_get(model_input, field)
        if value is None:
            # weights.local_path and repo.commit may be explicitly null by contract.
            if field not in {"weights.local_path", "repo.commit", "fixture.audio"}:
                errors.append(f"missing required MODEL_INPUT field: {field}")
                missing.append(field)

    task_type = model_input.get("task_type")
    if task_type not in TASK_TYPES:
        errors.append(f"illegal task_type: {task_type}")

    deployment = model_input.get("deployment_type")
    if deployment not in DEPLOYMENT_TYPES:
        errors.append(f"illegal deployment_type: {deployment}")

    backend = dotted_get(model_input, "environment_hint.preferred_backend")
    if backend not in BACKEND_HINTS:
        errors.append(f"illegal environment_hint.preferred_backend: {backend}")

    repo_url = dotted_get(model_input, "repo.url")
    if deployment == "local" and not repo_url:
        errors.append("repo.url is required for local models")

    errors.extend(_validate_io_compatibility(model_input))
    warnings.extend(_validate_metrics(model_input))

    evidence_refs = model_input.get("evidence_refs", {})
    if isinstance(evidence_refs, dict):
        for field in ["model.name", "task.primary_task"]:
            if field not in evidence_refs and not model_input.get("missing_fields"):
                warnings.append(f"MODEL_INPUT lacks evidence reference for {field}")

    confidence = model_input.get("confidence", {})
    if isinstance(confidence, dict) and confidence:
        decision = confidence.get("decision_hint")
        if decision in {"C", "D", "needs_human_review"}:
            warnings.append(
                f"MODEL_INPUT confidence decision_hint={decision}; review before formal onboarding"
            )
        if confidence.get("human_review_required") is True:
            warnings.append("MODEL_INPUT confidence requires human review")

    try:
        preview = model_input_to_preview(model_input)
        prompt = model_input_to_onboarding_prompt(model_input)
        if not preview.get("io_contract"):
            errors.append("MODEL_INPUT cannot be converted into model.spec.preview.yaml")
        if "MODEL_INPUT summary" not in prompt:
            errors.append("onboarding_prompt.md cannot be generated from MODEL_INPUT")
    except Exception as exc:
        errors.append(f"preview conversion failed: {exc}")

    status = "fail" if errors else ("warning" if warnings else "pass")
    return {
        "status": status,
        "can_route_to": "tool_onboarding" if not errors else "",
        "blocking_errors": errors,
        "warnings": warnings,
        "missing_fields": sorted(set(missing + list(model_input.get("missing_fields", [])))),
        "conflict_fields": [],
        "evidence_coverage": {"evidence_refs_present": bool(evidence_refs)},
        "model_input_validation": {
            "required_fields": not missing,
            "io_contract_compatible": not any("io_contract" in error for error in errors),
            "preview_convertible": not any("preview" in error for error in errors),
            "onboarding_prompt_generatable": not any("onboarding_prompt" in error for error in errors),
        },
        "main_flow_input_validation": {},
        "next_steps": _next_steps(status, "tool_onboarding", missing),
    }


def _validate_io_compatibility(model_input: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    task = model_input.get("task_type")
    if task == "unknown":
        return errors
    rule = TASK_IO_COMPATIBILITY.get(task)
    if not rule:
        return errors

    io_contract = model_input.get("io_contract", {})
    input_type = io_contract.get("input_type")
    output_type = io_contract.get("output_type")
    primary_field = io_contract.get("primary_field")
    required_fields = set(io_contract.get("required_fields") or [])
    nonempty_fields = set(io_contract.get("nonempty_fields") or [])

    if input_type not in rule["input_type"]:
        errors.append(f"task_type {task} incompatible with io_contract.input_type={input_type}")
    if output_type not in rule["output_type"]:
        errors.append(f"task_type {task} incompatible with io_contract.output_type={output_type}")
    allowed_primary = rule.get("primary_field", set())
    if allowed_primary and primary_field not in allowed_primary:
        errors.append(f"task_type {task} incompatible with io_contract.primary_field={primary_field}")
    required_any = rule.get("required_any")
    if required_any and required_fields.isdisjoint(required_any):
        errors.append(f"task_type {task} required_fields must include one of {sorted(required_any)}")
    nonempty_any = rule.get("nonempty_any")
    if nonempty_any and nonempty_fields.isdisjoint(nonempty_any):
        errors.append(f"task_type {task} nonempty_fields must include one of {sorted(nonempty_any)}")
    if io_contract.get("json_serializable") is not True:
        errors.append("io_contract.json_serializable must be true")
    return errors


def _validate_metrics(model_input: dict[str, Any]) -> list[str]:
    metrics = set(model_input.get("evaluation_metrics", []) or [])
    task = model_input.get("task_type")
    if not metrics or task not in METRIC_COMPATIBILITY:
        return []
    allowed = METRIC_COMPATIBILITY[task]
    incompatible = sorted(metrics - allowed)
    if incompatible:
        return [f"metrics may be incompatible with {task}: {', '.join(incompatible)}"]
    return []


def _validate_confidence(user_spec: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    confidence = user_spec.get("confidence", {})
    required = [
        "overall",
        "overall_percent",
        "extraction",
        "scoring_version",
        "paper_evidence_score",
        "decision_hint",
        "human_review_required",
        "training_recipe_indicated",
        "extraction_warnings",
        "confidence_warnings",
        "evidence_card_ids",
    ]
    missing = [field for field in required if field not in confidence]
    if missing:
        errors.append(f"confidence missing required fields: {', '.join(missing)}")

    overall_percent = confidence.get("overall_percent")
    if not isinstance(overall_percent, int) or not 0 <= overall_percent <= 100:
        errors.append(f"confidence.overall_percent must be integer 0..100, got {overall_percent}")
        overall_percent = 0
    overall = confidence.get("overall")
    if not isinstance(overall, (int, float)) or not 0 <= float(overall) <= 1:
        errors.append(f"confidence.overall must be number 0..1, got {overall}")

    decision = confidence.get("decision_hint")
    if decision not in {"A", "B", "C", "D", "needs_human_review"}:
        errors.append(f"illegal confidence.decision_hint: {decision}")

    evidence_card_ids = confidence.get("evidence_card_ids", [])
    if not isinstance(evidence_card_ids, list):
        errors.append("confidence.evidence_card_ids must be a list")
        evidence_card_ids = []
    if overall_percent >= 70 and not evidence_card_ids:
        errors.append("high confidence requires evidence_card_ids")

    coverage = evidence_coverage(user_spec)
    missing_high = [
        field
        for field in ["model.name", "task.primary_task", "source.paper_title"]
        if not coverage.get(field, {}).get("covered")
    ]
    if overall_percent >= 70 and missing_high:
        errors.append(f"high confidence lacks high evidence coverage: {', '.join(missing_high)}")

    repo_url = dotted_get(user_spec, "source.repo_url")
    checkpoint = dotted_get(user_spec, "model.checkpoint_source")
    model_card = dotted_get(user_spec, "source.model_card_url")
    if overall_percent >= 70 and not repo_url:
        errors.append("confidence.overall_percent >= 70 requires source.repo_url")
    if overall_percent >= 70 and checkpoint in (None, "", "unknown") and not model_card:
        warnings.append("confidence.overall_percent >= 70 lacks checkpoint/model_card evidence")

    spans = user_spec.get("evidence_spans", [])
    paper_sections = {
        str(span.get("section_name"))
        for span in spans
        if isinstance(span, dict) and span.get("source", "paper_text") == "paper_text"
    }
    if overall_percent >= 70 and paper_sections and paper_sections <= {"abstract"}:
        errors.append("abstract-only evidence cannot support confidence.overall_percent >= 70")

    score_breakdown = confidence.get("score_breakdown", {})
    if isinstance(score_breakdown, dict):
        for dimension, breakdown in score_breakdown.items():
            if not isinstance(breakdown, dict):
                continue
            for contribution in breakdown.get("contributions", []):
                if (
                    isinstance(contribution, dict)
                    and contribution.get("points_awarded", 0)
                    and not contribution.get("evidence_card_id")
                ):
                    errors.append(f"score contribution lacks evidence card: {dimension}")
                    break

    for warning in confidence.get("confidence_warnings", []) if isinstance(confidence.get("confidence_warnings"), list) else []:
        warnings.append(str(warning))

    return errors, warnings, {
        "present": True,
        "required_fields_present": not missing,
        "overall_percent": overall_percent,
        "decision_hint": decision,
        "evidence_card_ids_count": len(evidence_card_ids),
        "high_evidence_coverage_ok": not missing_high,
    }


def _validate_identity_and_evidence_quality(user_spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    spans = [span for span in user_spec.get("evidence_spans", []) if isinstance(span, dict)]
    confidence = user_spec.get("confidence", {}) if isinstance(user_spec.get("confidence"), dict) else {}
    overall_percent = confidence.get("overall_percent", 0)
    if not isinstance(overall_percent, int):
        overall_percent = 0
    decision = confidence.get("decision_hint")

    model_spans = [
        span
        for span in spans
        if span.get("field") == "model.name" and span.get("candidate_status") != "rejected"
    ]
    for span in model_spans:
        flags = set(span.get("quality_flags") or [])
        negative = set(span.get("negative_signals") or [])
        section = str(span.get("section_name") or "")
        if section in {"related work", "references", "appendix", "background"} or flags & {
            "related_work_only",
            "references_only",
            "baseline_only",
        } or "prior_work_or_baseline_context" in negative:
            message = "model.name evidence appears to come from related/references/baseline context"
            if overall_percent >= 70:
                errors.append(f"high confidence with low-quality critical evidence: {message}")
            else:
                warnings.append(message)
            break

    repo_url = dotted_get(user_spec, "source.repo_url")
    model_name = dotted_get(user_spec, "model.name")
    repo_name = _name_from_url(repo_url)
    if repo_url and model_name and model_name != "unknown" and repo_name and not _names_similar(model_name, repo_name):
        warnings.append("model.name is dissimilar to source.repo_url basename")

    conflict_fields = set(user_spec.get("conflict_fields", []) or [])
    if "model.name" in conflict_fields and decision in {"A", "B"}:
        errors.append("model.name conflict cannot route with confidence decision A/B")

    for span in spans:
        if span.get("field") == "data.eval_datasets" and span.get("usage_type") in {
            "pretrain",
            "upstream_initialization",
            "train",
            "validation",
            "downstream",
        }:
            errors.append("data.eval_datasets contains non-evaluation usage evidence")
            break
    for span in spans:
        if span.get("field") == "data.train_datasets" and span.get("usage_type") in {
            "pretrain",
            "upstream_initialization",
            "downstream",
            "eval",
            "test",
            "validation",
        }:
            errors.append("data.train_datasets contains non-target-training usage evidence")
            break

    repo_spans = [span for span in spans if span.get("field") == "source.repo_url"]
    if repo_url and repo_spans and not any(span.get("source") == "paper_text" for span in repo_spans):
        warnings.append("source.repo_url has no paper_text evidence span")
    if repo_url and not repo_spans:
        warnings.append("source.repo_url has no explicit evidence span")

    low_quality_critical = any(
        span.get("field") in {"model.name", "task.primary_task", "source.repo_url"}
        and set(span.get("quality_flags") or [])
        for span in spans
    )
    if overall_percent >= 70 and low_quality_critical:
        errors.append("high confidence with low-quality critical evidence")

    return errors, warnings


def _normalize_identity(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _names_similar(left: str | None, right: str | None) -> bool:
    l_norm = _normalize_identity(left)
    r_norm = _normalize_identity(right)
    if not l_norm or not r_norm:
        return False
    return l_norm in r_norm or r_norm in l_norm


def _name_from_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = str(url).rstrip("/).,")
    parts = [part for part in cleaned.replace("?", "/").replace("#", "/").split("/") if part]
    if not parts:
        return None
    last = parts[-1]
    if last.lower() in {"tree", "blob", "main", "master", "summary"} and len(parts) > 1:
        last = parts[-2]
    if last.endswith(".git"):
        last = last[:-4]
    return last


def validate_main_flow_input(main_flow_input: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    payload = main_flow_input.get("MAIN_FLOW_INPUT")
    if not isinstance(payload, dict):
        errors.append("missing MAIN_FLOW_INPUT mapping")
    else:
        if not payload.get("target", {}).get("model_dir"):
            errors.append("target.model_dir is missing")
        evidence = payload.get("evidence", {})
        for key in ["readme_path", "config_path", "model_spec_path"]:
            if not evidence.get(key):
                errors.append(f"evidence.{key} is missing or file does not exist")
    return {
        "status": "fail" if errors else "pass",
        "can_route_to": "main_flow_evaluation" if not errors else "",
        "blocking_errors": errors,
        "warnings": [],
        "missing_fields": [],
        "conflict_fields": [],
        "evidence_coverage": {},
        "model_input_validation": {},
        "main_flow_input_validation": {"files_checked": True},
        "next_steps": _next_steps("fail" if errors else "pass", "main_flow_evaluation", []),
    }


def merge_validation_report(
    *,
    user_spec_report: dict[str, Any],
    route: str,
    model_input_report: dict[str, Any] | None = None,
    main_flow_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors = list(user_spec_report.get("blocking_errors", []))
    warnings = list(user_spec_report.get("warnings", []))
    if model_input_report:
        errors.extend(model_input_report.get("blocking_errors", []))
        warnings.extend(model_input_report.get("warnings", []))
    if main_flow_report:
        errors.extend(main_flow_report.get("blocking_errors", []))
        warnings.extend(main_flow_report.get("warnings", []))

    status = "fail" if errors else ("warning" if warnings else "pass")
    return {
        "status": status,
        "can_route_to": route if not errors else "",
        "blocking_errors": errors,
        "warnings": warnings,
        "missing_fields": sorted(set(user_spec_report.get("missing_fields", []))),
        "conflict_fields": user_spec_report.get("conflict_fields", []),
        "evidence_coverage": user_spec_report.get("evidence_coverage", {}),
        "model_input_validation": model_input_report or {},
        "main_flow_input_validation": main_flow_report or {},
        "next_steps": _next_steps(status, route, user_spec_report.get("missing_fields", [])),
    }


def _next_steps(status: str, route: Any, missing: list[str]) -> list[str]:
    if status == "fail":
        return ["Resolve blocking_errors before handing artifacts to downstream SURE workflows."]
    if route == "needs_human_input" or missing:
        return ["Provide missing fields, then rebuild the user_spec_query artifact."]
    if route == "tool_onboarding":
        return ["Pass MODEL_INPUT.yaml to the SURE Tool Onboarding Workflow."]
    if route == "main_flow_evaluation":
        return ["Pass MAIN_FLOW_INPUT.yaml to the SURE Main Flow Agent."]
    if route == "controlled_training_conversion":
        return ["Review training_conversion_request.json before any training workflow."]
    return ["Review generated artifacts."]
