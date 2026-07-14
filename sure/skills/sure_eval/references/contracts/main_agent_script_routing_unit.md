# Main Agent SCRIPT_ROUTING_UNIT Contract

## Purpose

`SCRIPT_ROUTING_UNIT` is responsible for turning main-agent decisions into an ordered deterministic execution route.

It bridges:

- task classification
- plan
- dataset decision
- execution-surface materialization
- deterministic scripts

## Required Output

- `steps`
- `routing_reason`
- `wait_points`
- `stop_condition`

Each step should include:

- `name`
- `script`
- `inputs`
- `outputs`
- `completion_criteria`

For evaluation steps, the route should also preserve dataset-level evaluation
context:

- dataset task
- dataset language
- metric selected for that dataset
- language-aware post-processing / normalization policy

This is required because SURE-EVAL metrics are not purely model-level
operations. ASR, S2TT, SER, GR, SLU, SD, and SA-ASR datasets can require
different language-specific normalization and scoring behavior even when they
share the same model.

The deterministic evaluation route must inherit the compatible behavior from
the SURE evaluation pipeline for supported metrics:

- ASR: `asr_num2words` normalization, `clean_marks.strip_all_punct`, then
  language-driven WER/CER scoring
- code-switch ASR: MER on mixed tokens, WER on English tokens, CER on Chinese
  tokens
- SER/GR: label normalization and numeric-label mapping
- S2TT: sacreBLEU BLEU/chrF2 with tokenizer selected by dataset language
- SLU: prompt-option restoration compatible with `process_prediction.py`
- SD: meeteval DER with the configured collar
- SA-ASR: `text_normalizer.normalize_text`, meeteval cpWER, and meeteval DER

## Allowed Step Types

The following step names constitute the exclusive whitelist for SCRIPT_ROUTING_UNIT. Any `step.name` not in this list is a contract violation:

- `prepare_dataset`
- `materialize_templates`
- `validate_execution_shell`
- `wait_for_predictions`
- `validate_predictions`
- `evaluate_predictions`
- `refresh_report`

## Script Path Constraints

Every `step.script` in `script_routing.json` must satisfy the following constraints:

1. **Must start with `scripts/`**: Only deterministic scripts under the `scripts/` directory are valid routing targets.
2. **Forbidden prefixes**: `demo/`, `examples/`, `tests/`, `src/`
3. **File must exist on disk**: The referenced script file must actually exist in the repository.

Rationale: Scripts under `demo/` and `examples/` are sample code for human developers to learn the API. They are not production-grade deterministic execution paths. Agent Flow must use validated scripts from `scripts/`.

## Must Not Do

- must not invent new script names without human approval
- must not bypass deterministic scripts for low-uncertainty work
- must not silently omit required validation before evaluation
- must not evaluate predictions without preserving the dataset language and
  post-processing context used by the deterministic evaluator
- must not route to scripts outside the `scripts/` directory (e.g., `demo/`, `examples/`)
- must not use step names outside the Allowed Step Types whitelist

## Compliance Check Protocol

Before emitting `script_routing.json`, the Agent **must** perform a compliance self-check and produce `script_routing_compliance_check.json`.

### Self-Check Requirements

1. Verify every `step.script` starts with `scripts/`.
2. Verify no `step.script` uses a forbidden prefix (`demo/`, `examples/`, `tests/`, `src/`).
3. Verify every `step.script` references a file that exists on disk.
4. Verify every `step.name` is in the Allowed Step Types whitelist.

### Self-Check Pass Criteria

- **Zero violations**: All four checks above must pass.
- Only after passing the self-check may the Agent proceed to emit `script_routing.json`.
- If the self-check fails, the Agent must emit `blocking_issues` and halt. It is forbidden to produce `script_routing.json`.

### Self-Check Output Template

- [script_routing_compliance_check.json](../templates/script_routing_compliance_check.json)

### Code-Level Validation (Interception Layer)

After the self-check passes, `script_routing.json` must also pass mandatory code-level validation:

```bash
python -m sure_eval.agent.validators <path-to-script_routing.json>
```
Run from the repository root. Replace <path-to-script_routing.json> with the actual path.
Example:
  python -m sure_eval.agent.validators \
    sure/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_001/script_routing.json


The validator (`src/sure_eval/agent/validators.py`) performs:
- JSON Schema validation (step type whitelist, required fields)
- Script path prefix validation (`scripts/` + forbidden prefix checks)
- Script file disk existence validation

A `script_routing.json` that fails this validation must not proceed to EXECUTION_SURFACE_UNIT or any subsequent stage.

## Wait Contract

If a route contains `wait_for_predictions`, it must follow:

- [prediction_generation_contract.md](prediction_generation_contract.md)

This means `wait_for_predictions` must specify:

- where prediction files are written
- which datasets are being generated
- how completion is determined
- which status file records progress

If the final handoff surface is a one-click shell entrypoint, the route should
also define a shell-materialization step before preflight validation.

That materialization should specify:

- shell path
- template or generation method
- resolved model / dataset / run-directory inputs
- expected output artifacts

Only after that may the route define a preflight shell-validation step.

That validation should specify:

- shell path
- bounded smoke-test mode
- expected smoke-test artifacts
- stop condition if shell validation fails

## Output Template

- [main_agent_script_routing.json](../templates/main_agent_script_routing.json)
