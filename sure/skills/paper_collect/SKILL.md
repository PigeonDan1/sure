# /paper_collect

Collect a deduplicated paper set for the user's topic and produce a standard Sure paper collection artifact.

This reference implementation is intentionally offline and deterministic. It is meant to test the Harness contract and provide a template for later Tavily, Semantic Scholar, arXiv, PDF download, and citation-expansion integrations.

## Inputs

- User arguments from the Sure invocation.
- Optional `target <number>` or `target=<number>` in the user arguments.
- The run directory shown in the Sure invocation.

Default target count is 10 papers. Minimum acceptable target is 1.

## Workflow

1. Parse the topic and target count from user arguments.
2. Call `sure_update_state` with phase `search`.
3. Run:

```bash
node <package_dir>/scripts/paper_collect.mjs \
  --query "<topic>" \
  --target <target> \
  --run-id "<run_id>" \
  --run-dir "<run_dir>" \
  --skill-name "paper_collect"
```

Use the package directory shown in the Sure invocation. For repository skills it is usually `sure/skills/paper_collect`; for project-local overrides it may be `.sure/skills/paper_collect`.

4. Call `sure_update_state` with phase `validate`, counters from the generated `artifacts/papers.manifest.json`, and the paper collection artifact path.
5. Call `sure_finish` with:
   - `status: "success"` when collected paper count is at least the target count.
   - `manifest_path: ".sure/runs/<run_id>/manifest.json"`.
   - `summary` containing collected count, target count, and artifact location.

If the script fails or the collection is incomplete, write whatever partial artifacts exist and finish with `status: "failed"` or `status: "incomplete"` as appropriate.

## Required Outputs

The script writes these files under the run directory:

- `manifest.json`: final Sure manifest envelope for `sure_finish`.
- `artifacts/papers.manifest.json`: normalized paper collection.
- `artifacts/raw_candidates.jsonl`: raw generated candidates before deduplication.
- `artifacts/search_log.jsonl`: search source log.
- `artifacts/dedupe_index.json`: dedupe decisions.
- `artifacts/failures.jsonl`: failed records, empty for the offline happy path.
- `artifacts/metadata/*.json`: one metadata file per collected paper.

## Success Criteria

Success requires:

- final manifest envelope is valid JSON
- final manifest status matches `sure_finish.status`
- `artifacts/papers.manifest.json` exists
- `papers` contains at least `target_count` entries
- no duplicate `dedupe_key`
- every paper has `id`, `title`, `year`, `authors`, `source`, `source_rank`, `dedupe_key`, and `download_status`

The `pre_finish` hook enforces these checks. If it returns repair instructions, fix the artifacts and call `sure_finish` again.
