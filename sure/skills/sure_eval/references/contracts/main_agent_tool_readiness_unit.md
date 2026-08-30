# Main Agent Tool Readiness Contract

## Purpose

This unit prevents `/sure_eval` from repairing, guessing, or executing an unapproved model runtime. It consumes only the exact model directory below the configured approved model root.

## Required Output

- `readiness`: `ready | needs_onboarding | needs_repair | unavailable`
- `model_dir`: exact approved NFS directory
- optional `verdict_status`, `server_present`, `model_py_present`, `handoff_to_tool_agent`, and `routing_reason`

## Required Evidence

For a local model, `ready` requires exactly one approved deployment binding.

Container binding:

- `deployment_ready.json` has status `ready` and package `docker-registry`;
- `runtime_inventory.json` declares `container_only` execution;
- `package_gate.json` proves local, Docker, registry, and bundle readiness;
- tag, digest, and digest-pinned image agree across all artifacts;
- approved model storage is read-only and results are written separately.

Local Python binding:

- the active site permits `local` plus the `python` local runtime;
- `deployment_ready.json` has status `ready` and package `none`;
- `runtime_inventory.json` declares `eval_runtime=python` and `execution_mode=local_only`;
- `package_gate.json` proves local and bundle readiness without Docker or registry claims;
- the sealed uv Model Runtime manifest resolves below the active site's `storage.runtime_root` and passes live verification;
- model-core hashes are declared and verified before and after trusted-host execution.

Both bindings require valid deployment-marker hashes and an independently writable results directory. `config.yaml`, `model.py`, `server.py`, and verdict remain model evidence, but they cannot replace the deployment binding. A model-local `.venv`, Dockerfile text, logs, local image listings, and similar image names are never readiness sources.

## Routing

1. Binding valid: `readiness=ready`, continue to planning.
2. Approved directory absent: `readiness=needs_onboarding`, finish `/sure_onboard` and promote after human review.
3. Directory exists but the binding is missing/inconsistent: `readiness=needs_repair`; do not infer a replacement runtime.
4. Never modify NFS from `/sure_eval`.

The output template is `scripts/templates/main_agent_tool_readiness_routing.json`.
