# Model Tool Agent Memory

This directory contains reusable memory for model onboarding. It is intentionally
routed: common memory is read by default, while bad cases are read only when
their trigger appears.

## Files

```text
memory/
├── COMMON.md              # default shared memory
├── ROUTING.md             # when to read optional memory
└── bad_cases/
    └── README.md          # index of failure-specific memories
```

## Default Context

Read by default:

- `memory/COMMON.md`
- `task_playbooks/ROUTING.md`
- `playbooks/env_ROUTING.md`

Do not read bad-case memory by default.

## Audit

When optional memory is read, record why in `build_plan.json`,
`failure_classification.json`, or `tool_agent_run_report.json`.
