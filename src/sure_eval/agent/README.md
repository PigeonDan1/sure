# SURE-EVAL Main Flow Agent

This directory contains the **Harness-First Main Flow Agent Workflow** for orchestration across evaluation tasks.

Unlike the tool onboarding workflow under `src/sure_eval/models/`, the main flow agent does not redesign tool integration. It routes work across:

- user intent understanding
- task classification
- dataset scope decision
- deterministic script execution
- result assessment

---

## 🤖 Main Flow Agent Workflow

### How It Works

1. **Initial Prompt** → Defines the main flow agent role and routing rules
2. **MAIN_FLOW_INPUT** → Describes the current user goal and working context
3. **Structured Execution** → Agent follows the routing file and unit contracts
4. **Artifact Generation** → Agent emits structured run files for audit

---

## Usage

### Step 1: Initial Prompt

Use this as the system prompt for your AI agent:

```text
cd /path/to/sure-eval
你现在扮演 SURE-EVAL 的主流程执行代理。你的任务不是重新实现 tool onboarding，也不是自由式探索，而是严格按照仓库中定义的 harness-first 主流程规范，完成一次 evaluation orchestration。

你必须遵守以下文档：
1. src/sure_eval/agent/AGENTS.md
2. docs/contracts/main_flow_architecture.md
3. docs/contracts/main_agent_spec.md
4. docs/contracts/main_agent_task_unit.md
5. docs/contracts/main_agent_plan_unit.md
6. docs/contracts/main_agent_dataset_unit.md
7. docs/contracts/main_agent_assessment_unit.md
8. docs/policies/constitution.md

你的目标：
- 理解当前请求属于哪类主流程任务
- 判断是否需要调用既有 tool onboarding workflow
- 选择合理的数据集范围
- 优先调用 deterministic scripts，而不是重写它们
- 对本轮执行形成结构化 run report

你必须显式产出以下结构化文件：
- task_classification.json
- main_agent_plan.json
- dataset_decision.json
- script_routing.json
- assessment_report.json
- main_agent_run_report.json

请按当前 workflow 执行：
INTAKE → TASK_CLASSIFICATION_UNIT → PLAN_UNIT → DATASET_SCOPE_UNIT → SCRIPT_ROUTING_UNIT → EXECUTE / WAIT → ASSESSMENT_UNIT → RUN_REPORT_UNIT

工作要求：
- 所有关键决策必须基于 evidence
- 能交给 scripts 的工作必须交给 scripts
- 不允许重写既有 tool onboarding workflow
- 任何 skipped dataset 都必须说明理由
- 任何 stop / block / handoff 决策都必须说明理由

下面是本次输入：

MAIN_FLOW_INPUT
```

### Step 2: MAIN_FLOW_INPUT Format

```yaml
user_goal: evaluate_existing_model|onboarding_then_evaluate|repair_broken_model|audit_results

target:
  model_name: my_model
  model_dir: src/sure_eval/models/my_model
  tool_workflow_ready: true|false|unknown

constraints:
  allow_tool_workflow: true|false
  allowed_tasks: [ASR, S2TT]
  allowed_datasets: null
  blocked_datasets: []
  dry_run: false

evidence:
  readme_path: src/sure_eval/models/my_model/README.md
  config_path: src/sure_eval/models/my_model/config.yaml
  artifacts_dir: src/sure_eval/models/my_model/artifacts
  prior_results: []

runtime_context:
  available_scripts:
    - scripts/prepare_sure_dataset.py
    - scripts/materialize_predictions_template.py
    - scripts/validate_prediction_files.py
    - scripts/evaluate_predictions.py
    - scripts/refresh_report_snapshot.py
```

### Step 3: Run

Send Initial Prompt + MAIN_FLOW_INPUT to your AI agent.

The agent should:

- classify the task
- produce a plan
- choose datasets or explain why not
- route work to deterministic scripts
- assess outputs
- emit a final structured run report

---

## Required Outputs

The main flow agent must produce the following files during a run:

| File | Purpose |
|------|---------|
| `task_classification.json` | Task type decision |
| `main_agent_plan.json` | Top-level execution plan |
| `dataset_decision.json` | Selected and skipped datasets |
| `script_routing.json` | Script execution sequence |
| `assessment_report.json` | Result interpretation |
| `main_agent_run_report.json` | Final run summary |

Templates are located under [`templates/`](/cpfs/user/jingpeng/workspace/sure-eval/templates).

---

## Routing Reminder

The main flow agent should prefer these scripts as its deterministic execution surface:

- [prepare_sure_dataset.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/prepare_sure_dataset.py)
- [materialize_predictions_template.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/materialize_predictions_template.py)
- [validate_prediction_files.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/validate_prediction_files.py)
- [evaluate_predictions.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/evaluate_predictions.py)
- [refresh_report_snapshot.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/refresh_report_snapshot.py)

The tool onboarding subsystem remains separate and mature. The main flow agent may invoke it, but should not redesign it.
