# Bad Case Memory Index

Bad cases are optional memory. Read this index only after a concrete failure or
known-risk trigger appears. Then read only the matching bad-case file.

Do not pre-load every historical story into default context. The merged index
`sure/memory/index.md` (repo root) covers this table plus the not yet confirmed
entries under `sure/memory/`; look there first.

## Route Table

| Trigger or symptom | Suggested memory file | Notes |
|--------------------|-----------------------|-------|

## Adding A Bad Case

Do not add rows or files here by hand. Entries are extracted by the
`extract_lessons` unit of a run, published to `sure/memory/provisional/`,
confirmed by use or by a person, and moved into this directory with
`python3 -s sure/runtime/memory/cli.py export <entry_id>`, which also keeps
the route table above in sync (one row per exported file; rows whose file is
gone are removed). Then commit the file and this README together.

Each bad-case file carries a five-line provenance header (`Trigger:`, `Cell:`,
`Source:`, `Added:`, `Status:`) followed by the six-section body described in
`sure/runtime/memory/EXTRACTION.md`.
