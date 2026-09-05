---
name: sure-eval
description: "Score an existing SURE prediction bundle with the pinned evaluation engine without running inference."
---

# SURE Eval

This portable skill coordinates score an existing sure prediction bundle with the pinned evaluation engine without running inference.

# SURE Eval

Score an existing SURE prediction bundle with the pinned evaluation engine without running inference.

SURE Core is authoritative for workflow transitions and validator outcomes. Work on exactly the current unit, write its declared artifact under the run artifact scope, and call the host control plane to validate it. Do not edit checkpoint files or infer PASS from a process exit code.

## Workflow

1. `dataset_scope` produces `dataset_decision.json` (linear).
2. `execute_evaluation` produces `eval_run_report.json` (gate).
3. `assessment` produces `assessment_report.json` (gate).
4. `extract_lessons` produces `extraction_declaration.json` (gate).
5. `run_report` produces `main_agent_run_report.json` (gate).

A missing artifact stays on the current unit. A changed failing artifact consumes one retry; an unchanged failing digest does not. Capability absence is `NOT_EXECUTED/CAPABILITY_MISSING`, never PASS.

## Portable host rules

Use the host-neutral "surectl" executable:

1. Start a run with `surectl start --skill <skill> --run-id <run-id>`.
2. Produce only the artifact for the current unit.
3. Call `surectl validate --run-id <run-id>`; Core decides advance, retry, block, or wait.
4. Inspect `surectl status --run-id <run-id>` before choosing the next action.
5. Finalize only through `surectl finalize --run-id <run-id> --status <status>`.

Do not call a later unit directly, write `state.json`, claim trusted execution, or use a reference root as an output directory.

## Workflow reference

### Branch main

1. `dataset_scope` -> `dataset_decision.json` (linear).
2. `execute_evaluation` -> `eval_run_report.json` (gate).
3. `assessment` -> `assessment_report.json` (gate).
4. `extract_lessons` -> `extraction_declaration.json` (gate).
5. `run_report` -> `main_agent_run_report.json` (gate).

## Canonical artifact contract

| Artifact | Path | Required |
| --- | --- | --- |
| `runtime_binding` | `artifacts/runtime_binding.json` | yes |
| `prediction_source_resolved` | `artifacts/prediction_source_resolved.json` | yes |
| `dataset_decision` | `artifacts/dataset_decision.json` | yes |
| `eval_run_report` | `artifacts/eval_run_report.json` | yes |
| `assessment_report` | `artifacts/assessment_report.json` | yes |
| `run_report` | `artifacts/main_agent_run_report.json` | yes |

## References

Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.
