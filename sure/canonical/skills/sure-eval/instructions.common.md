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
