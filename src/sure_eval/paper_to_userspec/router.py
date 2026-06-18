"""Route user_spec_query objects to SURE downstream dry-run artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .schema import ROUTES


CRITICAL_FIELDS = ["model.name", "task.primary_task"]

ROUTE_ARTIFACTS = {
    "tool_onboarding": ("MODEL_INPUT.yaml", "tool_onboarding"),
    "main_flow_evaluation": ("MAIN_FLOW_INPUT.yaml", "main_flow_evaluation"),
    "controlled_training_conversion": (
        "training_conversion_request.json",
        "controlled_training",
    ),
    "needs_human_input": ("missing_information_request.json", "human_review"),
}


def _dotted_get(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _registry_lookup(model_name: str, models_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "matched": False,
        "model_name": model_name,
        "model_dir": None,
        "reason": "registry_lookup_not_attempted",
    }
    try:
        from sure_eval.models.registry import ModelRegistry

        registry = ModelRegistry(models_dir=models_dir)
        result["available"] = True
        direct = registry.get_model(model_name)
        if direct:
            result.update(
                {
                    "matched": True,
                    "model_dir": str(direct.path),
                    "reason": "matched ModelRegistry by configured model name",
                }
            )
            return result
        normalized = model_name.lower().replace("-", "_").replace(" ", "_")
        for name in registry.list_models():
            if name.lower().replace("-", "_").replace(" ", "_") == normalized:
                info = registry.get_model(name)
                result.update(
                    {
                        "matched": bool(info),
                        "model_dir": str(info.path) if info else None,
                        "reason": "matched ModelRegistry by normalized model name",
                    }
                )
                return result
        result["reason"] = "no ModelRegistry match"
    except Exception as exc:  # pragma: no cover - depends on local registry/import health
        result["reason"] = f"registry lookup unavailable: {exc}"

    model_dir = models_dir / model_name.lower().replace(" ", "_")
    if model_dir.exists():
        result.update({"matched": True, "model_dir": str(model_dir), "reason": "matched model directory"})
    return result


def route_user_spec(
    user_spec: dict[str, Any],
    repo_root: str | Path | None = None,
    route_override: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root or Path.cwd())
    models_dir = root / "src" / "sure_eval" / "models"
    missing = list(user_spec.get("missing_fields", []))
    for field in CRITICAL_FIELDS:
        value = _dotted_get(user_spec, field)
        if value in (None, "", "unknown") and field not in missing:
            missing.append(field)

    intent = user_spec.get("user_goal", {}).get("intent", "unknown")
    model_name = str(user_spec.get("model", {}).get("local_sure_model_name") or user_spec.get("model", {}).get("name") or "")
    lookup = _registry_lookup(model_name, models_dir)

    if route_override:
        if route_override not in ROUTES:
            raise ValueError(f"illegal route_override: {route_override}")
        route = route_override
        reason = f"explicit route override requested: {route_override}"
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]
    elif missing:
        route = "needs_human_input"
        reason = f"critical fields missing: {', '.join(sorted(missing))}"
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]
    elif intent == "controlled_training":
        route = "controlled_training_conversion"
        reason = "user goal explicitly requests controlled training conversion"
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]
    elif intent == "onboard" and not lookup["matched"]:
        route = "tool_onboarding"
        reason = "user goal is onboard and the model is not matched in ModelRegistry"
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]
    elif intent == "evaluate" and lookup["matched"]:
        route = "main_flow_evaluation"
        reason = f"user goal is evaluate and {lookup['reason']}"
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]
    elif lookup["matched"]:
        route = "main_flow_evaluation"
        reason = lookup["reason"]
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]
    else:
        route = "tool_onboarding"
        reason = "model is not known to ModelRegistry and no matching model directory was found"
        next_artifact, downstream_flow = ROUTE_ARTIFACTS[route]

    assert route in ROUTES
    decision = {
        "route": route,
        "reason": reason,
        "next_artifact": next_artifact,
        "downstream_flow": downstream_flow,
        "registry_lookup": lookup,
        "missing_fields": missing,
        "route_override": route_override,
        "training_recipe_indicated": bool(
            user_spec.get("confidence", {}).get("training_recipe_indicated")
        ),
    }
    user_spec["missing_fields"] = missing
    user_spec["sure_routing"] = {
        "route": route,
        "reason": reason,
        "next_artifact": next_artifact,
        "downstream_flow": downstream_flow,
    }
    return decision
