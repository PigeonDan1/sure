# Main Agent Worked Example

This document provides a minimal hand-walked example for evaluating whether the current main-flow harness is self-consistent.

---

## Scenario

We assume:

- task: `evaluate_existing_model`
- model: an already integrated ASR model
- README indicates ASR support only
- tool workflow is already ready
- no prediction files exist yet

---

## Example Inputs

### MAIN_FLOW_INPUT

```yaml
user_goal: evaluate_existing_model

target:
  model_name: demo_asr_model
  model_dir: src/sure_eval/models/demo_asr_model
  tool_workflow_ready: true

constraints:
  allow_tool_workflow: true
  allowed_tasks: [ASR]
  allowed_datasets: null
  blocked_datasets: []
  dry_run: false

evidence:
  readme_path: src/sure_eval/models/demo_asr_model/README.md
  config_path: src/sure_eval/models/demo_asr_model/config.yaml
  artifacts_dir: src/sure_eval/models/demo_asr_model/artifacts
  prior_results: []
```

---

## Example Unit Outputs

### 1. task_classification.json

```json
{
  "run_id": "demo_run_001",
  "timestamp": "2026-04-09T00:00:00Z",
  "task_type": "evaluate_existing_model",
  "reason": "model is already integrated and the user requested evaluation",
  "need_tool_workflow": false,
  "confidence": "high",
  "input_signals": [
    "tool_workflow_ready=true",
    "user_goal=evaluate_existing_model"
  ]
}
```

### 2. main_agent_plan.json

```json
{
  "run_id": "demo_run_001",
  "timestamp": "2026-04-09T00:00:01Z",
  "goal": "Evaluate an already integrated ASR model on selected ASR datasets",
  "task_type": "evaluate_existing_model",
  "need_tool_workflow": false,
  "execution_steps": [
    "prepare_dataset",
    "materialize_templates",
    "wait_for_predictions",
    "validate_predictions",
    "evaluate_predictions"
  ],
  "stop_condition": "stop after evaluation if predictions validate successfully",
  "notes": []
}
```

### 3. dataset_decision.json

```json
{
  "run_id": "demo_run_001",
  "timestamp": "2026-04-09T00:00:02Z",
  "selection_basis": [
    "README states ASR support",
    "allowed_tasks=[ASR]"
  ],
  "selected_datasets": ["aishell1", "librispeech_clean"],
  "skipped_datasets": [
    {
      "dataset": "covost2_en2zh",
      "reason": "README does not indicate S2TT support"
    }
  ]
}
```

### 4. script_routing.json

```json
{
  "run_id": "demo_run_001",
  "timestamp": "2026-04-09T00:00:03Z",
  "routing_reason": "Need canonical datasets, prediction templates, validation, and scoring",
  "wait_points": ["wait_for_predictions"],
  "stop_condition": "stop and report if prediction validation fails",
  "steps": [
    {
      "name": "prepare_dataset",
      "script": "scripts/prepare_sure_dataset.py",
      "inputs": {
        "datasets": ["aishell1", "librispeech_clean"]
      }
    },
    {
      "name": "materialize_templates",
      "script": "scripts/materialize_predictions_template.py",
      "inputs": {
        "datasets": ["aishell1", "librispeech_clean"]
      }
    },
    {
      "name": "wait_for_predictions",
      "script": null,
      "inputs": {}
    }
  ]
}
```

### 5. assessment_report.json

```json
{
  "run_id": "demo_run_001",
  "timestamp": "2026-04-09T00:10:00Z",
  "status": "partial_success",
  "evidence": [
    "datasets prepared",
    "templates generated",
    "prediction files not yet filled"
  ],
  "blocking_issues": [
    "waiting for prediction files"
  ],
  "next_action": "fill prediction files and resume from validation",
  "notes": []
}
```

### 6. main_agent_run_report.json

```json
{
  "run_id": "demo_run_001",
  "timestamp": "2026-04-09T00:10:01Z",
  "task_type": "evaluate_existing_model",
  "goal": "Evaluate an already integrated ASR model on selected ASR datasets",
  "selected_datasets": ["aishell1", "librispeech_clean"],
  "executed_steps": [
    "prepare_dataset",
    "materialize_templates"
  ],
  "status": "partial_success",
  "artifacts": {
    "task_classification": "task_classification.json",
    "plan": "main_agent_plan.json",
    "dataset_decision": "dataset_decision.json",
    "script_routing": "script_routing.json",
    "assessment_report": "assessment_report.json"
  },
  "next_action": "fill prediction files and continue with validation + evaluation",
  "notes": []
}
```

---

## What This Example Verifies

This worked example is considered successful if:

- all units can be filled without ambiguity
- selected vs skipped datasets are clearly justified
- routing includes an explicit wait point
- assessment and run report stay consistent

If any of those cannot be written cleanly, the harness spec still has a gap.
