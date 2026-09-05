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
