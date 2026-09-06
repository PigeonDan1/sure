# SURE memory boundary

This distribution carries the host-neutral `memory-contract.json` used by SURE Core.
Memory is advisory: its diagnostics and references never manufacture a workflow PASS or change the business validator result.

Validate the projection before using a host memory backend:

`surectl memory --contract ./memory-contract.json --skill sure_onboard`

Logical references use `memory://<skill>/<kind>/<slug>`. A backend that is not installed or cannot provide a structured receipt must be reported as `NOT_EXECUTED/CAPABILITY_MISSING`; it must not be substituted with a successful result.

The portable package does not import Pi lifecycle modules. A host may provide the memory writer and its roots explicitly, while SURE Core remains authoritative for workflow transitions.
