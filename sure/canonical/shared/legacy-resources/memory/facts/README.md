# Shared Facts

Facts are short, dated statements about the environment that every skill
shares: partition names, CUDA versions, cache layouts, dataset quirks. They are
the `fact` entry type of the SURE memory system; the format and the rules for
writing one are in `sure/runtime/memory/EXTRACTION.md`, section 4.2.

This directory holds confirmed facts only, one file per fact, git tracked.

## How a file gets here

1. A run's `extract_lessons` unit writes a fact candidate with a file as
   evidence.
2. `post_finish` publishes it to `sure/memory/provisional/_shared/<slug>/`
   (outside git).
3. A person confirms it (`python3 -s sure/runtime/memory/cli.py confirm
   <entry_id>`; facts never auto-confirm) and exports it here
   (`python3 -s sure/runtime/memory/cli.py export _shared/<slug>`), then
   commits the file.

Do not write files here by hand; the index would not know their provenance.

## Format

The five header lines are written by the publisher, never by hand:

```markdown
Trigger: <trigger strings, separated by "; ", may be empty>
Cell: _shared/_ x n.a.
Source: <run and target that produced it>
Added: <YYYY-MM-DD>
Status: confirmed

# <one sentence stating what is true now>

Scope: cluster | model_family:<name> | dataset:<name>
Checked-at: <YYYY-MM-DD>
Evidence: <path or path:line>

<optional detail, at most 60 words>
```

## Index

`sure/memory/index.md` (repo root, built by the `pre_start` hook) lists every
fact with its scope and marks it `[stale]` once it is older than the per-scope
limit in `sure/runtime/memory/config.json` (`stale_after_days`). Stale facts
are flagged, not deleted; re-check them and supersede. This README keeps no
list of its own.

## Note on discovery

`sure/skills/_shared/` has no `sure.skill.json` on purpose: skill discovery
skips it, so nothing here is a slash command.
