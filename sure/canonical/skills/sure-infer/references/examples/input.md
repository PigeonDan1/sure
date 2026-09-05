MAIN_FLOW_INPUT:
 user_goal: evaluate_existing_model

 target:
    model_name: asr_sensevoice_small
    model_dir: sure/models/asr_sensevoice_small
    tool_workflow_ready: true

 constraints:
    allow_tool_workflow: true
    allowed_tasks: [ASR]
    allowed_datasets: /srv/sure/datasets/group/store/ds_pool/example-librispeech-test-clean
    blocked_datasets: []
    dry_run: false

 harness:                          # ← 新增字段
    mandatory_doc: sure/skills/sure_infer/SKILL.md
    execution_surface_template: sure/skills/sure_infer/scripts/infer_entrypoint.py
    prediction_source: regenerate
    isolate_from_prior_runs: true

 evidence:
    readme_path: sure/models/asr_sensevoice_small/README.md
    config_path: sure/models/asr_sensevoice_small/config.yaml
    artifacts_dir: sure/models/asr_sensevoice_small/artifacts
    model_spec_path: sure/models/asr_sensevoice_small/model.spec.yaml

 runtime_context:
    available_scripts:
    - scripts/prepare_sure_dataset.py
    - scripts/materialize_predictions_template.py
    - scripts/validate_prediction_files.py
    - scripts/evaluate_predictions.py
    - scripts/refresh_report_snapshot.py
    - scripts/generate_predictions_via_server.py
    templates_dir: sure/skills/sure_infer/scripts/templates/
    output_dir: sure/models/asr_sensevoice_small/eval_runs/main_agent_asr_sensevoice_small_001
