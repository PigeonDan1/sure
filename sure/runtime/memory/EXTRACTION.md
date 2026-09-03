# extract_lessons: extraction contract

This file is the contract for the `extract_lessons` unit of `/sure_onboard` (after `verdict`), `/sure_eval` (after `assessment`), `/sure_infer` (after `execute_inference`), `/sure_trans` (after `verdict`) and `/sure_feed` (after `rank_and_select`). Each of those SKILL.md files points here. Read it when the state machine enters that unit, and again when `pre_finish` asks for a declaration on a `failed` or `incomplete` finish (section 10).

What the unit produces: `artifacts/extraction_declaration.json`, plus zero to five candidate directories under `artifacts/candidates/` and, for facts, evidence files under `artifacts/memory_evidence/`.

What checks it: `scripts/check_memory_extraction.py`, run by the hook like any other gate. Everything it checks is mechanical and listed in section 8. Failing it twice in a row does not block the run: the hook advances on its own and records `extraction: failed`.

Everything you write here is advisory memory for later runs: agent-written, not human-reviewed. A later run sees at most two entries at a time, one line each, inside a gate repair. Write for a reader who has none of your context.

## 1. When you enter the unit

1. Read `artifacts/run_digest.json` with the read tool. The hook wrote it the moment the previous unit passed. It is the only input the gate accepts as ground truth about this run.
2. Do not rebuild it in place. The gate compares the sha256 of `artifacts/run_digest.json` with the value the hook stored in the checkpoint; if you rerun `scripts/build_run_digest.py` onto that path, every candidate is rejected (check 9). To look at a fresh digest, write it somewhere else: `"$HARNESS_PYTHON_BIN" scripts/build_run_digest.py --run-dir <run_dir> --repo-root <repo_root> --out <run_dir>/artifacts/run_digest.preview.json` (cwd = the skill package). The preview is not evidence and the gate ignores it.
3. If the digest is only `{"schema": "...", "error": "..."}`, the hook could not read the run. Declare `no_new_lessons: true` and quote the error in `no_lessons_reason`. That is the one declaration the gate accepts in this state.
4. Decide whether this run taught anything worth keeping (section 3). `no_new_lessons: true` with a one-line reason is the normal outcome of a clean run. Do not invent a candidate to fill the unit.
5. Write candidates and evidence first, the declaration last (section 6). The gate runs on the first tool result after `artifacts/extraction_declaration.json` exists; a declaration written before its candidates fails check 10 and costs an attempt.

## 2. What run_digest.json contains

| field | meaning |
|---|---|
| `run.run_id`, `run.skill` | this run; `source.run_id` and `source.skill` in every proposal must equal them |
| `run.args` | the invocation arguments as you saw them, with every absolute path value replaced by `<path>` and every URL by `<url>` |
| `run.target.kind`, `run.target.id` | `model` (onboard: the model id; trans: the `model_name`; feed: the selected model id) or `eval` (eval: the `model=` argument); `source.target` in every proposal must equal `run.target.id` |
| `run.status_so_far`, `run.cutoff` | run status when the digest was built; number of event lines it covers |
| `run.memory_usage[]` | memory entries shown to this run: `entry_id`, `unit`, `attempt`, `outcome` (`useful`, `disputed`, `open`). `disputed` means the entry was shown and the unit still failed on the same trigger |
| `units[]` | one row per unit of this skill: `id`; `outcome` (`passed`, `failed`, `skipped`, `current`); `attempts`; `repairs[]` with `attempt` and `text` (the gate's own words, head and tail only, memory block removed); `fix_window[]` with `tool` and `command` (at most 10 commands run between the last block and the pass; only for units that failed and then passed; non-bash tools record only the path); `last_commands[]` (only for units that ended failed); `log_tail` with `path` and `lines` (last 30 lines of the unit's registered log, only for failed units that have one) |
| `tool_errors` | count of tool calls that returned an error |
| `prior_runs[]` | earlier runs of the same skill on the same target, newest first, at most 5: `run_id`, `status`, `failed_unit`, `finished_at`, `last_repair`, `last_repair_source` (`gate` when a gate wrote that text, `agent` when it is the previous agent's own `sure_finish` summary, `null` when there is no repair), `candidates[]` (slugs that run extracted) |
| `memory_index_snapshot[]` | every entry the memory index knows: `id`, `type`, `status`, `target_skill`, `component`, `cause`, `trigger[]`, `useful`, `disputed`. Use it for check 7 (duplicates) before writing a candidate |
| `units_registry` | `{skill: [unit ids]}`; the only legal values for `cell.component` |

Two facts about the material:

- `fix_window` holds commands, not their output. The event log stores tool inputs only. You can say what was tried and that the unit passed afterwards; you cannot quote what a command printed unless the same text is in `log_tail` or in a file you cite as evidence.
- The digest is capped at 20 KB. When a run is long the hook shrinks it in a fixed order (index snapshot down to id, status and cell; registry down to this skill; prior runs down to 2; log tails down to 10 lines; repairs down to 300 characters; fix windows down to 5 commands). Repairs and fix windows are never removed entirely.

## 3. What to extract, in this order

1. An entry that was shown to this run and disputed (`run.memory_usage[].outcome == "disputed"`): propose `op: "modify"` or `op: "supersede"` with `target_entry` set to it, and cite the `gate_repair` claim of the attempt where it failed. This is the most valuable correction the library can get.
2. A unit that failed and then passed (`outcome == "passed"`, `attempts > 1`, non-empty `fix_window`): a `bad_case` whose trigger comes from `repairs[]` or `log_tail`, whose mitigation is what the fix window shows actually led to the pass.
3. A unit that ended failed where `log_tail` or a cited file pins the cause to a location: a `bad_case` with `causal: true` and a `path:line` in `evidence`.
4. Something this run did differently from `prior_runs` on the same target, when the difference explains the outcome.
5. An environment fact you verified in this run (partition names, CUDA versions, cache layouts, dataset quirks): a `fact`, with the observation written to a file (section 4.2).

Successful runs usually yield nothing new. Recipes (what worked, as a positive procedure) are not an entry type yet; do not dress one up as a bad_case.

## 4. Two entry types

Both are English, strategy-level, and stand alone. Write the pattern, not one model's numbers: "pin torch to the wheel built for the host CUDA" is a lesson; "torch==2.3.1+cu121 fixed Qwen3-ASR" is a diary line. Never paste secrets, tokens, or absolute paths outside the run directory.

### 4.1 bad_case body (`proposal.md`)

```markdown
# <title: the symptom and the cause in one line>

## Trigger
<the error strings or symptoms, verbatim, that mark this case>

## Affected Step
<which skill and which unit hit it>

## Minimum Evidence
<the least you must look at: paths, or path:line>

## Known Mitigation
<what fixed it, as a strategy>

## Verification
<one command that proves the fix, or one file to check>

## Example Artifacts
<optional: paths under this run's directory>
```

Rules the gate enforces on this body:

- the five sections Trigger, Affected Step, Minimum Evidence, Known Mitigation, Verification are required and must not be empty; Example Artifacts is optional; no other H2 sections;
- at most 200 words in the body, not counting the title, any `## ` section heading, and fenced code blocks;
- no line may start with `Trigger:`, `Cell:`, `Source:`, `Added:`, `Status:` or `Superseded-by:` (the publisher writes that five-line header itself; the gate rejects bodies that already carry it);
- printable text only.

### 4.2 fact body (`proposal.md`)

```markdown
# <one sentence stating what is true now>

Scope: cluster | model_family:<name> | dataset:<name>
Checked-at: <YYYY-MM-DD>
Evidence: <path or path:line>

<optional detail, at most 60 words>
```

A fact needs a file on disk that shows it. If you learned it from command output in your context (for example `vc info`), run the command again and tee the output into `artifacts/memory_evidence/<n>.txt`, then cite that file. A fact written from memory has no evidence file and the gate rejects it. Only bash observation commands and `tee` are used for this; nothing else runs from bash in this unit.

The `Scope:` and `Checked-at:` lines above must equal proposal.json's `scope` and `checked_at` fields byte-for-byte, and the `Evidence:` line must be one of the strings in proposal.json's `evidence` array verbatim; the gate rejects a body that disagrees with the JSON.

## 5. proposal.json

One per candidate directory, next to `proposal.md`:

```json
{
  "schema": "sure.memory.proposal.v2",
  "type": "bad_case",
  "op": "add",
  "target_skill": "sure_onboard",
  "target_entry": null,
  "applies_to": ["sure_onboard"],
  "cell": { "component": "build_env", "cause": "cuda_version_mismatch" },
  "trigger": ["no kernel image is available for execution on the device"],
  "causal": true,
  "evidence": ["artifacts/build_env.log:212", "artifacts/build_env_result.json"],
  "claims": [
    { "kind": "gate_repair", "unit": "build_env", "attempt": 1, "status": "failed" },
    { "kind": "unit_result", "unit": "build_env", "attempt": 2, "status": "passed" }
  ],
  "source": { "run_id": "<run_id>", "skill": "sure_onboard", "target": "<run.target.id>", "digest_sha256": "<sha256 of artifacts/run_digest.json>" },
  "similar": null,
  "scope": null,
  "checked_at": null
}
```

Field by field:

- `type`: `bad_case` or `fact`.
- `op`: `add` for a new entry; `modify` when an existing entry is right but incomplete; `supersede` when it is wrong or obsolete. `modify` and `supersede` need `target_entry` = an entry id from the index snapshot; `add` has `target_entry: null`.
- `target_skill`: for a `bad_case`, the skill whose unit hits the problem (`sure_onboard`, `sure_eval`, `sure_infer`, `sure_trans`, `sure_feed`); it may differ from the skill you are running when the fix belongs elsewhere. For a `fact` it is always `_shared`.
- `applies_to`: for a `bad_case` exactly `[target_skill]`. For a `fact` the skills that should see it; default all five: `["sure_onboard", "sure_eval", "sure_infer", "sure_trans", "sure_feed"]`.
- `cell.component`: for a `bad_case` the unit id where the failure surfaced, taken from `units_registry[target_skill]`; for a `fact`, `_`; `_shared` has no units, so `_`. When that unit is one of this run's `units[]`, `claims` has to name it: the hook only offers a bad_case at the unit its component names, so an entry filed on a unit its own claims never mention is offered where the lesson does not apply and is missing where it does.
- `cell.cause`: one of `python_dependency_missing`, `system_dependency_missing`, `cuda_version_mismatch`, `wrong_python_version`, `missing_weights`, `wrong_entrypoint`, `config_not_set`, `runtime_backend_incompatible` (the eval failure taxonomy), `infra`, `job_submission`, `resource_limit`, `data_layout`, `result_layout`, `metric_bypass` (facts use `n.a.`). No free text.
- `trigger`: at most 5 strings. Read check 4 in section 8 before choosing them. For a bad_case one and the same trigger must satisfy both halves at once: it appears verbatim (case-insensitive substring) in this digest's `units[].repairs[].text` or `units[].log_tail.lines`, or in the `last_repair` of a `prior_runs[]` entry whose `last_repair_source` is `gate`; and that same string carries no run-specific text (no `run_id`, no `prior_runs[].run_id`, no `target.id`, no `.sure/runs/` path, more than digits or hex, not a timestamp). Splitting the two halves over two triggers is rejected. The prior-run half is the only route for a unit that runs after `extract_lessons` (`run_report`, `finalize_model_bundle`): its gate text cannot be in this run's `units[]`, so file the bad_case with an empty `claims` and take the trigger from the prior run's gate repair. Triggers that occur only in evidence files are allowed and are kept for the index and prompt-level routing (`sure/memory/index.md`, the README route table), but they are not used for hook injection: at publish the publisher computes `hook_trigger`, the subset of your triggers it can find in the digest's repairs and log tail, and the hook matches on `hook_trigger` only. A trigger outside that subset never brings your entry into a gate repair.
- `causal`: `true` when you can point at the exact place the failure came from; then at least one `evidence` item is `path:line`.
- `evidence`: the paths this proposal rests on. A non-empty list. Relative paths only, no `..`, no absolute paths. Each path is resolved first against this run's directory `.sure/runs/<run_id>/` (its `artifacts/`, `vc_logs/`, `local_logs/`), then against the target directory (onboard: `sure/models/<model_name>/`; eval: the product directory recorded as `runtime.run_dir` in `eval_input_resolved.json`; the gate reads that file itself). Every path must exist; `path:line` must be within the file.
- `claims`: what the digest says happened. A `unit_result` claim names a unit with `attempt` equal to its `attempts` and `status` equal to its `outcome`; a `gate_repair` claim names a unit and an `attempt` that appears in its `repairs[]`, with `status: "failed"`. Every claim must be found in `run_digest.json`.
- `source`: `run_id` and `skill` from `run.*`, `target` = `run.target.id`, `digest_sha256` = the output of `sha256sum <run_dir>/artifacts/run_digest.json`, computed after you read the digest and before you write the declaration. Do not touch the digest in between.
- `similar`: `null`, or `{ "entry": "<entry_id>", "difference": "<one sentence>" }`. Required when your candidate overlaps an existing entry (check 7).
- `scope`, `checked_at`: facts only (`cluster`, `model_family:<name>` or `dataset:<name>`; `YYYY-MM-DD`). `null` for bad_cases.

Values that end up in the index and the README route table (`trigger`, `source`) must not contain `|`, `;` or non-printable characters.

## 6. Where to write, and with what

```
<run_dir>/artifacts/
  run_digest.json                        written by the hook; read only
  candidates/<nn>-<slug>/proposal.json   nn = 01..05; slug = lowercase words of your title joined by "-"
  candidates/<nn>-<slug>/proposal.md
  memory_evidence/<n>.txt                facts only; tee'd observation output
  extraction_declaration.json            written last
```

Use the write tool for every one of these files (it creates the directories). Do not use bash heredocs: the per-unit script whitelist rejects any bash command whose text contains `scripts/<name>.py`, and a Verification section or an evidence path will often contain exactly that. Bash in this unit is for observation commands, `tee`, `sha256sum` and nothing else.

Do not write under `sure/memory/` or under any `references/memory/` directory. Publishing to `sure/memory/provisional/` happens in `post_finish` without you; moving an entry into `references/` is a human step (`cli export`).

## 7. extraction_declaration.json

```json
{
  "schema": "sure.memory.extraction.v2",
  "no_new_lessons": false,
  "no_lessons_reason": null,
  "covered_by": ["sure_onboard/cuda-runtime-mismatch"],
  "candidates": ["01-torch-wheel-does-not-match-host-cuda"],
  "infra_noise": false,
  "infra_evidence": []
}
```

- `no_new_lessons: true` requires `candidates: []` and a non-empty `no_lessons_reason`; `no_new_lessons: false` requires at least one candidate.
- `candidates` lists directory names under `artifacts/candidates/`: single path segments, at most 5, and every directory that exists on disk must be listed.
- `covered_by`: entry ids that already say what this run would have taught. Naming them here is how you say "seen, already in the library" without writing a duplicate.
- `infra_noise: true` says the failures in this run were infrastructure (node down, quota, network), not the model. Then every candidate has `cause: "infra"` and `infra_evidence` holds at least one resolvable path.

After the file exists, call `sure_update_state` as for every other unit; the hook runs the gate on the next tool result.

## 8. The gate, in plain words

The repair text names the failed check and the candidate. Fix what it names; resubmitting the same bytes does not rerun the gate and does not spend an attempt. Changing a candidate file re-runs it even when the declaration is unchanged.

1. Shape: schema strings, enums, required fields, section list, word limits, no provenance header lines, no `|`, `;` or non-printable characters in trigger or source values; a fact's `target_skill` is `_shared`, a bad_case's is not. When a bad_case's `cell.component` is a unit of this run, one of its `claims` must name that unit.
2. Evidence: every path exists under the run directory or the target directory, is relative, has no `..`; line numbers are in range.
3. Claims: every claim is found in the digest (section 5).
4. Triggers: each trigger is at least 8 characters after trimming; is not just a stopword (`error`, `failed`, `failure`, `exception`, `warning`, `missing`, `invalid`, `cuda`, `timeout`, `not found`); still has at least 8 characters after the harness's own repair template phrases are removed; does not start with `re:`. For a bad_case at least one trigger must satisfy both conditions at once: it appears verbatim (case-insensitive) in this digest's `repairs` or `log_tail`, or in a `prior_runs[]` entry's `last_repair` when that entry's `last_repair_source` is `gate` (source `agent` is the previous agent's own sentence and does not count); and that same trigger contains none of this run's `run_id`, no `prior_runs[].run_id`, no `target.id` or `.sure/runs/`, is more than digits or hex, and is not a timestamp. One generic trigger that was never seen, next to one observed trigger that carries the run id, does not pass; a single string has to be both. Read that as: at least one trigger has to be a string that will show up unchanged the next time the same failure happens. Triggers taken only from evidence files are allowed: they are kept for the index and prompt-level routing but are not used for hook injection, because `hook_trigger` (the triggers the hook matches on) is computed at publish from the digest's repairs and log tail, and an evidence-only trigger is never in it. A fact needs at least one trigger too, and each must appear verbatim in the evidence file it cites; for facts `hook_trigger` is the whole trigger list. Scope alone is not enough: `matchFacts` selects on scope **or** a trigger hit, so a scope-only fact would be injected into every run that matches its scope with nothing in the run pointing at it.
5. Infra noise: `infra_noise: true` forces `cause: "infra"` on every bad_case candidate (a fact's `cause` is always `n.a.` regardless) and needs resolvable `infra_evidence`.
6. Causal: `causal: true` needs a `path:line` in `evidence`.
7. Duplicates and cells: a cell (`target_skill/component x cause`) is held shut only by a confirmed, not superseded entry that the hooks can still select; an `add` into such a cell is rejected, use `modify` / `supersede` or `covered_by`. A confirmed occupant the hooks can never select — one with an empty `hook_trigger`, or one whose `component` is `_`, since no unit is named `_` — does not hold its cell shut, because it will never fire and refusing a live lesson in favour of it helps nobody. Such an occupant, and a cell holding only provisional or disputed entries, both allow the `add`, but `similar.entry` must name the occupant and `difference` must be non-empty. An `add` whose trigger set equals an existing entry's is rejected. A trigger set that is a subset of, or overlaps at Jaccard 0.5 or more with, an entry of the same target_skill and component, or a title that is nearly the same sentence, requires `similar` to point at that entry. The same rules apply between candidates of this batch. Legacy entries (no cell yet) do not occupy a cell, but when one of your triggers equals one of theirs you must name that entry in `similar.entry` or `covered_by`. `similar.entry`, when set, must exist in the index.
8. Targets: `modify` / `supersede` need an existing `target_entry`; a bad_case's `applies_to` equals `[target_skill]`.
9. Source: `source.run_id` is this run; `source.digest_sha256` equals both the checkpoint's value and the sha256 of `artifacts/run_digest.json` on disk right now.
10. Declaration: `no_new_lessons` agrees with `candidates` and `no_lessons_reason`; every listed candidate directory exists and every existing directory is listed; no candidate id is listed twice; at most 5.

## 9. If a candidate cannot pass

Drop it. Change the declaration to `no_new_lessons: true` and put the reason in `no_lessons_reason` (for example "trigger only visible in stdout, no log file to cite"). That is a legitimate declaration, not a bypass. After two consecutive gate failures the hook advances the state machine by itself and records `extraction: failed`; do not spend turns fighting the gate.

## 10. Finishing with failed or incomplete

A run that ends with `status: failed` or `incomplete` still extracts. If `artifacts/extraction_declaration.json` is missing or fails the gate when you call `sure_finish`, the hook builds a digest for the run as it stands now, stores its sha in the checkpoint, and returns a repair that says so. In this path do not run any script (the current unit's whitelist would refuse it anyway): read `artifacts/run_digest.json`, write candidates and the declaration by this contract (a `no_new_lessons: true` declaration is fine when the digest shows nothing usable), then call `sure_finish` again. Do not end the turn without doing that. The hook asks at most twice; the third `sure_finish` goes through and the run is recorded as `extraction: failed`.

## Limits (from sure/runtime/memory/config.json)

| key | value | meaning |
|---|---|---|
| `max_candidates_per_run` | 5 | candidate directories per run |
| `max_triggers_per_candidate` | 5 | strings in `trigger` |
| `bad_case_max_words` | 200 | words in a bad_case body, title and code blocks excluded |
| `fact_max_words` | 60 | words in a fact's optional detail |
| `trigger_min_chars` | 8 | characters per trigger after trimming and template removal |
| `extraction_gate_max_failures` | 2 | consecutive gate failures before the hook advances on its own |
| `finish_extraction_max_attempts` | 2 | times `pre_finish` asks for a declaration on a non-success finish |
| `inject_max_entries` | 2 | entries a later run sees at once |
