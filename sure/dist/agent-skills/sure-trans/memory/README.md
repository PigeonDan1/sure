# SURE memory boundary

This distribution carries the host-neutral `memory-contract.json` used by SURE Core.
Memory is advisory: its diagnostics and references never manufacture a workflow PASS or change the business validator result.

Validate the projection before using a host memory backend:

`surectl memory --contract ./memory-contract.json --skill sure_trans`

Logical references use `memory://<skill>/<kind>/<slug>`. A backend that is not installed or cannot provide a structured receipt must be reported as `NOT_EXECUTED/CAPABILITY_MISSING`; it must not be substituted with a successful result.

The portable package does not import Pi lifecycle modules. The pinned runtime includes `sure/runtime/memory/launcher.py`; hosts may invoke its `publish`, `index`, or `promote` operation with explicit `--memory-root`, reference roots, and (when applicable) `--reference-root`. The launcher emits `sure.memory.writer_receipt.v1`, never writes workflow checkpoints, and treats memory failures as advisory while SURE Core remains authoritative for workflow transitions.
