# SURE-EVAL Main Flow Agent

This directory contains the **Harness-First Main Flow Agent Workflow** for orchestration across evaluation tasks.

Unlike the tool onboarding workflow under `src/sure_eval/models/`, the main flow agent does not redesign tool integration. It routes work across:

- user intent understanding
- task classification
- tool readiness and routing
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
5. docs/contracts/main_agent_tool_readiness_unit.md
6. docs/contracts/main_agent_plan_unit.md
7. docs/contracts/main_agent_dataset_unit.md
8. docs/contracts/main_agent_script_routing_unit.md
9. docs/contracts/main_agent_assessment_unit.md
10. docs/contracts/main_agent_run_report_unit.md
11. docs/policies/constitution.md

你的目标：
- 理解当前请求属于哪类主流程任务
- 先判断当前模型是否可以 direct server use，还是应先做 server smoke test，还是应转 tool workflow
- 选择合理的数据集范围
- 优先调用 deterministic scripts，而不是重写它们
- 对本轮执行形成结构化 run report

你必须显式产出以下结构化文件：
- task_classification.json
- tool_readiness_routing.json
- main_agent_plan.json
- dataset_decision.json
- script_routing.json
- assessment_report.json
- main_agent_run_report.json

请按当前 workflow 执行：
INTAKE → TASK_CLASSIFICATION_UNIT → TOOL_READINESS_AND_ROUTING_UNIT → PLAN_UNIT → DATASET_SCOPE_UNIT → SCRIPT_ROUTING_UNIT → EXECUTE / WAIT → ASSESSMENT_UNIT → RUN_REPORT_UNIT

工作要求：
- 所有关键决策必须基于 evidence
- 能交给 scripts 的工作必须交给 scripts
- 不允许重写既有 tool onboarding workflow
- 如果模型声明了 server/tool 路径，必须优先做 server-first 判定，不允许一开始就掉进 wrapper-level 或 dependency-level 修补
- 如果 `TOOL_READINESS_AND_ROUTING_UNIT` 判断为 `tool_broken_needs_repair` 或 `not_tool_ready`，应停止主流程评测并 handoff
- 任何 skipped dataset 都必须说明理由
- 任何 stop / block / handoff 决策都必须说明理由
- 每个单元都要优先落结构化文件，而不是只输出自然语言总结

下面是本次输入：

MAIN_FLOW_INPUT
```

### Complete Prompt Template

If you want a single copy-paste block for a coding agent, use this:

```text
cd /cpfs/user/jingpeng/workspace/sure-eval

你现在扮演 SURE-EVAL 的主流程执行代理。你的任务不是重新实现 tool onboarding，也不是自由式探索，而是严格按照仓库中定义的 harness-first 主流程规范，完成一次 evaluation orchestration。

你必须遵守以下文档：
1. src/sure_eval/agent/AGENTS.md
2. docs/contracts/main_flow_architecture.md
3. docs/contracts/main_agent_spec.md
4. docs/contracts/main_agent_task_unit.md
5. docs/contracts/main_agent_tool_readiness_unit.md
6. docs/contracts/main_agent_plan_unit.md
7. docs/contracts/main_agent_dataset_unit.md
8. docs/contracts/main_agent_script_routing_unit.md
9. docs/contracts/main_agent_assessment_unit.md
10. docs/contracts/main_agent_run_report_unit.md
11. docs/policies/constitution.md

你的工作边界：
- 你是主流程 agent，不是 tool onboarding agent
- 你必须先做 TASK_CLASSIFICATION_UNIT
- 然后必须做 TOOL_READINESS_AND_ROUTING_UNIT
- 如果模型已经声明 server/tool 路径，优先 direct server use 或 server smoke test
- 如果 tool 路径坏了，应 handoff 给既有 tool workflow，而不是自己重写 tool integration
- 只有在 tool path 已就绪时，才进入 dataset scope 和 deterministic scripts

你的强制输出文件：
- task_classification.json
- tool_readiness_routing.json
- main_agent_plan.json
- dataset_decision.json
- script_routing.json
- assessment_report.json
- main_agent_run_report.json

你的执行顺序必须是：
INTAKE
→ TASK_CLASSIFICATION_UNIT
→ TOOL_READINESS_AND_ROUTING_UNIT
→ PLAN_UNIT
→ DATASET_SCOPE_UNIT
→ SCRIPT_ROUTING_UNIT
→ EXECUTE / WAIT
→ ASSESSMENT_UNIT
→ RUN_REPORT_UNIT

你的执行原则：
- 所有关键判断必须基于 evidence
- 能交给 deterministic scripts 的工作，必须交给 scripts
- 不允许跳过 TOOL_READINESS_AND_ROUTING_UNIT
- 不允许一开始直接下沉到 wrapper-level / dependency-level 修补
- 任何 skipped dataset、handoff、blocked、stop 都必须说明理由
- 输出必须优先结构化，且与 templates 对齐

当前 deterministic script surface:
- scripts/prepare_sure_dataset.py
- scripts/materialize_predictions_template.py
- scripts/validate_prediction_files.py
- scripts/evaluate_predictions.py
- scripts/refresh_report_snapshot.py

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
  model_spec_path: src/sure_eval/models/my_model/model.spec.yaml
  prior_results: []

runtime_context:
  available_scripts:
    - scripts/prepare_sure_dataset.py
    - scripts/materialize_predictions_template.py
    - scripts/validate_prediction_files.py
    - scripts/evaluate_predictions.py
    - scripts/refresh_report_snapshot.py
  output_dir: /tmp/main_agent_run_xxx
```

### Recommended `MAIN_FLOW_INPUT` Example

For a real existing model evaluation, you can use:

```yaml
MAIN_FLOW_INPUT:
  user_goal: evaluate_existing_model

  target:
    model_name: asr_qwen3
    model_dir: src/sure_eval/models/asr_qwen3
    tool_workflow_ready: true

  constraints:
    allow_tool_workflow: true
    allowed_tasks: [ASR]
    allowed_datasets: null
    blocked_datasets: []
    dry_run: false

  evidence:
    readme_path: src/sure_eval/models/asr_qwen3/README.md
    config_path: src/sure_eval/models/asr_qwen3/config.yaml
    artifacts_dir: src/sure_eval/models/asr_qwen3/artifacts
    model_spec_path: src/sure_eval/models/asr_qwen3/model.spec.yaml
    prior_results: []

  runtime_context:
    available_scripts:
      - scripts/prepare_sure_dataset.py
      - scripts/materialize_predictions_template.py
      - scripts/validate_prediction_files.py
      - scripts/evaluate_predictions.py
      - scripts/refresh_report_snapshot.py
    output_dir: /tmp/main_agent_strict_replay_asr_qwen3_003
```

### Step 3: Run

Send Initial Prompt + MAIN_FLOW_INPUT to your AI agent.

The agent should:

- classify the task
- decide tool readiness and routing
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
| `tool_readiness_routing.json` | Tool readiness and execution-path decision |
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

## Practical Notes

- If the model already declares a server path in `config.yaml`, the agent should prefer a server-first smoke test before any dataset work.
- If that smoke test fails, the correct main-flow action is usually `handoff_to_tool_workflow`.
- A worked real example is documented in [main_agent_qwen3_asr_case.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/main_agent_qwen3_asr_case.md).
