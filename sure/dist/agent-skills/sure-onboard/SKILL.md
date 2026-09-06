---
name: sure-onboard
description: "Onboard or repair a model, validate its inference contract, and seal a reproducible Eval runtime."
---

# SURE Onboard

This portable skill coordinates onboard or repair a model, validate its inference contract, and seal a reproducible eval runtime.

# SURE Onboard

Onboard or repair a model, validate its inference contract, and seal a reproducible Eval runtime.

SURE Core is authoritative for workflow transitions and validator outcomes. Work on exactly the current unit, write its declared artifact under the run artifact scope, and call the host control plane to validate it. Do not edit checkpoint files or infer PASS from a process exit code.

## Workflow

1. `load_model_input` produces `model_input_resolved.json` (gate).
2. `context_selection` produces `context_selection.json` (linear).
3. `discover` produces `repo_summary.json` (linear).
4. `classify` produces `classification.json` (linear).
5. `plan` produces `backend_choice.json` (linear).
6. `build_plan` produces `build_plan.json` (gate).
7. `validate_spec` produces `spec_validation.json` (gate).
8. `prepare_fixture` produces `fixture_manifest.json` (gate).
9. `build_env` produces `build_env_result.json` (gate).
10. `fetch_weights` produces `weights_manifest.json` (gate).
11. `validate_env_compat` produces `env_compat_result.json` (gate).
12. `generate_wrapper` produces `wrapper_manifest.json` (linear).
13. `validate_import` produces `import_result.json` (gate).
14. `validate_load` produces `load_result.json` (gate).
15. `validate_infer` produces `infer_result.json` (gate).
16. `validate_contract` produces `contract_result.json` (gate).
17. `package_container` produces `docker_registry_result.json` (gate).
18. `save_artifacts` produces `artifact_manifest.json` (gate).
19. `package_gate` produces `package_gate.json` (gate).
20. `write_runtime_inventory` produces `runtime_inventory.json` (gate).
21. `verdict` produces `verdict.json` (gate).
22. `extract_lessons` produces `extraction_declaration.json` (gate).
23. `finalize_model_bundle` produces `deployment_ready.json` (gate).

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

1. `load_model_input` -> `model_input_resolved.json` (gate).
2. `context_selection` -> `context_selection.json` (linear).
3. `discover` -> `repo_summary.json` (linear).
4. `classify` -> `classification.json` (linear).
5. `plan` -> `backend_choice.json` (linear).
6. `build_plan` -> `build_plan.json` (gate).
7. `validate_spec` -> `spec_validation.json` (gate).
8. `prepare_fixture` -> `fixture_manifest.json` (gate).
9. `build_env` -> `build_env_result.json` (gate).
10. `fetch_weights` -> `weights_manifest.json` (gate).
11. `validate_env_compat` -> `env_compat_result.json` (gate).
12. `generate_wrapper` -> `wrapper_manifest.json` (linear).
13. `validate_import` -> `import_result.json` (gate).
14. `validate_load` -> `load_result.json` (gate).
15. `validate_infer` -> `infer_result.json` (gate).
16. `validate_contract` -> `contract_result.json` (gate).
17. `package_container` -> `docker_registry_result.json` (gate).
18. `save_artifacts` -> `artifact_manifest.json` (gate).
19. `package_gate` -> `package_gate.json` (gate).
20. `write_runtime_inventory` -> `runtime_inventory.json` (gate).
21. `verdict` -> `verdict.json` (gate).
22. `extract_lessons` -> `extraction_declaration.json` (gate).
23. `finalize_model_bundle` -> `deployment_ready.json` (gate).

## Canonical artifact contract

| Artifact | Path | Required |
| --- | --- | --- |
| `verdict` | `artifacts/verdict.json` | yes |
| `model_spec` | (declared by producer) | no |
| `wrapper` | (declared by producer) | no |
| `build_plan` | `artifacts/build_plan.json` | no |
| `fixture_manifest` | `artifacts/fixture_manifest.json` | no |
| `package_gate` | `artifacts/package_gate.json` | no |
| `runtime_inventory` | `artifacts/runtime_inventory.json` | yes |
| `deployment_ready` | `artifacts/deployment_ready.json` | yes |

## References

Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.
