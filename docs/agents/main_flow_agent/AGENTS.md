# SURE-EVAL 主流程 Agent Harness 规范

**版本**: v1.0  
**目标**: 将主流程 Agent 定义为一个可路由、可分单元执行、可结构化评估的 orchestration harness  
**范围**: 主流程 planning / tool readiness routing / dataset scope / script routing / result assessment

---

## 1. 目标与范围

> **Architecture Contract**: 主流程 Agent 的全局边界由 [`contracts/main_flow_architecture.md`](contracts/main_flow_architecture.md) 定义。  
> **Agent Spec**: 主流程 Agent 的职责细节由 [`contracts/main_agent_spec.md`](contracts/main_agent_spec.md) 定义。

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
- **脚本路径约束**：SCRIPT_ROUTING_UNIT 只能引用 `scripts/` 目录下的脚本，`demo/`、`examples/`、`tests/` 下的脚本禁止作为 Agent Flow 的执行路径
- **步骤类型白名单**：SCRIPT_ROUTING_UNIT 的 `step.name` 必须在 Allowed Step Types 白名单中
- **不重写 tool workflow**
- **执行面隔离原则**：`EXECUTION_SURFACE_UNIT` 必须从 `docs/agents/main_flow_agent/templates/` 目录的模板生成（如 `docs/agents/main_flow_agent/templates/run_single_model.sh`），禁止引用或复制任何已有 `eval_runs/` 目录中的脚本、预测文件或报告。每个 evaluation run 是完全独立的执行单元， prior runs 的历史产物对新 run 无参考价值。
  - 结构化输出 `execution_surface.json` 必须包含 `source_provenance` 审计字段，明确声明使用的模板路径。
  - `EXECUTION_READINESS_UNIT` 必须通过 `scripts/check_execution_surface_compliance.py` 验证：声明的模板是否存在于 `docs/agents/main_flow_agent/templates/` 目录下，且与用户指定的模板一致。未通过则 `execution_ready` 强制为 `false`。

---

## 2.5 主流程 Agent System Prompt 绝对约束

以下约束必须作为主流程 Agent 的 system prompt 的一部分注入，不可被绕过或选择性遵守：

```
[SYSTEM_CONSTRAINT: EXECUTION_SURFACE_ISOLATION]
When materializing the execution surface (run_evaluation.sh):
1. ALLOWED_TEMPLATES_ROOT: "docs/agents/main_flow_agent/templates/"
   - The generated script MUST be derived ONLY from the user-specified template
     under ALLOWED_TEMPLATES_ROOT.
   - You MUST NOT use any template outside ALLOWED_TEMPLATES_ROOT.
2. TEMPLATE_DECLARATION:
   - execution_surface.json -> source_provenance.template_file MUST contain the
     exact path of the template used.
   - If the declared template is not under docs/agents/main_flow_agent/templates/, the run is INVALID.
3. SELF_VERIFICATION:
   - Before declaring execution_surface materialized, confirm the template path
     is correct and exists.
   - If you are unsure, you MUST declare execution_ready=false.

[SYSTEM_CONSTRAINT: VC_SUBMIT_MANDATORY]
When the model has a Docker image and the Volcano (vc) CLI is installed:
1. VC_AVAILABILITY_CHECK:
   - The agent MUST run `which vc` AND `vc info` before attempting ANY local
     execution (including local Docker).
   - If `which vc` returns a path AND `vc info` returns cluster partition data,
     `vc submit` is CONSIDERED AVAILABLE and is the ONLY permitted execution path.
   - `vc --version` or other non-essential subcommands MUST NOT be used to
     determine vc availability.
2. LOCAL_EXECUTION_PROHIBITED:
   - Local `bash run_evaluation.sh`, local `docker run`, or any other non-vc
     execution is STRICTLY PROHIBITED when vc is available.
   - Any deviation MUST be recorded in execution_surface.json →
     source_provenance.isolation_compliance.template_parameters_deviated with
     reason "local_fallback_instead_of_vc_submit", and the run MUST be marked
     INVALID.
3. FALLBACK_CONDITIONS:
   - Local execution is ONLY permitted when BOTH conditions are met:
     a) `which vc` fails (CLI not installed), OR
     b) `vc info` definitively fails (no cluster connectivity).
   - If vc is available but `vc submit` itself fails (e.g., no valid partition,
     image not found), the agent MUST report the failure and STOP; it MUST NOT
     silently fallback to local execution.
```

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
SMOKE_TEST_UNIT
    ↓
SUBMIT_VC_RUN / WAIT_FOR_TOOL_WORKFLOW
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
| `SMOKE_TEST_UNIT` | bounded smoke test，验证模型能产出非空预测 | `smoke_test_report.json` |
| `SUBMIT_VC_RUN` | 通过 `vc submit` 提交集群任务（替代本地 bash） | `vc_job_id` 记录在 `main_agent_run_report.json` |
| `ASSESSMENT_UNIT` | 解释执行结果，异常时暂停请求用户确认 | `assessment_report.json` |
| `RUN_REPORT_UNIT` | 汇总整轮 run，用户确认后才持久化 | `main_agent_run_report.json` |

---

## 6. 子单元文档索引

| 子单元 | 文档 |
|--------|------|
| `TASK_CLASSIFICATION_UNIT` | [`contracts/main_agent_task_unit.md`](contracts/main_agent_task_unit.md) |
| `TOOL_READINESS_AND_ROUTING_UNIT` | [`contracts/main_agent_tool_readiness_unit.md`](contracts/main_agent_tool_readiness_unit.md) |
| `PLAN_UNIT` | [`contracts/main_agent_plan_unit.md`](contracts/main_agent_plan_unit.md) |
| `DATASET_SCOPE_UNIT` | [`contracts/main_agent_dataset_unit.md`](contracts/main_agent_dataset_unit.md) |
| `SCRIPT_ROUTING_UNIT` | [`contracts/main_agent_script_routing_unit.md`](contracts/main_agent_script_routing_unit.md) |
| `EXECUTION_SURFACE_UNIT` | [`contracts/main_agent_execution_surface_unit.md`](contracts/main_agent_execution_surface_unit.md) |
| `EXECUTION_READINESS_UNIT` | [`contracts/main_agent_execution_readiness_unit.md`](contracts/main_agent_execution_readiness_unit.md) |
| `SMOKE_TEST_UNIT` | （由 EXECUTION_READINESS_UNIT 扩展，或独立文档 `main_agent_smoke_test_unit.md`） |
| `wait_for_predictions` contract | [`contracts/prediction_generation_contract.md`](contracts/prediction_generation_contract.md) |
| `ASSESSMENT_UNIT` | [`contracts/main_agent_assessment_unit.md`](contracts/main_agent_assessment_unit.md) |
| `RUN_REPORT_UNIT` | [`contracts/main_agent_run_report_unit.md`](contracts/main_agent_run_report_unit.md) |

---

## 7. 输出模板索引

| 文件 | 模板 |
|------|------|
| `task_classification.json` | [`templates/main_agent_task_classification.json`](../templates/main_agent_task_classification.json) |
| `tool_readiness_routing.json` | [`templates/main_agent_tool_readiness_routing.json`](../templates/main_agent_tool_readiness_routing.json) |
| `main_agent_plan.json` | [`templates/main_agent_plan.json`](../templates/main_agent_plan.json) |
| `dataset_decision.json` | [`templates/main_agent_dataset_decision.json`](../templates/main_agent_dataset_decision.json) |
| `script_routing.json` | [`templates/main_agent_script_routing.json`](../templates/main_agent_script_routing.json) |
| `execution_surface.json` | [`templates/main_agent_execution_surface.json`](../templates/main_agent_execution_surface.json) |
| `run_evaluation.sh` | [`templates/run_single_model.sh`](templates/run_single_model.sh) |
| `execution_readiness_report.json` | [`templates/main_agent_execution_readiness_report.json`](../templates/main_agent_execution_readiness_report.json) |
| `assessment_report.json` | [`templates/main_agent_assessment_report.json`](../templates/main_agent_assessment_report.json) |
| `main_agent_run_report.json` | [`templates/main_agent_run_report.json`](../templates/main_agent_run_report.json) |
| `model_eval_manifest.json` | [`templates/model_eval_manifest.json`](../templates/model_eval_manifest.json) |

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
- `source_provenance`
  - `template_file`: 使用的模板路径
  - `template_sha256`: 模板文件的 SHA-256
  - `files_read_during_generation`: 生成过程中读取的所有文件路径列表
  - `isolation_compliance`:
    - `eval_runs_referenced`: bool
    - `prior_run_scripts_copied`: bool
    - `template_parameters_deviated`: []（如有偏离，列出具体偏离项）
    - `deviation_approved_by_user`: bool
- `resolved_inputs`
- `expected_outputs`
- `resume_supported`（是否声明了 `--resume` 或等价机制）

### 8.7 EXECUTION_READINESS_UNIT

**目标**:
- 在正式后台执行前验证 shell / 执行入口是否已经过 bounded smoke test
- bounded smoke test **是阻塞条件**：未通过则不允许进入 EXECUTE
- 检查 resume 完整性（如已有部分预测文件，确认 `--resume` 可正确衔接）
- **执行面隔离审计**：必须通过 `scripts/check_execution_surface_compliance.py` 验证声明的模板是否存在于 `docs/agents/main_flow_agent/templates/` 目录下且与用户指定的一致
- 避免用户最后一键运行时才遇到运行期问题

**最小输出**:
- `execution_ready`
- `status`
- `validation_mode`
- `validated_shell_entrypoint`
- `smoke_test_command`
- `smoke_test_passed`
- `resume_integrity_check`
- `isolation_audit`
  - `audit_passed`: bool
  - `audit_tool`: `scripts/check_execution_surface_compliance.py`
  - `checks`:
    - `template_source`
    - `source_provenance`
- `blocking_issues`
- `next_action`

### 8.8 SUBMIT_VC_RUN

**目标**:
- 将已验证的 `run_evaluation.sh` 通过 `vc submit` 提交到 Volcano 集群执行，替代本地 `bash run_evaluation.sh`
- 自动修复容器内 `.venv` symlink 问题
- 记录集群 job_id，建立本地 run 与集群任务的关联

**前置条件**:
- `execution_surface.json` 已生成且 `materialized=true`
- `execution_readiness_report.json` 中 `execution_ready=true`
- `smoke_test_passed=true`（bounded smoke test 通过是强制前置）
- `local_bash_executed=false` — 如果检测到本地已执行过 `bash run_evaluation.sh`
  或等价的本地 Docker 运行，当前 run 必须标记为 **INVALID**，需清理产物后重新
  通过 `vc submit` 提交。

**vc 可用性判定 SOP**:
| 检查命令 | 成功标准 | 含义 |
|----------|----------|------|
| `which vc` | 返回路径 | vc CLI 已安装 |
| `vc info` | 返回 partition 表格 | vc 已认证且集群可达 |
| `vc --version` | 任意输出 | **不用于判定可用性** |

**结论规则**：
- `which vc` 成功 + `vc info` 成功 → **vc submit 为强制路径**
- `which vc` 失败 → 本地执行许可
- `which vc` 成功但 `vc info` 失败 → 尝试 `vc submit` 一次，若提交失败则停止执行并报告错误，禁止自动 fallback 到本地

**执行步骤**:
1. **定位 execution_surface.json**
   ```
   {model_dir}/eval_runs/{run_id}/execution_surface.json
   ```
2. **提交任务**（Agent 二选一）：
   - **方式 A（CLI）**：
     ```bash
     sure-eval submit-run <model_name> <run_id>
     ```
   - **方式 B（脚本）**：
     ```bash
     python src/sure_eval/agent/trigger_vc.py \
       src/sure_eval/models/<model_name>/eval_runs/<run_id>/execution_surface.json
     ```
3. **命令内部自动完成的工作**（由 `vc_submitter.py` 处理，Agent 无需手动干预）：
   - 镜像选择：扫描 `docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model_name>:*`，取版本号最新的 tag
   - GPU 分区选择：解析 `vc info -u`（权限）+ `vc info`（空闲），按优先级 `5090 > 4090 > 3090 > A10 > V100 > 2080ti` 选最佳可用分区
   - 内存估算：读取 `model.spec.yaml` / `config.yaml` 的 `resources.memory_gb` 和 `weights.size_gb`，公式 `max(memory_gb, weight_size * 2.5 + 4)`， clamp 到 `[8, 64]`
   - `.venv` 修复：`--cmd` 开头自动插入 `ln -sfn /opt/{model_name}_venv {repo}/src/sure_eval/models/{model_name}/.venv`
   - 环境变量注入：`REPO_ROOT=/workspace/sure-eval`、`PYTHON_BIN=/opt/{model_name}_venv/bin/python`
   - Volume mount：`-v /mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox:/workspace/sure-eval`
4. **记录输出**
   - `vc submit` 返回的 `job_id`（如 `job-177978646130336471391-yixuan-wang`）
   - 将 `job_id` 写入 `main_agent_run_report.json` 的 `vc_job_id` 字段
5. **日志追踪**
   - 向用户输出查看日志的命令：
     ```bash
     vc logs -t {job_id}-master-0
     vc logs -t {job_id}-master-0 -f   # 实时追踪
     ```

**失败处理**:
- 若 `vc submit` 返回错误（如镜像不存在、分区无权限），停止执行，记录错误到 `main_agent_run_report.json` 的 `notes`
- 若提交成功但任务很快失败（EndTime 接近 StartTime），Agent 应执行 `vc logs -t {job_id}-master-0` 获取错误日志，进入 `ASSESSMENT_UNIT` 分析

**结果回收**:
- 由于使用了 `-v` volume mount，容器内写入 `/workspace/sure-eval/...` 的文件会**实时同步回宿主机**
- Agent 无需额外拷贝，评估完成后直接在本地的 `{run_dir}/` 下读取：
  - `predictions/*.txt`
  - `evaluation_payload.json`
  - `run.log`

**最小输出**:
- `vc_job_id`: 集群任务 ID
- `submit_time`: 提交时间
- `partition`: 实际使用的 GPU 分区
- `image`: 实际使用的 Docker 镜像
- `estimated_memory_gb`: 申请的内存大小

**vc 可用性判定 SOP**:
| 检查命令 | 成功标准 | 含义 |
|----------|----------|------|
| `which vc` | 返回路径 | vc CLI 已安装 |
| `vc info` | 返回 partition 表格 | vc 已认证且集群可达 |
| `vc --version` | 任意输出 | **不用于判定可用性** |

**结论规则**：
- `which vc` 成功 + `vc info` 成功 → **vc submit 为强制路径**
- `which vc` 失败 → 本地执行许可
- `which vc` 成功但 `vc info` 失败 → 尝试 `vc submit` 一次，若提交失败则停止执行并报告错误，禁止自动 fallback 到本地

---

### 8.9 ASSESSMENT_UNIT

**目标**:
- 判断本轮执行是成功、部分成功还是阻塞
- 对异常指标（如 WER/CER > 50%、Accuracy < 20%）触发暂停，请求用户确认后再继续
- 不允许对异常结果静默通过

**最小输出**:
- `status`
- `evidence`
- `anomaly_detected`
- `user_confirmed`
- `next_action`

### 8.9 RUN_REPORT_UNIT

**目标**:
- 汇总整轮 run 的结构化结论
- 产出一份可以与 `model_eval_manifest.json` 对齐的终态报告
- 报告生成前必须先 preview，经用户确认（y/n）后才持久化；用户拒绝时标记为 cancelled

**最小输出**:
- 本轮任务类型
- 数据集范围
- 实际执行步骤
- 最终状态
- 下一步建议
- 上游 artifact 索引
- `model_eval_manifest.json` 的路径
- `report_persisted`（是否已写入文件）
- `execution_path_actual`: 实际使用的执行路径 (`vc_submit` / `local_bash` / `local_docker`)
- `execution_path_declared`: harness 中声明的期望路径
- `vc_job_id`: 若通过 vc submit 执行，记录集群 job_id
- `local_fallback_reason`: 若实际路径为 local，必须填写 fallback 原因
- `fallback_approved`: bool，若发生 fallback 是否经用户/规范明确批准

---

## 9. 与 deterministic scripts 的连接

主流程 Agent 的 routing 单元必须优先落到以下脚本接口：

- [prepare_sure_dataset.py](../../../scripts/prepare_sure_dataset.py)
- [materialize_predictions_template.py](../../../scripts/materialize_predictions_template.py)
- [generate_predictions_via_server.py](../../../scripts/generate_predictions_via_server.py)
  - 支持 `--resume`，默认开启；`NO_RESUME=1` 可禁用
- [validate_prediction_files.py](../../../scripts/validate_prediction_files.py)
- [evaluate_predictions.py](../../../scripts/evaluate_predictions.py)
- [refresh_report_snapshot.py](../../../scripts/refresh_report_snapshot.py)
- [check_execution_surface_compliance.py](../../../scripts/check_execution_surface_compliance.py)
  - `EXECUTION_READINESS_UNIT` 的强制前置检查
  - 验证执行面声明的模板是否存在于 `docs/agents/main_flow_agent/templates/` 目录下
- **vc submit 触发器**（替代本地 `bash run_evaluation.sh`）
  - CLI: `sure-eval submit-run <model_name> <run_id>`
  - 脚本: `python src/sure_eval/agent/trigger_vc.py <execution_surface.json>`
  - 自动完成：镜像选择、GPU 分区选择、内存估算、容器内 .venv symlink 修复

### `vc` 可用性判定 SOP（必须遵守）

| 检查命令 | 成功标准 | 含义 |
|----------|----------|------|
| `which vc` | 返回路径 | vc CLI 已安装 |
| `vc info` | 返回 partition 表格 | vc 已认证且集群可达 |
| `vc --version` | 任意输出 | **不用于判定可用性** |

**结论规则**：
- `which vc` 成功 + `vc info` 成功 → **vc submit 为强制路径**
- `which vc` 失败 → 本地执行许可
- `which vc` 成功但 `vc info` 失败 → 尝试 `vc submit` 一次，若提交失败则停止执行并报告错误，禁止自动 fallback 到本地

主流程 Agent 不应在 routing 层跳过这些脚本去直接构造中间格式。

但在进入这些脚本前，必须先经过 `TOOL_READINESS_AND_ROUTING_UNIT`，优先判断：

- 当前模型是否已经是 `server_ready`
- 是否应先做 server-first smoke test
- 是否应转入既有 tool workflow，而不是继续主流程评测

如果最终交付物是单模型单数据集 shell，则在正式后台运行前还必须经过
`EXECUTION_READINESS_UNIT`，验证：

- shell 语法
- bounded smoke mode
- 执行面隔离合规（`scripts/check_execution_surface_compliance.py`）
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
