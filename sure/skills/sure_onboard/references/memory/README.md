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
    ├── README.md          # route table of exported bad cases (cli export keeps it in sync)
    └── <slug>.md          # one confirmed bad case per file, five-line provenance header on top
```

Entries that are not yet confirmed live outside git under `sure/memory/`
(`provisional/`, `outbox/`); `sure/memory/index.md` is the merged index over
both layers and is what `ROUTING.md` points at first.

## Default Context

Read by default:

- `memory/COMMON.md`
- `task_playbooks/ROUTING.md`
- `playbooks/env_ROUTING.md`

Do not read bad-case memory by default.

## Audit

Record the memory files actually read in `context_selection.json` under
`selected_references.memory` (see `ROUTING.md`).
