---
name: sure-approve
description: Audit and explicitly approve a completed sure_onboard or sure_trans model bundle before publishing it for sure_eval.
---

# /sure_approve

Audit a completed `/sure_onboard` or `/sure_trans` bundle without mutating it, bind a human decision to the audited candidate, then publish it atomically for `/sure_eval`.

## Parameters

| Parameter | Required | Meaning |
| --- | --- | --- |
| `model_dir` | audit mode | Explicit readable producer bundle. Absolute paths and paths relative to the invocation directory are accepted. |
| `approve_dir` | no | Publication root. Defaults to the active site's approved model root. |
| `mode` | no | `audit` (default) or `approve`. |
| `repair` | no | `safe` (default) or `none`. Safe repair never changes executable behavior. |
| `review_manifest` | approve mode | `review_packet.json` emitted by a completed audit. |
| `decision` | approve mode | Explicitly `approve` or `reject`. The agent must never infer this value. |
| `replace` | no | Default `false`; an existing destination blocks publication. |
| `max_retries` | no | Gate retry limit; default 3. |

## Workflow

Run audit first:

```text
/sure_approve model_dir=/path/to/completed/model
```

The audit creates an isolated candidate under the run directory, verifies the producer contract and runtime, and ends with `review_packet.json` in `awaiting_approval`. It never writes to the approval root.

Read the review packet, make the decision yourself, then start a separate approval run:

```text
/sure_approve mode=approve review_manifest=/path/to/review_packet.json decision=approve
```

Approval verifies that the packet and candidate are unchanged. A positive decision publishes through a hidden same-filesystem sibling and atomic rename. A rejection records the decision and does not publish.

Use `approve_dir=/custom/root` only for a deliberate non-default publication. Such a result records `eval_visible=false`; current `/sure_eval` discovery sees only the active site's configured approved model root.

## Boundaries

- Do not mutate the producer directory.
- Reject incomplete, failed, API-ready, and `docker-local` products.
- Accept Docker v1 only with a digest-pinned, pull-verified registry image.
- Accept Python v2 only with a sealed `uv` Model Runtime and site policy enabling local Python.
- Require passing original, adapter, MCP, and equivalence evidence for `/sure_trans`.
- Restrict safe repair to derived paths, publication permissions, and excluded caches. Never repair wrappers, configs, weights, payloads, locks, runtimes, images, provenance, or failed validation.
- Reject source/destination overlap, escaping links, special files, and destination collisions.
- Never create the human decision. Require the user-supplied `decision` parameter.

Read [producer-contracts.md](references/producer-contracts.md) when classifying the producer or diagnosing a failed audit. Read [repair-policy.md](references/repair-policy.md) before accepting or describing a repair.

## State Machine

Audit mode runs units 1-8; approve mode resumes from the prior packet and runs units 9-11.

| # | Unit | Product |
| --- | --- | --- |
| 1 | `resolve_input` | `approve_input_resolved.json` |
| 2 | `classify_producer` | `producer_contract_report.json` |
| 3 | `audit_integrity` | `integrity_report.json` |
| 4 | `plan_repairs` | `repair_plan.json` |
| 5 | `apply_repairs` | `repair_report.json` |
| 6 | `seal_candidate` | `approval_manifest.json` |
| 7 | `verify_runtime` | `runtime_verification.json` |
| 8 | `prepare_review` | `review_packet.json` |
| 9 | `verify_decision` | `approval_decision.json` |
| 10 | `publish` | `publication_result.json` |
| 11 | `verify_publication` | `approval_ready.json` |

Run only the current unit's script with Harness Python from this package. Each script accepts `--run-dir` and `--produces`; `resolve_approve_input.py` additionally receives invocation parameters. Do not call producer scripts to manufacture missing evidence.
