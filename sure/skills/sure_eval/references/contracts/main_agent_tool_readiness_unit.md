# Main Agent Tool Readiness Contract

## Purpose

This unit prevents `/sure_eval` from repairing, guessing, or executing an unapproved model runtime. It consumes only the exact model directory below the configured approved model root.

## Required Output

- `readiness`: `ready | needs_onboarding | needs_repair | unavailable`
- `model_dir`: exact approved NFS directory
- optional `verdict_status`, `server_present`, `model_py_present`, `handoff_to_tool_agent`, and `routing_reason`

## Required Evidence

For a local model, `ready` requires all of the following:

- `artifacts/deployment_ready.json` with status `ready` and package `docker-registry`;
- `artifacts/runtime_inventory.json` schema v2 with `container_only` execution;
- `artifacts/package_gate.json` schema v2 with local, Docker, registry, and bundle readiness;
- exact tag, digest, and digest-pinned image agreement across those files;
- valid hashes declared by the deployment marker;
- read-only NFS model policy, writable separate results, no host Python fallback, and no image override.

`config.yaml`, `model.py`, `server.py`, and verdict remain model evidence, but they cannot replace the deployment binding. `.venv`, Dockerfile text, logs, local image listings, and similar image names are never readiness sources.

## Routing

1. Binding valid: `readiness=ready`, continue to planning.
2. Approved directory absent: `readiness=needs_onboarding`, finish `/sure_onboard` and promote after human review.
3. Directory exists but the binding is missing/inconsistent: `readiness=needs_repair`; do not infer a replacement runtime.
4. Never modify NFS from `/sure_eval`.

The output template is `scripts/templates/main_agent_tool_readiness_routing.json`.
