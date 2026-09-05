# SURE Feed

Feed ModelScope, HuggingFace, or GitHub speech models into the SURE pipeline and emit a validated onboarding handoff.

SURE Core is authoritative for workflow transitions and validator outcomes. Work on exactly the current unit, write its declared artifact under the run artifact scope, and call the host control plane to validate it. Do not edit checkpoint files or infer PASS from a process exit code.

## Workflow

1. `scan_modelscope` produces `scan_result.json` (linear).
2. `match_task` produces `match_task_result.json` (gate).
3. `collect_metadata` produces `metadata_result.json` (linear).
4. `convert_to_oref` produces `oref_result.json` (linear).
5. `synthesize_model_input` produces `model_input_result.json` (gate).
6. `rank_and_select` produces `rank_select_result.json` (gate).
7. `extract_lessons` produces `extraction_declaration.json` (gate).
8. `emit_handoff_manifest` produces `handoff_manifest.json` (linear).

A missing artifact stays on the current unit. A changed failing artifact consumes one retry; an unchanged failing digest does not. Capability absence is `NOT_EXECUTED/CAPABILITY_MISSING`, never PASS.
