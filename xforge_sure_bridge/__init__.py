"""Adapter utilities connecting XForge resource manifests to SURE artifacts."""

from xforge_sure_bridge.bridge import (
    BridgeError,
    collect_dataset_manifest,
    emit_sure_model_agent_handoff,
    materialize_model_manifest,
    plan_dataset_integration,
    plan_model_integration,
    process_dataset_manifest,
    process_dataset_manifest_to_oref,
    write_oref_registry_entry,
)
from xforge_sure_bridge.modelscope_daily import (
    SUPPORTED_TASKS,
    build_daily_summary,
    rank_candidates,
    render_markdown_summary,
    write_daily_summary,
)
from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    emit_sure_integration_plan,
    write_fetch_failure,
    write_fetch_success,
)

__all__ = [
    "BridgeError",
    "SUPPORTED_TASKS",
    "build_daily_summary",
    "build_selected_candidate",
    "collect_dataset_manifest",
    "emit_sure_model_agent_handoff",
    "emit_selected_resource_artifacts",
    "emit_sure_integration_plan",
    "materialize_model_manifest",
    "plan_dataset_integration",
    "plan_model_integration",
    "process_dataset_manifest",
    "process_dataset_manifest_to_oref",
    "rank_candidates",
    "render_markdown_summary",
    "write_oref_registry_entry",
    "write_fetch_failure",
    "write_fetch_success",
    "write_daily_summary",
]
