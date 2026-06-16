# Bad Case Memory Index

Bad cases are optional memory. Read this index only after a concrete failure or
known-risk trigger appears. Then read only the matching bad-case file.

Do not pre-load every historical story into default context.

## Route Table

| Trigger or symptom | Suggested memory file | Notes |
|--------------------|-----------------------|-------|
| Model weights exist but wrapper cannot find them; ModelScope path differs from repo id | `modelscope_cache_layout.md` | Useful for `.` escaped as `___`, provider cache roots, and `weights_manifest.json` resolution. |
| `server.py`, `validate.py`, or Docker command calls the wrong Python/module | `wrong_entrypoint.md` | Use when entrypoint is declared but runtime starts a different path. |
| Docker command starts but mounted files are missing in container | `docker_mount_missing.md` | Use when bind mounts or workdir are wrong. |
| Import/load fails with CUDA, cuDNN, torch, torchvision, or operator mismatch | `cuda_runtime_mismatch.md` | Use after capturing exact version/error evidence. |
| Validation output is non-empty but fails required field/type checks | `fixture_contract_mismatch.md` | Use when `io_contract` and actual output disagree. |
| Prediction generation skips empty template files because resume is enabled | `empty_prediction_resume.md` | Main-flow related, but tool wrappers may see the same symptom in local validation. |

## Adding A Bad Case

Each bad-case file should contain:

- trigger strings or symptoms;
- affected workflow step;
- minimum evidence to collect;
- known fix or mitigation;
- verification command;
- links to affected model examples.

Do not add broad narrative history without a trigger and verification path.
