---
name: sure-trans
description: "Transform a Docker or locked Python model runtime into a SURE Eval deployment bundle."
---

# SURE Trans

This portable skill coordinates transform a docker or locked python model runtime into a sure eval deployment bundle.

# SURE Trans

Transform a Docker or locked Python model runtime into a SURE Eval deployment bundle.

SURE Core is authoritative for workflow transitions and validator outcomes. Work on exactly the current unit, write its declared artifact under the run artifact scope, and call the host control plane to validate it. Do not edit checkpoint files or infer PASS from a process exit code.

## Workflow

1. `load_trans_input` produces `trans_input_resolved.json` (gate).
2. `inspect_dependencies` produces `inference_dependency_report.json` (gate).
3. `detect_framework` produces `framework_detection.json` (gate).
4. `prepare_fixture` produces `fixture_manifest.json` (gate).
5. `build_source_image` produces `source_image_result.json` (gate).
6. `validate_env_compat` produces `execution_compat.json` (gate).
7. `validate_original_inference` produces `original_inference_result.json` (gate).
8. `stage_model_payload` produces `model_payload_manifest.json` (gate).
9. `generate_adapter` produces `adapter_manifest.json` (gate).
10. `build_adapter_image` produces `adapter_image_result.json` (gate).
11. `validate_import` produces `import_result.json` (gate).
12. `validate_load` produces `load_result.json` (gate).
13. `validate_infer` produces `infer_result.json` (gate).
14. `validate_contract` produces `contract_result.json` (gate).
15. `validate_mcp` produces `mcp_result.json` (gate).
16. `validate_equivalence` produces `equivalence_result.json` (gate).
17. `package_container` produces `docker_registry_result.json` (gate).
18. `write_runtime_inventory` produces `runtime_inventory.json` (gate).
19. `verdict` produces `verdict.json` (gate).
20. `extract_lessons` produces `extraction_declaration.json` (gate).
21. `finalize_model_bundle` produces `deployment_ready.json` (gate).

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

1. `load_trans_input` -> `trans_input_resolved.json` (gate).
2. `inspect_dependencies` -> `inference_dependency_report.json` (gate).
3. `detect_framework` -> `framework_detection.json` (gate).
4. `prepare_fixture` -> `fixture_manifest.json` (gate).
5. `build_source_image` -> `source_image_result.json` (gate).
6. `validate_env_compat` -> `execution_compat.json` (gate).
7. `validate_original_inference` -> `original_inference_result.json` (gate).
8. `stage_model_payload` -> `model_payload_manifest.json` (gate).
9. `generate_adapter` -> `adapter_manifest.json` (gate).
10. `build_adapter_image` -> `adapter_image_result.json` (gate).
11. `validate_import` -> `import_result.json` (gate).
12. `validate_load` -> `load_result.json` (gate).
13. `validate_infer` -> `infer_result.json` (gate).
14. `validate_contract` -> `contract_result.json` (gate).
15. `validate_mcp` -> `mcp_result.json` (gate).
16. `validate_equivalence` -> `equivalence_result.json` (gate).
17. `package_container` -> `docker_registry_result.json` (gate).
18. `write_runtime_inventory` -> `runtime_inventory.json` (gate).
19. `verdict` -> `verdict.json` (gate).
20. `extract_lessons` -> `extraction_declaration.json` (gate).
21. `finalize_model_bundle` -> `deployment_ready.json` (gate).

## Canonical artifact contract

| Artifact | Path | Required |
| --- | --- | --- |
| `dependency_report` | `artifacts/inference_dependency_report.json` | yes |
| `framework_detection` | `artifacts/framework_detection.json` | yes |
| `fixture` | `artifacts/fixture_manifest.json` | yes |
| `execution_compat` | `artifacts/execution_compat.json` | yes |
| `source_runtime` | `artifacts/source_image_result.json` | yes |
| `model_payload` | `artifacts/model_payload_manifest.json` | yes |
| `runtime_inventory` | `artifacts/runtime_inventory.json` | yes |
| `verdict` | `artifacts/verdict.json` | yes |
| `deployment_ready` | `artifacts/deployment_ready.json` | yes |

## References

Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.
