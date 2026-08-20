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
| Docker smoke writes `sample_output.json` but the JSON says `overall: FAILED`, or the validation log has a failed contract/load/infer stage | `docker_smoke_false_pass.md` | Use when scripts only test file existence and may turn a failed smoke into a false PASS. |
| Import/load fails with CUDA, cuDNN, torch, torchvision, or operator mismatch | `cuda_runtime_mismatch.md` | Use after capturing exact version/error evidence. |
| `nvidia-smi` sees GPUs but sandboxed Python reports `torch.cuda.is_available() == False`, `device_count == 0`, or `Can't initialize NVML` | `sandbox_cuda_visibility.md` | Use before accepting CPU fallback or marking local uv GPU failed. |
| Validation output is non-empty but fails required field/type checks | `fixture_contract_mismatch.md` | Use when `io_contract` and actual output disagree. |
| Prediction generation skips empty template files because resume is enabled | `empty_prediction_resume.md` | Main-flow related, but tool wrappers may see the same symptom in local validation. |
| ASR WER/CER appears in artifacts but was computed by local edit-distance helper instead of SURE ASR metric classes | `asr_metric_bypass.md` | Use when `validate.py` contains `_edit_distance`, `edit_distance`, or custom WER/CER normalization. |
| Speech-understanding multitask outputs exist but no combined report references task routes and the `prompt_norm` / `classify` nodes | `speech_understanding_metric_bypass.md` | Use when ASR/S2TT/SER/SLU/GR ref/hyp files exist but `speech_understanding_metric_report.json` is missing or incomplete. |
| Kimi-Audio or another dual text/audio stream model emits a stable leading `!` in generated text | `kimi_audio_leading_bang_token.md` | Use before adding wrapper or metric string normalization; collect token-level evidence and fix at detokenization. |
| TTS validation only checks output audio contract or writes ad hoc MOS/SIM fields instead of using `src/sure_eval/evaluation/tasks/tts` | `tts_metric_bypass.md` | Use when TTS artifacts lack `tts_metric_report.json` or do not reference `sure_eval.evaluation.tasks.tts`. |
| VC validation only checks converted audio contract or writes ad hoc similarity/MOS fields instead of using `src/sure_eval/evaluation/tasks/vc` | `vc_metric_bypass.md` | Use when VC artifacts lack `vc_metric_report_local_pipeline.json` or do not reference `sure_eval.evaluation.tasks.vc`. |
| Re-onboarding local uv points to an existing model `.venv` instead of a run-local environment | `reonboard_env_reuse.md` | Use when `models_reonboard/runs/<model>/.venv` is a symlink or validation uses `src/sure_eval/models/<model>/.venv`. |
| VC metric report has semantic/speaker metrics but MOS provider resources are missing after using a new cache dir | `vc_metric_cache_mismatch.md` | Use when errors mention missing `dnsmos`, `EmergentTTS-Eval repo_dir`, or `UTMOS-demo repo_dir`. |
| `vc submit` fails with `partition not found` after using a shorthand queue name | `vc_partition_name_mismatch.md` | Use when a human-facing queue alias differs from the exact `vc submit -p` partition name. |
| VC smoke/load fails with `tp_plan='auto'`, `WORLD_SIZE`, and `device_map="auto"` in Transformers or ModelScope code | `vc_world_size_device_map_auto_tp_plan.md` | Use when a single-GPU `vc submit` job accidentally triggers tensor-parallel/distributed loading. |

## Adding A Bad Case

Each bad-case file should contain:

- trigger strings or symptoms;
- affected workflow step;
- minimum evidence to collect;
- known fix or mitigation;
- verification command;
- links to affected model examples.

Do not add broad narrative history without a trigger and verification path.
