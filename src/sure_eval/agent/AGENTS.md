# SURE-EVAL 主流程 Agent Harness 规范

**版本**: v1.0  
**目标**: 将主流程 Agent 定义为一个可路由、可分单元执行、可结构化评估的 orchestration harness  
**范围**: 主流程 planning / tool readiness routing / dataset scope / script routing / result assessment

---

## 1. 目标与范围

> **Architecture Contract**: 主流程 Agent 的全局边界由 [`../../../docs/contracts/main_flow_architecture.md`](../../../docs/contracts/main_flow_architecture.md) 定义。  
> **Agent Spec**: 主流程 Agent 的职责细节由 [`../../../docs/contracts/main_agent_spec.md`](../../../docs/contracts/main_agent_spec.md) 定义。

本文档是主流程 Agent 的**harness-first 入口文档**。

它的作用不是讨论抽象架构，而是定义：

- 主流程 Agent 的 routing 方式
- 各子单元的职责边界
- 每个子单元的结构化输出文件
- 如何逐单元运行与逐单元评估
- 如何形成最终 run report

---

## 2. 当前设计原则

主流程 Agent 必须遵守以下原则：

- **一个总控 Agent**，不扩展为 swarm
- **routing 先于 prompt 技巧**
- **单元化执行**，每个阶段都应有结构化输出
- **脚本优先**，低不确定性执行必须落到 deterministic scripts
- **不重写 tool workflow**

---

## 3. 主 Routing 文件角色

本文档本身就是主流程 Agent 的 routing 文件。

它定义主流程 Agent 由以下子单元组成：

1. `TASK_CLASSIFICATION_UNIT`
2. `TOOL_READINESS_AND_ROUTING_UNIT`
3. `PLAN_UNIT`
4. `DATASET_SCOPE_UNIT`
5. `SCRIPT_ROUTING_UNIT`
6. `EXECUTION_SURFACE_UNIT`
7. `EXECUTION_READINESS_UNIT`
8. `ASSESSMENT_UNIT`
9. `RUN_REPORT_UNIT`

每个单元：

- 有明确输入
- 有明确输出
- 有明确“不做什么”
- 有对应的结构化文件模板

---

## 4. 主状态机

```
INTAKE
    ↓
TASK_CLASSIFICATION_UNIT
    ↓
TOOL_READINESS_AND_ROUTING_UNIT
    ↓
PLAN_UNIT
    ↓
DATASET_SCOPE_UNIT
    ↓
SCRIPT_ROUTING_UNIT
    ↓
EXECUTION_SURFACE_UNIT
    ↓
EXECUTION_READINESS_UNIT
    ↓
EXECUTE_SCRIPTS / WAIT_FOR_TOOL_WORKFLOW
    ↓
ASSESSMENT_UNIT
    ↓
RUN_REPORT_UNIT
    ↓
DONE
```

---

## 5. 子单元总览

| 单元 | 作用 | 结构化输出 |
|------|------|------------|
| `TASK_CLASSIFICATION_UNIT` | 判断任务类型 | `task_classification.json` |
| `TOOL_READINESS_AND_ROUTING_UNIT` | 判断是否优先 direct server use，或转 tool workflow | `tool_readiness_routing.json` |
| `PLAN_UNIT` | 形成执行计划 | `main_agent_plan.json` |
| `DATASET_SCOPE_UNIT` | 选择 / 跳过数据集 | `dataset_decision.json` |
| `SCRIPT_ROUTING_UNIT` | 形成脚本调用序列 | `script_routing.json` |
| `EXECUTION_SURFACE_UNIT` | 生成最终 shell / command handoff artifact | `execution_surface.json` |
| `EXECUTION_READINESS_UNIT` | 验证 shell / 执行入口是否可安全后台运行 | `execution_readiness_report.json` |
| `ASSESSMENT_UNIT` | 解释执行结果 | `assessment_report.json` |
| `RUN_REPORT_UNIT` | 汇总整轮 run | `main_agent_run_report.json` |

---

## 6. 子单元文档索引

| 子单元 | 文档 |
|--------|------|
| `TASK_CLASSIFICATION_UNIT` | [`../../../docs/contracts/main_agent_task_unit.md`](../../../docs/contracts/main_agent_task_unit.md) |
| `TOOL_READINESS_AND_ROUTING_UNIT` | [`../../../docs/contracts/main_agent_tool_readiness_unit.md`](../../../docs/contracts/main_agent_tool_readiness_unit.md) |
| `PLAN_UNIT` | [`../../../docs/contracts/main_agent_plan_unit.md`](../../../docs/contracts/main_agent_plan_unit.md) |
| `DATASET_SCOPE_UNIT` | [`../../../docs/contracts/main_agent_dataset_unit.md`](../../../docs/contracts/main_agent_dataset_unit.md) |
| `SCRIPT_ROUTING_UNIT` | [`../../../docs/contracts/main_agent_script_routing_unit.md`](../../../docs/contracts/main_agent_script_routing_unit.md) |
| `EXECUTION_SURFACE_UNIT` | [`../../../docs/contracts/main_agent_execution_surface_unit.md`](../../../docs/contracts/main_agent_execution_surface_unit.md) |
| `EXECUTION_READINESS_UNIT` | [`../../../docs/contracts/main_agent_execution_readiness_unit.md`](../../../docs/contracts/main_agent_execution_readiness_unit.md) |
| `wait_for_predictions` contract | [`../../../docs/contracts/prediction_generation_contract.md`](../../../docs/contracts/prediction_generation_contract.md) |
| `ASSESSMENT_UNIT` | [`../../../docs/contracts/main_agent_assessment_unit.md`](../../../docs/contracts/main_agent_assessment_unit.md) |
| `RUN_REPORT_UNIT` | [`../../../docs/contracts/main_agent_run_report_unit.md`](../../../docs/contracts/main_agent_run_report_unit.md) |

---

## 7. 输出模板索引

| 文件 | 模板 |
|------|------|
| `task_classification.json` | [`../../../templates/main_agent_task_classification.json`](../../../templates/main_agent_task_classification.json) |
| `tool_readiness_routing.json` | [`../../../templates/main_agent_tool_readiness_routing.json`](../../../templates/main_agent_tool_readiness_routing.json) |
| `main_agent_plan.json` | [`../../../templates/main_agent_plan.json`](../../../templates/main_agent_plan.json) |
| `dataset_decision.json` | [`../../../templates/main_agent_dataset_decision.json`](../../../templates/main_agent_dataset_decision.json) |
| `script_routing.json` | [`../../../templates/main_agent_script_routing.json`](../../../templates/main_agent_script_routing.json) |
| `execution_surface.json` | [`../../../templates/main_agent_execution_surface.json`](../../../templates/main_agent_execution_surface.json) |
| `execution_readiness_report.json` | [`../../../templates/main_agent_execution_readiness_report.json`](../../../templates/main_agent_execution_readiness_report.json) |
| `assessment_report.json` | [`../../../templates/main_agent_assessment_report.json`](../../../templates/main_agent_assessment_report.json) |
| `main_agent_run_report.json` | [`../../../templates/main_agent_run_report.json`](../../../templates/main_agent_run_report.json) |
| `model_eval_manifest.json` | [`../../../templates/model_eval_manifest.json`](../../../templates/model_eval_manifest.json) |

---

## 8. 单元执行要求

### 8.1 TASK_CLASSIFICATION_UNIT

**目标**:
- 判断当前任务属于 `onboarding_then_evaluate` / `evaluate_existing_model` / `repair_broken_model` / `audit_results`

**最小输出**:
- `task_type`
- `reason`
- `need_tool_workflow`
- `confidence`

### 8.2 TOOL_READINESS_AND_ROUTING_UNIT

**目标**:
- 判断当前模型是否应优先 direct server use
- 判断是否只需做 server smoke test
- 判断何时必须转 tool workflow

**最小输出**:
- `tool_readiness_state`
- `preferred_execution_path`
- `server_smoke_test_required`
- `handoff_to_tool_agent`
- `reason`

### 8.3 PLAN_UNIT

**目标**:
- 形成本轮总体计划

**最小输出**:
- 主要目标
- 预期执行步骤
- stop condition

### 8.4 DATASET_SCOPE_UNIT

**目标**:
- 明确 selected / skipped datasets

**最小输出**:
- `selected_datasets`
- `skipped_datasets`
- 每个 skipped item 的 reason

### 8.5 SCRIPT_ROUTING_UNIT

**目标**:
- 把 agent 的决策转成确定性脚本调用顺序

**最小输出**:
- `steps`
- 每一步对应的 script
- 每一步的输入依赖
- 每一步的输出路径
- 每一步的完成判定条件
- `wait_points`
- `stop_condition`

### 8.6 EXECUTION_SURFACE_UNIT

**目标**:
- 将 routing 决策 materialize 成最终交付面
- 在 shell handoff 模式下生成真实存在的 shell artifact

**最小输出**:
- `execution_surface_type`
- `materialized`
- `entrypoint_path`
- `resolved_inputs`
- `expected_outputs`

### 8.7 EXECUTION_READINESS_UNIT

**目标**:
- 在正式后台执行前验证 shell / 执行入口是否已经过 bounded smoke test
- 避免用户最后一键运行时才遇到运行期问题

**最小输出**:
- `execution_ready`
- `status`
- `validation_mode`
- `validated_shell_entrypoint`
- `smoke_test_command`
- `blocking_issues`
- `next_action`

### 8.8 ASSESSMENT_UNIT

**目标**:
- 判断本轮执行是成功、部分成功还是阻塞

**最小输出**:
- `status`
- `evidence`
- `next_action`

### 8.9 RUN_REPORT_UNIT

**目标**:
- 汇总整轮 run 的结构化结论
- 产出一份可以与 `model_eval_manifest.json` 对齐的终态报告

**最小输出**:
- 本轮任务类型
- 数据集范围
- 实际执行步骤
- 最终状态
- 下一步建议
- 上游 artifact 索引
- `model_eval_manifest.json` 的路径

---

## 9. 与 deterministic scripts 的连接

主流程 Agent 的 routing 单元必须优先落到以下脚本接口：

- [prepare_sure_dataset.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/prepare_sure_dataset.py)
- [materialize_predictions_template.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/materialize_predictions_template.py)
- [validate_prediction_files.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/validate_prediction_files.py)
- [evaluate_predictions.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/evaluate_predictions.py)
- [refresh_report_snapshot.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/refresh_report_snapshot.py)

主流程 Agent 不应在 routing 层跳过这些脚本去直接构造中间格式。

但在进入这些脚本前，必须先经过 `TOOL_READINESS_AND_ROUTING_UNIT`，优先判断：

- 当前模型是否已经是 `server_ready`
- 是否应先做 server-first smoke test
- 是否应转入既有 tool workflow，而不是继续主流程评测

如果最终交付物是单模型单数据集 shell，则在正式后台运行前还必须经过
`EXECUTION_READINESS_UNIT`，验证：

- shell 语法
- bounded smoke mode
- 预测生成是否能在当前环境起步
- shell 是否会产出约定的 run evidence

---

## 10. 成功标准

主流程 Agent harness 的最低成功标准：

| 检查项 | 标准 |
|--------|------|
| routing 清晰 | 每轮 run 可映射到固定单元 |
| 单元可审计 | 每个单元都有结构化输出 |
| 脚本边界稳定 | script routing 不漂移 |
| tool 边界稳定 | 不侵入既有 tool workflow |
| 最终可汇总 | 能生成统一 run report |

---

## 11. 当前不做的事

**不实现**:

- 多 agent 分工对话
- dataset agent / metric agent / report agent 拆分
- 主流程 Agent 自己执行复杂 tool integration
- 无结构化输出的自由式执行

**允许**:

- 先以文档 + 模板方式实现 harness
- 后续再逐个单元落代码

---

## 12. 推荐实现顺序

1. 固化 routing file
2. 固化子单元文档
3. 固化输出模板
4. 再把主流程 Agent 的 plan schema / prompt 实现出来

即：

**先搭 harness，再写 agent 行为**
