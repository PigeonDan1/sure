# {model_full_name} Evaluation Snapshot

## Basic Information

- Model name: `{model_full_name}`
- Model source: {model_source}
- Task: {task}
- Dataset: `{datasets}`
- Run ID: `{run_id}`
- Output directory: `{run_dir}`
- Standard results mirror: `{results_dir}`
- Status: {status}

## Dataset Scope

{dataset_scope}

## Runtime Environment

- Execution path: {execution_path}
- Python environment for evaluation scripts: {eval_python_env}
- Python environment for model server: {server_python_env}
- Python version used by evaluation environment: {python_version}
- Device override: {device}
- Model weights: {model_weights}
- Model cache path: `{model_cache_path}`

## Model Input

The run used the repository MCP server tool:

```text
{tool_signature}
```

The model server was invoked through `scripts/generate_predictions_via_server.py` with protocol `{protocol_id}` and tool name `{tool_name}`.

## Result Summary

{result_summary}

## Validation Summary

{validation_summary}

## Evaluation Pipeline

{evaluation_pipeline}

## Runtime And Tool Versions

{runtime_versions}

## Output Artifacts

{output_artifacts}

## Test Notes

{test_notes}
