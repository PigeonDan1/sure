MAIN_FLOW_INPUT:
 user_goal: evaluate_existing_model

 target:
    model_name: asr_kimi_audio
    model_dir: sure/models/asr_kimi_audio
    tool_workflow_ready: true

 constraints:
    allow_tool_workflow: true
    allowed_tasks: [ASR, S2TT, GR, SER, SLU]
    allowed_datasets: data/datasets/librispeech_test_clean
    blocked_datasets: []
    dry_run: false

 harness:                          # ← 新增字段
    mandatory_doc: docs/agents/main_flow_agent/AGENTS.md
    execution_surface_template: docs/agents/main_flow_agent/templates/run_single_model.sh
    prediction_source: regenerate
    isolate_from_prior_runs: true

 evidence:
    readme_path: sure/models/asr_kimi_audio/README.md
    config_path: sure/models/asr_kimi_audio/config.yaml
    artifacts_dir: sure/models/asr_kimi_audio/artifacts
    model_spec_path: sure/models/asr_kimi_audio/model.spec.yaml

 runtime_context:
    available_scripts:
    - scripts/prepare_sure_dataset.py
    - scripts/materialize_predictions_template.py
    - scripts/validate_prediction_files.py
    - scripts/evaluate_predictions.py
    - scripts/refresh_report_snapshot.py
    - scripts/generate_predictions_via_server.py
    templates_dir: docs/agents/main_flow_agent/templates/
    output_dir: sure/models/asr_kimi_audio/eval_runs/main_agent_asr_kimi_audio_001
