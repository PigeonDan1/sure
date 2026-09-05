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
