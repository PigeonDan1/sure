# Bad Case Memory Index

Bad cases are optional memory. Read this index only after a concrete failure or
known-risk trigger appears. Then read only the matching bad-case file.

Do not pre-load every historical story into default context.

## Route Table

| Trigger or symptom | Suggested memory file | Notes |
|--------------------|-----------------------|-------|
| `server.py`, `validate.py`, or Docker command calls the wrong Python/module | `wrong_entrypoint.md` | Use when entrypoint is declared but runtime starts a different path. |
| ASR WER/CER appears in artifacts but was computed by local edit-distance helper instead of SURE ASR metric classes | `asr_metric_bypass.md` | Use when `validate.py` contains `_edit_distance`, `edit_distance`, or custom WER/CER normalization. |
| Speech-understanding multitask outputs exist but no combined report references task routes and the `prompt_norm` / `classify` nodes | `speech_understanding_metric_bypass.md` | Use when ASR/S2TT/SER/SLU/GR ref/hyp files exist but `speech_understanding_metric_report.json` is missing or incomplete. |
| TTS validation only checks output audio contract or writes ad hoc MOS/SIM fields instead of using `src/sure_eval/evaluation/tasks/tts` | `tts_metric_bypass.md` | Use when TTS artifacts lack `tts_metric_report.json` or do not reference `sure_eval.evaluation.tasks.tts`. |
| VC validation only checks converted audio contract or writes ad hoc similarity/MOS fields instead of using `src/sure_eval/evaluation/tasks/vc` | `vc_metric_bypass.md` | Use when VC artifacts lack `vc_metric_report_local_pipeline.json` or do not reference `sure_eval.evaluation.tasks.vc`. |

## Adding A Bad Case

Each bad-case file should contain:

- trigger strings or symptoms;
- affected workflow step;
- minimum evidence to collect;
- known fix or mitigation;
- verification command;
- links to affected model examples.

Do not add broad narrative history without a trigger and verification path.
