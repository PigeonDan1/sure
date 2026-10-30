---
name: sure
description: Operate the complete SURE audio-model workflow through its guarded harness, including provider initialization, model discovery, onboarding, transformation, human approval, inference, evaluation, agent-chain evaluation, and interrupted-run recovery.
---

# SURE

Use this skill as the entry point for the whole SURE system. Resolve this skill
directory through symlinks; the harness checkout is two directories above it.
Keep that checkout and its submodule available. This folder alone is not the
engine, and the calling agent's provider login does not configure SURE's provider.

## Setup and invocation

Follow the checkout's `README.md` Quick Start: Node >=22.19, Git, uv, initialized
submodules, and `npm ci --ignore-scripts`. Run `npm run sure:doctor` from the
checkout. Site policy and provider credentials are independent; inspect
`npm run sure:site-info` rather than replacing a bundled or local policy.
First use may download locked Python runtimes. Model/provider calls may use the
configured account; honor the user's evaluation scope and resource constraints.

The helper needs Python 3.10 or newer. If no Python is installed, use
`uv run --no-project --python 3.11 /path/to/sure/skills/sure/scripts/run.py list`
instead; uv supplies the interpreter without installing project dependencies.

The repository exposes this skill at `.agents/skills/sure`. For use from other
projects, symlink the absolute `skills/sure` directory into the host's skill
directory (Codex: `~/.agents/skills/sure`). A host without skill discovery can read
this file directly. Run the helper by its absolute path from any working directory:

```bash
python /path/to/sure/skills/sure/scripts/run.py list
python /path/to/sure/skills/sure/scripts/run.py feed --arguments 'url=<model-url>' --dry-run
python /path/to/sure/skills/sure/scripts/run.py feed --arguments 'url=<model-url>' --trust
```

`list` returns the installed command manifests and manuals. `--dry-run` only
plans the native command: it does not test provider auth, gates or inference.
Pass `--trust` only when the user trusts this checkout; otherwise the native
harness retains its project-trust check. Use `--args-file PATH` instead of
`--arguments` for long inputs. Supply credentials through native login/auth
storage or environment variables, never through arguments or prompt files.

The helper first asks the native bridge for the installed, validated command
manifest, including `.sure/skills` project overrides. It then sends one
structured JSON request through native `/sure_invoke`. Arguments remain an
opaque native string and `output_dir` is a typed field, preserving Unicode,
quotes, apostrophes and Windows paths. The native command preserves hooks,
`sure_update_state`, `sure_finish`, runtime locks and human gates. It checks
correlated terminal records, manifests and products rather than interpreting
fluent text as success.

Every call receives an invocation ID, request and code hashes, append-only
invocation events, a live receipt, native stdout/stderr, run snapshots and a
trace summary of tool calls, blocks, errors, finish repairs, continuations and
provider retries under `.sure/agent-invocations/<id>/`. Timeout and cancellation
terminate the native process group; competing init, resume and output paths are
locked.
Do not run the internal Python scoring/onboarding scripts as a replacement for
the harness: doing so omits its gate enforcement.

## Route the request

Read only the selected command's current `sure/skills/<command>/SKILL.md` before
constructing its arguments. Command semantics follow the installed version:

| Request | Helper command | Evidence / handoff |
| --- | --- | --- |
| Configure SURE provider, model and authentication | `init` | `.sure/init.json`; noninteractive calls require `--option` and `--model` |
| Discover a model and collect source evidence | `feed` | model input and onboarding handoff |
| Build and validate a new runnable model package | `onboard` | model package, runtime inventory, deployment readiness |
| Adapt an existing Dockerfile / inference implementation | `trans` | standard runtime adapter and deployment readiness |
| Review and publish a model | `approve` | immutable candidate, human decision, approval receipt |
| Run an approved model on datasets | `infer` | predictions, inference protocol and generation status |
| Score or re-score existing predictions | `eval` | evaluation batch, routes, metrics and reports; no model inference |
| Evaluate an ordered chain of approved models | `agent_eval` | agent product bundle and evaluation batch |
| Continue a resumable interrupted run | `resume` | same run ID, checkpoint and final manifest |

This version has no `/sure_reval`: existing-prediction scoring is `/sure_eval`.
Check `list` on another checkout rather than inventing missing commands.
`/sure_init` and `/sure_resume` are native commands, not skill packages.
See [references/workflows.md](references/workflows.md) for concrete call shapes.

Initialize when SURE's provider is unconfigured. For a new model, route
`feed -> onboard` (or `trans`) `-> approve -> infer -> eval` as needed. Existing
approved models can start at `infer`; existing prediction bundles can start at
`eval`. Do not rerun completed stages or infer when the request only asks to score.

Approval is a two-run workflow: create the review packet, show the concrete
candidate to the user, then submit their explicit decision. The outer agent
must not manufacture `decision=approve`. Also surface any checkpoint that needs
human confirmation; report it as incomplete and preserve the run ID for recovery.
Do not automatically restart a failed run or bypass a system-failure manifest.

## Return to the caller

Return the helper's status, command, run ID, run directory, manifest/result path,
artifact paths, and any error/next action. A nonzero helper exit or any status
other than `success` is not success. Read the actual metric/report artifacts to
report scores and sample counts. Initialization success only confirms provider
setup, not model deployment. A successful fixture test proves integration, not
the quality of a real model. Keep credentials and large model/data payloads out
of the response and repository.
