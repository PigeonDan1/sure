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
