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
`/cpfs/user/jingpeng/workspace/evaluation-pipeline` for supported metrics:

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

- `prepare_dataset`
- `materialize_templates`
- `validate_execution_shell`
- `wait_for_predictions`
- `validate_predictions`
- `evaluate_predictions`
- `refresh_report`

## Must Not Do

- must not invent new script names without human approval
- must not bypass deterministic scripts for low-uncertainty work
- must not silently omit required validation before evaluation
- must not evaluate predictions without preserving the dataset language and
  post-processing context used by the deterministic evaluator

## Wait Contract

If a route contains `wait_for_predictions`, it must follow:

- [prediction_generation_contract.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/prediction_generation_contract.md)

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

- [main_agent_script_routing.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_script_routing.json)
