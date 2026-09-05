---
name: sure-infer
description: "Run an approved model over selected datasets and produce a provenance-bound prediction bundle."
---

# SURE Infer

This portable skill coordinates run an approved model over selected datasets and produce a provenance-bound prediction bundle.

# SURE Infer

Run an approved model over selected datasets and produce a provenance-bound prediction bundle.

SURE Core is authoritative for workflow transitions and validator outcomes. Work on exactly the current unit, write its declared artifact under the run artifact scope, and call the host control plane to validate it. Do not edit checkpoint files or infer PASS from a process exit code.

## Workflow

1. `dataset_scope` produces `dataset_decision.json` (linear).
2. `execute_inference` produces `execution_result.json` (gate).
3. `extract_lessons` produces `extraction_declaration.json` (gate).
4. `run_report` produces `main_agent_run_report.json` (gate).

A missing artifact stays on the current unit. A changed failing artifact consumes one retry; an unchanged failing digest does not. Capability absence is `NOT_EXECUTED/CAPABILITY_MISSING`, never PASS.

## Portable host rules

Use the host-neutral `surectl` executable. Set `SURE_DEV_ROOT` to a local writable workspace and provide the immutable `SURE_POLICY_DIGEST` and `SURE_EXECUTOR_DIGEST` bindings before starting.

1. Start with `surectl start --skill <skill> --run-id <run-id> --root "$SURE_DEV_ROOT"`.
2. Produce only the artifact for the current unit.
3. For an execution unit, write an `execution_request.json` and call `surectl execute --run-id <run-id> --execution-request <path>`; this writes a receipt only and never advances the checkpoint.
4. Call `surectl validate --run-id <run-id>`; Core alone decides `PASS`, `FAIL`, `BLOCKED`, `RETRY`, or `NOT_EXECUTED` and the process exit code mirrors that outcome.
5. Inspect `surectl status --run-id <run-id>` before choosing the next action; use `surectl resume --run-id <run-id>` only for a failed, binding-compatible run.
6. Finalize only through `surectl finalize --run-id <run-id> --status <status>`.
7. `surectl conformance` reports cooperative evidence; it must not be described as trusted or formal assurance.

Do not call a later unit directly, write `state.json`, forge a capability as available, or use a reference root as an output directory. A missing capability is `NOT_EXECUTED/CAPABILITY_MISSING`, never `PASS`.

## Workflow reference

### Branch main

1. `dataset_scope` -> `dataset_decision.json` (linear).
2. `execute_inference` -> `execution_result.json` (gate).
3. `extract_lessons` -> `extraction_declaration.json` (gate).
4. `run_report` -> `main_agent_run_report.json` (gate).

## Canonical artifact contract

| Artifact | Path | Required |
| --- | --- | --- |
| `eval_input_resolved` | `artifacts/eval_input_resolved.json` | no |
| `dataset_decision` | `artifacts/dataset_decision.json` | yes |
| `execution_surface` | `artifacts/execution_surface.json` | yes |
| `execution_result` | `artifacts/execution_result.json` | yes |
| `run_report` | `artifacts/main_agent_run_report.json` | yes |
| `protocol` | `artifacts/protocol.yaml` | no |
| `prediction_manifests` | `artifacts/predictions` | no |
| `model_eval_manifest` | (declared by producer) | no |

## References

Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.
