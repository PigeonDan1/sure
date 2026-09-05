# Main Agent DATASET_SCOPE_UNIT Contract

## Purpose

`DATASET_SCOPE_UNIT` is responsible for deciding which canonical datasets should be evaluated for the current model or task.

## Required Output

- `selected_datasets`
- `skipped_datasets`
- `selection_basis`

Each skipped dataset must include a reason.

## Dataset Identity Rules

- `selected_datasets[]` stores source roots under a configured
  `allowed_source_roots` entry at `.../ds_pool/<source_dataset_name>`.
  Source roots must not contain spaces.
- `resolved_datasets[]` stores the resolved identity for each selected source
  root: `dataset_id`, `source_root`, `source_dataset_name`, `version_id`,
  `task`, `language`, and `jsonl_path`.
- The report-facing identity is always
  `dataset_id = <source_dataset_name>__<version_id>` (no task suffix).
  Legacy alias names must not appear as new report identities.
- A trailing `@<version_id>` selects the version when a dataset has more than
  one (e.g. `.../ds_pool/<name>@v1.0.2`).

## Must Use

- model README
- existing tool artifacts
- explicit human constraints

## Must Not Do

- must not guess capability from task name alone
- must not launch scoring directly

## Output Template

- [main_agent_dataset_decision.json](../templates/main_agent_dataset_decision.json)
