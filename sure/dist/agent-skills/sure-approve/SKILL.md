---
name: sure-approve
description: "Audit and explicitly approve a completed model bundle for publication to SURE Eval."
---

# SURE Approve

This portable skill coordinates audit and explicitly approve a completed model bundle for publication to sure eval.

# SURE Approve

Audit and explicitly approve a completed model bundle for publication to SURE Eval.

SURE Core is authoritative for workflow transitions and validator outcomes. Work on exactly the current unit, write its declared artifact under the run artifact scope, and call the host control plane to validate it. Do not edit checkpoint files or infer PASS from a process exit code.

## Workflow

1. `resolve_input` produces `approve_input_resolved.json` (gate).
2. `classify_producer` produces `producer_contract_report.json` (gate).
3. `audit_integrity` produces `integrity_report.json` (gate).
4. `plan_repairs` produces `repair_plan.json` (gate).
5. `apply_repairs` produces `repair_report.json` (gate).
6. `seal_candidate` produces `approval_manifest.json` (gate).
7. `verify_runtime` produces `runtime_verification.json` (gate).
8. `prepare_review` produces `review_packet.json` (gate).

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

### Branch audit

1. `resolve_input` -> `approve_input_resolved.json` (gate).
2. `classify_producer` -> `producer_contract_report.json` (gate).
3. `audit_integrity` -> `integrity_report.json` (gate).
4. `plan_repairs` -> `repair_plan.json` (gate).
5. `apply_repairs` -> `repair_report.json` (gate).
6. `seal_candidate` -> `approval_manifest.json` (gate).
7. `verify_runtime` -> `runtime_verification.json` (gate).
8. `prepare_review` -> `review_packet.json` (gate).

### Branch approve

1. `verify_decision` -> `approval_decision.json` (gate).
2. `publish` -> `publication_result.json` (gate).
3. `verify_publication` -> `approval_ready.json` (gate).

## Canonical artifact contract

| Artifact | Path | Required |
| --- | --- | --- |
| `review_packet` | `artifacts/review_packet.json` | no |
| `approval_decision` | `artifacts/approval_decision.json` | no |
| `approval_ready` | `artifacts/approval_ready.json` | no |

## References

Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.
