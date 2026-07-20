# Main Agent EXECUTION_READINESS_UNIT Contract

## Purpose

`EXECUTION_READINESS_UNIT` is the final preflight gate before a human launches
the generated shell in the background.

Its purpose is to make shell validation part of the main-agent flow, so users
do not discover shell/runtime issues only after starting a full one-click run.

This unit runs only after `EXECUTION_SURFACE_UNIT` has materialized the final
handoff surface.

## Required Output

- `execution_ready`
- `status`
- `validation_mode`
- `validated_shell_entrypoint`
- `smoke_test_command`
- `smoke_test_run_dir`
- `gpu_preflight`
- `checked_artifacts`
- `blocking_issues`
- `next_action`

## Validation Modes

Allowed values:

- `syntax_only`
- `smoke_test`
- `smoke_test_with_prediction_generation`

For single-model single-dataset shells, the recommended default is:

- `smoke_test_with_prediction_generation`

## Required Checks

A compliant execution-readiness validation should check:

1. the shell entrypoint exists
2. the shell passes `bash -n`
3. the shell supports a bounded smoke mode
4. the smoke mode can create a valid model-local run directory
5. the smoke mode can write required run evidence
6. prediction generation can at least begin under the approved execution path
7. **GPU preflight** (if model declares `resources.gpu: true`):
   - run `nvidia-smi` to inspect GPU availability and memory status
   - identify GPUs with sufficient free memory for model inference
   - record recommended `DEVICE` setting (e.g., `cuda:0`, `cuda:1`, or `cpu`)
   - if no GPU has enough memory, mark `cpu_fallback_ready: true` and document the fallback path
8. **Evaluation router preflight**:
   - confirm every evaluation step routes through `scripts/evaluate_predictions.py`
     or `sure-eval metric describe/run`, and then through
     `sure_eval.evaluation.scripts.run_task(...)`
   - confirm the selected task route is backed by
     `src/sure_eval/evaluation/tasks/<task>/routes.yaml`
   - confirm required metric artifacts include
     `metrics/<dataset>/<metric_slug>/report.json` and
     `metrics/<dataset>/<metric_slug>/pipeline_description.json`
   - for ASR and other lightweight text routes, do not replace route-backed
     normalization/scoring with shell-local or model-image metric code
9. **TTS single-process inference parity** (if vc is available and mandatory):
   - this is 本次 TTS 音频生成经验; it should not be generalized to all
     main-flow tasks or VC without separate evidence
   - for TTS inference, do not accept a local one-sample smoke test as proof
     that vc execution is ready
   - require a bounded TTS smoke test through the same `vc submit` image/path
     used by the full run
   - if model load fails under vc with a Transformers/ModelScope tensor-parallel
     error such as `tp_plan='auto'`, inspect whether vc-injected distributed
     variables (`WORLD_SIZE`, `RANK`, `LOCAL_RANK`, `MASTER_ADDR`, `MASTER_PORT`)
     affected a single-process loader
   - classify confirmed TTS cases as a vc/main-flow environment compatibility
     issue unless model-specific evidence proves otherwise
   - do not globally strip distributed variables in main-flow scripts; require a
     TTS model-local wrapper or runtime configuration change for
     single-process loaders only
10. **TTS/VC audio node-local evaluation preflight**:
   - treat inference and evaluation as separate execution surfaces
   - confirm evaluation is routed through `src/sure_eval/evaluation` and the
     node-local uv environments/checkpoints under
     `src/sure_eval/evaluation/nodes/*`
   - if cluster GPU execution is required, the `vc submit -i ...` image is only
     a base runtime shell with interpreter/CUDA/`ffmpeg`; it is not the metric
     dependency source
   - for `tts_wer`/`vc_wer`, verify `ffmpeg` is available in the execution
     surface and that the configured node-local transcription runtime can load
     from its checkpoint/cache
   - if prediction validation already passed, require evaluation-only retry
     semantics for subsequent attempts
   - mark the run incomplete if the vc job completed but
     `evaluation_payload.json`, `report.jsonl`, `protocol.yaml`, or
     `metrics/<dataset>/<metric_slug>/report.json` is missing
11. **TTS inference runtime preflight**:
   - if `vc_runtime_contract.runtime_paths.container_python_path` is declared,
     verify that the interpreter exists in the same image/path used by the vc
     inference job
   - if the image does not follow the default `/opt/<model>_venv/bin/python`
     convention, require an explicit container Python or venv path before full
     TTS inference
   - verify the generated vc command does not prepend host `.venv`,
     `.venv.hostbak`, or host site-packages directories to container
     `PYTHONPATH`
   - verify TTS prompt/reference text fields are preserved in the generated
     tool-call arguments when the canonical samples contain them
   - verify TTS semantic metrics match the dataset language route (`tts_cer`
     for Chinese-family languages; `tts_wer` for English) before launching
     evaluation-only jobs

## Required Evidence

This unit should leave evidence for:

- shell path
- smoke-test command
- smoke-test mode
- run directory used for smoke validation
- whether `prepare_summary.json` exists
- whether `predictions/manifest.json` exists
- whether `prediction_generation_status.json` exists
- whether the smoke test reached a valid success or blocking point

## Valid Outcomes

- `execution_ready`
- `execution_blocked_model_runtime`
- `execution_blocked_shell_contract`
- `execution_not_applicable`

## Must Not Do

- must not treat `phase_1 PASSED` as sufficient proof that background shell
  execution is safe
- must not skip bounded smoke validation when the final handoff surface is a
  shell entrypoint
- must not mark `execution_ready=true` if prediction generation cannot begin in
  the current environment
- must not validate a shell path that has not been materialized by
  `EXECUTION_SURFACE_UNIT`
- must not skip GPU preflight when the model config declares GPU requirements
- must not recommend `execution_ready=true` without a valid GPU or CPU fallback path
- must not mark execution ready if the evaluation step bypasses the
  `src/sure_eval/evaluation` router or omits route-backed metric artifacts
- for TTS, must not treat local smoke success as equivalent to vc smoke
  success when `vc submit` is the mandatory execution path; this experience
  不应泛化到所有 main-flow 任务，也不应泛化到 VC
- for TTS, must not mark a model as broken solely because local smoke passes
  but vc smoke fails during model load; first check vc-injected distributed
  variables and container Python/venv parity
- for TTS/VC, must not mark execution ready for full metric evaluation without
  an evaluation-surface dependency preflight
- for TTS/VC, must not treat a Completed vc job as evaluation success when the
  metric artifacts are missing or empty
- for TTS, must not mark execution ready if the vc inference command depends
  on a guessed nonexistent container venv path or imports host `.venv.hostbak`
  inside the container
- for TTS, must not drop available prompt/reference text during smoke or
  full prediction generation
- for TTS, must not launch semantic evaluation with a metric that conflicts with
  the canonical dataset language route

## Related Contracts

- [main_agent_execution_surface_unit.md](main_agent_execution_surface_unit.md)
- [main_agent_script_routing_unit.md](main_agent_script_routing_unit.md)
- [single_model_single_dataset_shell.md](single_model_single_dataset_shell.md)
- [prediction_generation_contract.md](prediction_generation_contract.md)

## Output Template

- [main_agent_execution_readiness_report.json](../templates/main_agent_execution_readiness_report.json)
