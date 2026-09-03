# Bad Case Memory Index

Bad cases are optional memory. Read this index only after a concrete failure or
known-risk trigger appears. Then read only the matching bad-case file.

Do not pre-load every historical story into default context.

## Route Table

| Trigger or symptom | Suggested memory file | Notes |
|--------------------|-----------------------|-------|

A fresh clone ships no confirmed bad cases: the table fills as entries are
confirmed and exported here (`cli.py export` reconciles it).

## Adding A Bad Case

Each bad-case file should contain:

- trigger strings or symptoms;
- affected workflow step;
- minimum evidence to collect;
- known fix or mitigation;
- verification command;
- links to affected model examples.

Do not add broad narrative history without a trigger and verification path.
