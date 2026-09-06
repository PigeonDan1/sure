---
name: sure-feed
description: "Feed ModelScope, HuggingFace, or GitHub speech models into the SURE pipeline and emit a validated onboarding handoff."
---

# SURE Feed

This portable skill coordinates feed modelscope, huggingface, or github speech models into the sure pipeline and emit a validated onboarding handoff.

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

## Portable host rules

Use the host-neutral `surectl` executable. Set `SURE_DEV_ROOT` to a local writable workspace and provide the immutable `SURE_POLICY_DIGEST` and `SURE_EXECUTOR_DIGEST` bindings before starting.

1. Start with `surectl start --skill <skill> --run-id <run-id> --root "$SURE_DEV_ROOT"`.
2. Produce only the artifact for the current unit.
3. For a unit whose canonical definition declares `execution_operation_id`, first produce its declared artifact, then call `surectl execute --run-id <run-id> --operation <execution_operation_id> --artifact <path>`; Core resolves the pinned operation and writes the request/receipt. Do not author a replacement request. For a unit without a registered operation, provide an `execution_request.json` and call `surectl execute --run-id <run-id> --execution-request <path>`. Either form writes execution evidence only and never advances the checkpoint.
4. Call `surectl validate --run-id <run-id>`; Core alone decides `PASS`, `FAIL`, `BLOCKED`, `RETRY`, or `NOT_EXECUTED` and the process exit code mirrors that outcome.
5. Inspect `surectl status --run-id <run-id>` before choosing the next action; use `surectl resume --run-id <run-id>` only for a failed, binding-compatible run.
6. Finalize only through `surectl finalize --run-id <run-id> --status <status>`.
7. `surectl conformance` reports cooperative evidence; it must not be described as trusted or formal assurance.

Do not call a later unit directly, write `state.json`, forge a capability as available, or use a reference root as an output directory. A missing capability is `NOT_EXECUTED/CAPABILITY_MISSING`, never `PASS`.

## Workflow reference

### Branch main

1. `scan_modelscope` -> `scan_result.json` (linear).
2. `match_task` -> `match_task_result.json` (gate).
3. `collect_metadata` -> `metadata_result.json` (linear).
4. `convert_to_oref` -> `oref_result.json` (linear).
5. `synthesize_model_input` -> `model_input_result.json` (gate).
6. `rank_and_select` -> `rank_select_result.json` (gate).
7. `extract_lessons` -> `extraction_declaration.json` (gate).
8. `emit_handoff_manifest` -> `handoff_manifest.json` (linear).

## Canonical artifact contract

| Artifact | Path | Required |
| --- | --- | --- |
| `runtime_binding` | `artifacts/runtime_binding.json` | yes |
| `model_input` | `artifacts/model_input.yaml` | yes |
| `feed_report` | `artifacts/feed_report.json` | yes |
| `model_input_result` | `artifacts/debug/model_input_result.json` | no |
| `handoff_manifest` | `artifacts/debug/handoff_manifest.json` | no |

## References

Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.
