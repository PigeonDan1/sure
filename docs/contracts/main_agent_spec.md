# SURE-EVAL Main Flow Agent Spec

**版本**: v1.0  
**目标**: 定义主流程 Agent 的职责、状态机、输入输出与执行边界  
**范围**: 评测主流程 orchestration，不包含 tool onboarding 子系统重设计

---

## 1. 目标与范围

> **Architecture Contract**: 本文档是 [`./main_flow_architecture.md`](./main_flow_architecture.md) 的细化规范。若两者冲突，以架构契约的角色边界为准。

### 1.1 核心目标

主流程 Agent 的目标不是“自己完成一切”，而是以最小职责完成以下工作：

- **理解任务**: 判断当前请求属于 onboarding、evaluation、repair 还是 audit
- **做出范围决策**: 选择合适的数据集、判定是否需要调用既有 tool workflow
- **推进稳定流程**: 调用 deterministic scripts，而不是重新实现它们
- **输出可审计决策**: 所有关键判断必须可解释、可回溯

### 1.2 设计原则

主流程 Agent 必须遵守以下 harness engineering 原则：

- **边界清晰**: 只做高不确定性决策，不接管低不确定性执行
- **脚本优先**: 能交给 deterministic scripts 的工作，不留给 agent
- **结构化优先**: 输出计划、数据集选择、执行结果时优先结构化
- **可复盘**: 任何停止、缩小范围、跳过数据集的决定都必须有理由
- **子系统尊重**: 既有 tool onboarding workflow 是成熟组件，主流程 Agent 只能调用，不得重构

### 1.3 当前边界

**做**:

- 任务分类
- 模型能力边界判读
- 评测数据集范围选择
- deterministic scripts 调度
- 脚本结果解释与下一步决策

**不做**:

- 不重写 tool onboarding harness
- 不直接实现数据转换逻辑
- 不直接实现 metric 计算
- 不直接实现 report 生成
- 不扩展为 multi-agent swarm

---

## 2. 主状态机

### 2.1 工作流总览

```
INTAKE
    ↓ (理解用户目标与上下文)
CLASSIFY_TASK
    ↓ (判断任务类型)
INSPECT_CONTEXT
    ↓ (读取 README / artifacts / config / history)
PLAN
    ↓ (形成执行计划与数据集范围)
DECIDE_TOOL_PATH
    ↓ (决定是否调用既有 tool workflow)
PREPARE_DATA
    ↓ (准备 canonical datasets)
MATERIALIZE_TEMPLATES
    ↓ (生成 prediction templates)
WAIT_OR_COLLECT_PREDICTIONS
    ↓ (等待/收集 prediction files)
VALIDATE_PREDICTIONS
    ↓ (校验 prediction files)
EVALUATE
    ↓ (计算 metric / RPS)
ASSESS
    ↓ (判断成功 / 部分成功 / 阻塞)
REFRESH_REPORTS
    ↓ (刷新 report snapshot，若适用)
REPORT
    ↓
DONE
```

### 2.2 失败处理状态机

```
FAIL
    ↓
CLASSIFY_FAILURE
    ↓
DECIDE_NEXT_ACTION
    ↓
{ RETRY_SCRIPT | NARROW_SCOPE | INVOKE_TOOL_WORKFLOW | STOP_AND_REPORT }
```

### 2.3 停止条件

以下任一情况出现时，主流程 Agent 可以结束本轮：

- 当前用户目标已完成
- prediction validation 未通过且无合理自动下一步
- evaluation 已完成并产出结构化结果
- tool workflow 尚未完成，继续推进需要切换到其子系统
- 缺少关键证据，必须等待人工决策

---

## 3. 任务类型分类

主流程 Agent 必须在 `CLASSIFY_TASK` 阶段将任务归为以下类型之一：

| 类型 | 说明 |
|------|------|
| `onboarding_then_evaluate` | 新模型接入后立即进入评测 |
| `evaluate_existing_model` | 模型已接入，仅做评测 |
| `repair_broken_model` | 现有模型或评测流程损坏，需要修复 |
| `audit_results` | 对已有结果做复核、解释、对比 |

分类结果必须进入结构化计划输出。

---

## 4. 与既有 Tool Workflow 的关系

### 4.1 主流程 Agent 的权限

主流程 Agent **可以**：

- 判断当前是否需要调用既有 tool onboarding workflow
- 将当前任务移交给该 workflow
- 消费该 workflow 的产物（README / config / artifacts / wrapper）

主流程 Agent **不可以**：

- 修改该 workflow 的设计原则
- 复制该 workflow 的状态机
- 以“轻量模式”重写其 backend / import / infer / contract 流程

### 4.2 何时调用既有 Tool Workflow

满足以下任一条件时，应优先调用既有 tool workflow，而不是继续脚本层评测：

- 目标模型尚未形成可调用 wrapper
- 当前模型没有通过最小 contract 验证
- README / artifacts 表明模型能力边界尚不可靠
- 当前失败本质上是 tool integration 问题，而不是评测问题

---

## 5. 输入规范

### 5.1 主输入

主流程 Agent 的输入来自四类来源：

1. **用户目标**
   - 例如：接入新模型、复跑评测、排查失败、审阅结果

2. **模型上下文**
   - `src/sure_eval/models/<model>/`
   - README
   - config
   - artifacts
   - model.spec.yaml

3. **评测上下文**
   - dataset definitions
   - script outputs
   - prior evaluation records
   - report snapshot

4. **人工限制**
   - 例如：只跑某类任务、不要触碰 tool workflow、只做 dry-run

### 5.2 关键判断输入

主流程 Agent 在选择 dataset scope 时，必须优先使用：

- 模型 README 的明确能力描述
- 已通过的 tool artifacts
- wrapper contract
- 明确的人类指令

不应仅凭 task 名字猜测能力边界。

---

## 6. 输出规范

### 6.1 主流程 Agent 必须产出的结构化结果

每次主流程执行至少应能归纳出以下结构：

```json
{
  "task_type": "evaluate_existing_model",
  "need_tool_workflow": false,
  "selected_datasets": ["aishell1", "librispeech_clean"],
  "skipped_datasets": [
    {
      "dataset": "covost2_en2zh",
      "reason": "model README does not support S2TT"
    }
  ],
  "execution_steps": [
    "prepare_dataset",
    "materialize_templates",
    "validate_predictions",
    "evaluate_predictions"
  ],
  "stop_condition": "stop_after_evaluation_if_results_are_valid"
}
```

### 6.2 必须落到脚本层接口的输出

主流程 Agent 的执行性输出必须转化为以下接口输入：

1. canonical dataset list  
2. prediction template materialization request  
3. prediction validation request  
4. evaluation request  
5. optional report refresh request

---

## 7. Deterministic Script Routing

主流程 Agent 的脚本调用顺序必须优先遵守以下路由：

### 7.1 数据准备

- [prepare_sure_dataset.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/prepare_sure_dataset.py)

### 7.2 预测模板生成

- [materialize_predictions_template.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/materialize_predictions_template.py)

### 7.3 预测文件校验

- [validate_prediction_files.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/validate_prediction_files.py)

### 7.4 正式评分

- [evaluate_predictions.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/evaluate_predictions.py)

### 7.5 报表刷新

- [refresh_report_snapshot.py](/cpfs/user/jingpeng/workspace/sure-eval/scripts/refresh_report_snapshot.py)

主流程 Agent 不应绕过这些脚本去直接拼装中间格式。

---

## 8. 核心决策点

### 8.1 是否进入 Tool Workflow

主流程 Agent 必须明确回答：

- 当前模型是否已经 ready-for-evaluation？
- 当前问题是否属于 integration failure？
- 当前是否需要先进入 tool workflow，再回到评测层？

### 8.2 评哪些数据集

主流程 Agent 必须明确输出：

- 哪些 dataset 被选中
- 哪些 dataset 被跳过
- 跳过理由是什么

### 8.3 何时停止

主流程 Agent 必须能判断：

- 什么时候继续跑下一个脚本
- 什么时候停止并等待 prediction files
- 什么时候该结束本轮并汇报

---

## 9. 成功标准

主流程 Agent 的最低成功标准不是“全部自动做完”，而是：

| 项目 | 标准 |
|------|------|
| 任务分类清晰 | 能明确判断 task type |
| 数据集范围可解释 | 每个 selected / skipped dataset 都有理由 |
| 脚本调用路径稳定 | 优先走 deterministic scripts |
| 不侵犯 tool 子系统边界 | 不重写 harness-first workflow |
| 结果可复盘 | 能解释本轮停在何处、为何停止、下一步是什么 |

---

## 10. 当前不做的事

主流程 Agent 当前明确**不做**以下扩展：

- 多 agent swarm orchestration
- 独立 dataset agent
- 独立 metric agent
- 独立 report agent
- 自由式 planner chat team
- agent 直接重写 prediction template / validation / evaluation 逻辑

---

## 11. 推荐实现顺序

主流程 Agent 的实现建议按以下顺序推进：

1. 固化状态机
2. 固化结构化 plan schema
3. 固化 dataset selection 规则
4. 固化 script routing 逻辑
5. 最后再写 system prompt / execution prompt

即：

**先定 spec，再写 prompt**

---

## 12. 附录：一句话定义

主流程 Agent 的一句话定义是：

> **它是 SURE-EVAL 的流程主控与范围决策器，不是 tool specialist，也不是评测脚本替代品。**
