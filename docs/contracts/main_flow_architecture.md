# Main Flow Architecture Contract

**Version**: 1.0  
**Scope**: Main flow agent, deterministic evaluation scripts, existing tool onboarding workflow  
**Status**: Active project guideline

---

## Purpose

This document defines the current top-level architecture for SURE-EVAL.

The project intentionally separates:

1. high-uncertainty decisions handled by the **main flow agent**
2. mature tool onboarding handled by the **existing harness-first tool workflow**
3. low-uncertainty execution handled by the **deterministic script layer**

This contract is meant to keep the system simple, auditable, and stable while the project evolves.

---

## Current Architecture

SURE-EVAL currently standardizes on the following structure:

- **One main flow agent**
- **One existing tool onboarding workflow**
- **One deterministic script layer**

No additional general-purpose sub-agents are required by default.

The existing tool onboarding workflow is considered mature and should remain unchanged unless a human explicitly requests redesign.

---

## Role Boundaries

### 1. Main Flow Agent

The main flow agent is the only agent responsible for global orchestration.

It is responsible for:

- understanding the user goal
- determining whether the task is onboarding, evaluation, repair, or audit
- reading model README / config / artifacts / current repo state
- deciding which datasets should be evaluated for the current model
- deciding when to invoke the existing tool onboarding workflow
- deciding the execution order of deterministic scripts
- validating shell / execution readiness before a human launches a background run
- interpreting script outputs and deciding next actions

It is **not** responsible for re-implementing evaluation logic, dataset conversion logic, or report generation logic.

---

### 2. Existing Tool Onboarding Workflow

The tool onboarding workflow under `src/sure_eval/models/` remains the dedicated solution for model integration.

It is responsible for:

- backend selection
- dependency isolation
- import / load / infer / contract validation
- wrapper generation
- model-specific artifacts

It should continue to follow existing harness-first policies and contracts.

This workflow is treated as a stable subsystem. The main flow architecture must integrate with it, not redesign it.

---

### 3. Deterministic Script Layer

The deterministic script layer is responsible for low-uncertainty, repeatable operations.

These scripts are the required execution surface for evaluation-related operations:

- [prepare_sure_dataset.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/prepare_sure_dataset.py)
- [materialize_predictions_template.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/materialize_predictions_template.py)
- [validate_prediction_files.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/validate_prediction_files.py)
- [evaluate_predictions.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/evaluate_predictions.py)
- [refresh_report_snapshot.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/refresh_report_snapshot.py)

The script layer is responsible for:

- canonical dataset preparation
- prediction template generation
- prediction file validation
- metric / RPS computation
- result recording
- report snapshot refresh

Scripts must remain deterministic and machine-consumable.

---

## Control Principle

The controlling rule is:

**The main flow agent decides scope. The script layer enforces format and execution.**

This means:

- the agent decides **what should be run**
- the scripts decide **how stable execution is performed**

---

## Inputs Controlled by the Main Flow Agent

The following inputs are agent decisions:

- whether the current task is onboarding, evaluation, repair, or audit
- whether the tool onboarding workflow must be invoked
- which datasets are appropriate for the current model
- which datasets should be skipped because of capability mismatch
- whether a result should be treated as partial, valid, or blocked
- whether to continue, retry, narrow scope, or escalate

These decisions may use:

- model README
- model spec
- wrapper contract
- existing artifacts
- prior evaluation outputs
- explicit human instructions

---

## Outputs That Must Land on Script Interfaces

The following outputs must be converted into deterministic script-layer inputs or outputs.

### 1. Dataset selection

The main flow agent must output a canonical dataset list, for example:

```json
["aishell1", "librispeech_clean", "covost2_en2zh"]
```

This list is the input to dataset preparation and template generation.

### 2. Prediction templates

For every selected dataset, prediction files must be materialized as:

```text
<dataset>.txt
```

with line format:

```text
key<TAB>prediction
```

Template generation must go through:

- [materialize_predictions_template.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/materialize_predictions_template.py)

### 3. Prediction validation payload

Before scoring, prediction files must be validated through:

- [validate_prediction_files.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/validate_prediction_files.py)

The system should rely on structured validation output, not ad hoc parsing.

### 4. Evaluation payload

Formal scoring must go through:

- [evaluate_predictions.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/evaluate_predictions.py)

The resulting structured payload is the canonical evaluation output for the main flow.

### 5. Report refresh output

Report refresh must go through:

- [refresh_report_snapshot.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/refresh_report_snapshot.py)

This keeps reporting logic outside the agent.

### 6. Execution-readiness evidence

If the final handoff surface is a shell entrypoint, the main flow agent must
leave structured execution-readiness evidence before recommending a background
run.

This evidence should confirm:

- the shell entrypoint is syntactically valid
- a bounded smoke mode exists
- bounded smoke execution can create valid run evidence under the current
  environment

---

## What Should Not Be Added Prematurely

The current architecture explicitly avoids:

- a large multi-agent swarm
- a separate generic dataset agent
- a separate metric agent
- a separate report agent
- agent-side reimplementation of deterministic scripts

These may only be introduced if there is clear evidence that the main flow agent becomes a bottleneck.

---

## Expansion Rule

Future architectural expansion must follow this order:

1. prefer improving deterministic scripts first
2. prefer improving main flow prompts / planning second
3. introduce new agents only when a repeated, high-uncertainty decision class cannot be handled cleanly by the main flow agent

In other words:

**add scripts before adding agents**

---

## Current Recommended Flow

The recommended end-to-end flow is:

1. Main flow agent determines the task type
2. If needed, invoke the existing tool onboarding workflow
3. Main flow agent chooses canonical datasets
4. Run [prepare_sure_dataset.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/prepare_sure_dataset.py)
5. Run [materialize_predictions_template.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/materialize_predictions_template.py)
6. Fill prediction files through the chosen model path
7. Run [validate_prediction_files.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/validate_prediction_files.py)
8. Run [evaluate_predictions.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/evaluate_predictions.py)
9. Run [refresh_report_snapshot.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/refresh_report_snapshot.py)

---

## Summary

The current project policy is:

- keep exactly one main flow agent
- keep the existing tool onboarding workflow unchanged
- push evaluation stability into deterministic scripts

This is the default architectural guideline until explicit human revision.
