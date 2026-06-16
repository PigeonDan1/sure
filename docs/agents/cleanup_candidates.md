# Cleanup Candidates

This document records possible future cleanup targets after the new agent
layout is stable. It is not an approval to delete files.

Before deleting any item here:

1. confirm the replacement path is documented in the relevant agent README;
2. run `rg` for every old path and public import path;
3. run the smoke tests for both `main_flow_agent` and `model_tool_agent`;
4. get explicit user approval for the exact deletion list.

## Agent Document Compatibility

| Candidate | Current replacement | Delete condition |
|-----------|---------------------|------------------|
| `src/sure_eval/agent/AGENTS.md` | `docs/agents/main_flow_agent/AGENTS.md` | No scripts, prompts, or user docs reference the old path. |
| `src/sure_eval/models/AGENTS.md` | `docs/agents/model_tool_agent/AGENTS.md` | No harness prompt or README still loads the old path. |
| `src/sure_eval/models/task_playbooks/` | `docs/agents/model_tool_agent/task_playbooks/` | All task routing and onboarding docs use the new path. |
| root `templates/` | `docs/agents/main_flow_agent/templates/` or `docs/agents/model_tool_agent/templates/` | Both workflow READMEs point to agent-specific templates and existing automation uses the new locations. |

## Old Documentation Roots

| Candidate | Current replacement | Delete condition |
|-----------|---------------------|------------------|
| `docs/contracts/` | `docs/agents/model_tool_agent/contracts/` where model-tool specific | Contract references are agent-scoped and old root docs are not linked. |
| `docs/playbooks/` | `docs/agents/model_tool_agent/playbooks/` where model-tool specific | Playbook references are agent-scoped and old root docs are not linked. |
| `docs/policies/` | `docs/agents/model_tool_agent/policies/` where model-tool specific | Policy references are agent-scoped and old root docs are not linked. |
| `docs/specs/` | `docs/agents/model_tool_agent/specs/` where model-tool specific | Spec references are agent-scoped and old root docs are not linked. |

## Evaluation Compatibility

| Candidate | Current replacement | Delete condition |
|-----------|---------------------|------------------|
| `src/sure_eval/evaluation/metrics.py` aggregate imports | task namespaces under `src/sure_eval/evaluation/{task}/` plus `evaluation/registry.py` | Completed in this cleanup pass after parity checks. |
| `src/sure_eval/evaluation/wenet_compute_cer.py` | `src/sure_eval/evaluation/asr/wenet_compute_cer.py` | Completed in this cleanup pass after hash and smoke checks. |
| `docs/agents/model_tool_agent/playbooks/env_pixi_or_conda.md` | `env_conda.md` plus `env_pixi.md` | Completed in this cleanup pass. |

## Verification Commands

Use these commands before proposing a deletion batch:

```bash
rg -n "src/sure_eval/agent/AGENTS|src/sure_eval/models/AGENTS|src/sure_eval/models/task_playbooks|templates/|docs/contracts|docs/playbooks|docs/policies|docs/specs|evaluation.metrics|evaluation.wenet_compute_cer|env_pixi_or_conda" .
PYTHONPATH=src python -m pytest tests/test_evaluator_metrics.py tests/test_evaluation_task_modules.py -q
```
