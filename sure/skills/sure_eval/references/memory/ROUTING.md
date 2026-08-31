# Memory Routing

Optional memory for `/sure_eval`. Default context stays small; open memory only
when a concrete failure or known-risk signal appears.

## Always Read

- `<run_dir>/artifacts/memory_context.json` when it exists: the `pre_start`
  hook writes it with the memory facts that match this run (cluster, model,
  datasets). Read it once during `task_classification`; it is advisory and
  `task_classification.json` gets no field for it (its schema forbids extra
  keys).

## Read Only On Trigger

| Trigger | Read | Why |
|---------|------|-----|
| A gate blocks with an error string you do not recognise | `sure/memory/index.md` first (repo-root path; merged index of confirmed and provisional entries, one bullet per entry with its triggers), then the matching entry file | One lookup covers every layer. |
| A gate repair ends with a `Memory (advisory, ...)` block | the entry files named in that block | The hook already matched them to this unit and this error. |
| Failure needs classifying before a retry | `failure_taxonomy.md` | Classify before repairing. |
| Retry decision is needed | `retry_and_escalation.md` | Decide retryability and stopping conditions. |
| Error resembles a known bad case and the index has no hit | `memory/bad_cases/README.md` | Route table of exported eval entries; empty until the first `cli export`. |

## Bad Case Routing

A bad case requires both:

1. an observed symptom or error string; and
2. a matching bullet in `sure/memory/index.md` (a trigger that is a substring
   of the error, case-insensitive) or a matching row in
   `memory/bad_cases/README.md`.

`sure/memory/index.md` is built by the `pre_start` hook; on a fresh clone it
appears after the first run starts. If nothing matches, use
`failure_taxonomy.md` for classification instead of guessing.

Entries marked `[provisional]` or `[disputed]` in the index were written by an
agent in an earlier run and not reviewed by a person; treat them as hints.

## Audit Record

No eval artifact has a memory field and none should be added. The hook records
which entries it showed you (`sure/memory/usage/<run_id>.jsonl`); there is
nothing to write by hand.
