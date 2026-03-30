# SURE-EVAL 模型接入 Harness 规范 (第一阶段)

**版本**: v1.0  
**目标**: 将语音模型仓库接入为**可复现的本地推理单元**  
**范围**: 第一阶段聚焦环境适配、本地格式化管理、最小推理调用

---

## 1. 目标与范围

> **Constitution**: 所有 harness 组件与 agent 必须遵守项目级 constitution 定义的高层不变原则，详见 [`../../../docs/policies/constitution.md`](../../../docs/policies/constitution.md)。

### 1.1 核心目标

本文档定义第一阶段模型接入的标准 workflow，确保：

- **环境可重建**: 通过规范化的 backend 选择和依赖管理
- **推理可复现**: 通过统一的验证契约和工件保存
- **过程可审计**: 通过结构化的 verdict 和 artifact 记录

### 1.2 支持范围

| 类型 | 支持状态 | 说明 |
|------|----------|------|
| 本地模型 | ✅ 主要目标 | 权重在本地下载和加载 |
| API 模型 | ✅ 支持 | 通过远程服务调用 |
| 完整 Benchmark | ❌ 不做 | 第一阶段只做最小推理验证 |
| 高度自动修复 | ❌ 不做 | 失败时人工介入诊断 |

### 1.3 第一阶段边界

**做**:
- 环境后端选择 (uv/pixi/docker/api)
- runtime identity 收敛 (display identity / runtime load identity / local dir name)
- 最小验证 (import/load/infer/contract)
- Wrapper 骨架生成
- 工件归档 (spec/verdict/log)

**不做**:
- 完整 multi-agent team chat
- 自由式 planner agent
- 大规模 benchmark/leaderboard
- 复杂自动修复循环

---

## 2. 第一阶段 Workflow 总览

### 2.1 主状态机

```
DISCOVER
    ↓ (收集 repo 信息)
CLASSIFY
    ↓ (判断模型类型)
PLAN
    ↓ (生成 spec, 选择 backend)
VALIDATE_SPEC
    ↓ (验证 spec 完整性与证据充分性)
BUILD_ENV
    ↓ (构建隔离环境)
FETCH_WEIGHTS
    ↓ (获取/验证权重)
VALIDATE_IMPORT
    ↓ (验证可导入)
VALIDATE_LOAD
    ↓ (验证可加载)
VALIDATE_INFER
    ↓ (验证可推理)
VALIDATE_CONTRACT
    ↓ (验证输出满足 io_contract)
GENERATE_WRAPPER
    ↓ (生成统一 wrapper)
SAVE_ARTIFACTS
    ↓ (保存所有工件)
DONE
```

### 2.2 失败处理状态机

```
FAIL
    ↓
DIAGNOSE (Evaluator Agent 分类失败)
    ↓
REPLAN (Builder Agent 建议修复)
    ↓
RETRY_FROM_CHECKPOINT (回到失败前状态)
```

**最大重试次数**: 3 次，超过则标记为 FAILED 并退出

---

## 3. 角色分工

第一阶段定义三个角色，明确各自职责边界。

### 3.1 Harness Controller

**性质**: 固定流程主控，非 LLM Agent

**职责**:
- 推进状态机执行
- 调用 shell 命令并捕获日志
- 保存工件到指定路径
- 判定验证结果 (PASS/FAIL)
- 在 BUILD_ENV 前检查 runtime identity、preflight 与 build plan 是否已收敛

**不介入**:
- 不决定 backend 选择
- 不诊断失败原因
- 不生成 wrapper 代码

### 3.2 Builder Agent

**介入节点**:
- **PLAN**: 推荐 backend 选择
- **BUILD_ENV**: 提供构建策略建议
- **GENERATE_WRAPPER**: 识别推理入口候选

**输入**:
- repo 扫描结果
- 依赖文件 (requirements.txt, environment.yml 等)
- 失败日志

**输出**:
- backend_choice.json
- build_plan.json
- wrapper_skeleton.py

**约束**:
- 只提供建议，不直接执行
- 所有建议必须有理由记录

### 3.3 Evaluator Agent

**介入节点**:
- **DIAGNOSE**: 解释测试日志
- **FAIL**: 分类失败类型
- **REPLAN**: 建议 retry 策略

**输入**:
- validation.log
- build.log
- failure taxonomy

**输出**:
- failure_classification.json
- retry_recommendation.json

**约束**:
- 必须引用 failure_taxonomy 中的标准类别
- 必须评估 retryable 可能性

### 3.4 第一阶段 Agent 边界

**不允许 Agent 直接替代**:
- 主流程状态推进
- shell 执行与日志保存
- 工件归档
- contract test 判定

---

## 4. 标准输入输出

### 4.1 工件分类

每次模型接入必须产生的工件按重要性分为三类：

#### Required Artifacts (每次必须)

| 工件 | 路径 | 说明 |
|------|------|------|
| `model.spec.yaml` | `src/sure_eval/models/{model}/model.spec.yaml` | 模型规范 |
| `backend_choice.json` | `src/sure_eval/models/{model}/artifacts/backend_choice.json` | 后端选择记录 |
| `build.log` | `src/sure_eval/models/{model}/artifacts/build.log` | 构建日志 |
| `validation.log` | `src/sure_eval/models/{model}/artifacts/validation.log` | 验证日志 |
| `verdict.json` | `src/sure_eval/models/{model}/artifacts/verdict.json` | 最终判定 |
| `wrapper` | `src/sure_eval/models/{model}/model.py`, `src/sure_eval/models/{model}/server.py`, `src/sure_eval/models/{model}/__init__.py` | 模型 wrapper 文件集 |
| `artifact_manifest.json` | `src/sure_eval/models/{model}/artifacts/artifact_manifest.json` | 工件清单 |

#### Conditional Artifacts (满足条件时必须有)

| 工件 | 路径 | 条件 |
|------|------|------|
| `spec_validation.json` | `artifacts/spec_validation.json` | VALIDATE_SPEC 执行 |
| `preflight_summary.json` | `artifacts/preflight_summary.json` | preflight 执行 |
| `weights_manifest.json` | `artifacts/weights_manifest.json` | weights.required == true |
| `failure_classification.json` | `artifacts/failure_classification.json` | DIAGNOSE 执行 |
| `retry_recommendation.json` | `artifacts/retry_recommendation.json` | REPLAN 执行 |
| `escalation.json` | `artifacts/escalation.json` | 人工升级触发 |
| `patch_report.json` | `artifacts/patch_report.json` | upstream/config patch 应用 |
| `uv.lock` | `uv.lock` | backend == 'uv' |
| `pixi.lock` | `pixi.lock` | backend == 'pixi' |
| `Dockerfile` | `Dockerfile` | backend == 'docker' |
| `.devcontainer/devcontainer.json` | `.devcontainer/devcontainer.json` | backend == 'docker' |

#### Optional Artifacts (可选)

| 工件 | 路径 | 说明 |
|------|------|------|
| `performance_notes.md` | `artifacts/performance_notes.md` | 性能说明 |
| `benchmark_preview.json` | `artifacts/benchmark_preview.json` | benchmark 预览 |
| `wrapper_notes.md` | `artifacts/wrapper_notes.md` | wrapper 实现备注 |
| `diagnostic_outputs/` | `artifacts/diagnostic_outputs/` | 额外诊断输出 |

### 4.2 模板位置

```
templates/
├── model.spec.yaml          # 模型规范模板
├── verdict.json             # 判定结果模板
└── artifact_manifest.json   # 工件清单模板
```

### 4.3 子文档索引

| 主题 | 文档路径 |
|------|----------|
| UV 环境策略 | `docs/playbooks/env_uv.md` |
| Pixi/Conda 环境策略 | `docs/playbooks/env_pixi_or_conda.md` |
| Docker 环境策略 | `docs/playbooks/env_docker.md` |
| API 模型策略 | `docs/playbooks/model_api.md` |
| 失败分类体系 | `docs/playbooks/failure_taxonomy.md` |
| Model Spec 规范 | `docs/specs/model_spec_template.md` |
| 验证契约 | `docs/contracts/minimal_validation.md` |

---

## 5. Backend Routing 规则

第一阶段采用 rule-based backend 选择，每次选择必须记录理由。

### 5.1 决策规则

```
1. 如果是 API-only 模型 
   → api backend
   
2. 如果 repo 有 Dockerfile 且依赖复杂 
   → docker backend
   
3. 如果 repo 有 environment.yml / conda 明确信号 
   → pixi_or_conda backend
   
4. 如果 repo 只有 pyproject.toml / requirements.txt 且主要是纯 Python 
   → uv backend
   
5. 如果涉及 CUDA 编译、自定义 C++/k2/复杂子模块 
   → docker backend 优先
   
6. 如果宿主机污染风险高 
   → docker backend 优先

7. 如果 phase-1 目标是 Python-only minimal callable path，且轻量 backend 可满足
   → 不因 preferred_backend 或 requires_gpu 提示而放弃 uv/pixi
```

### 5.2 记录要求

每次 backend 选择必须生成 `backend_choice.json`:

```json
{
  "chosen_backend": "uv",
  "reason": "pure python dependencies, no cuda compilation needed",
  "evidence": ["pyproject.toml present", "no Dockerfile", "no conda env"],
  "rejected_options": [
    {"backend": "docker", "reason": "overkill for simple model"}
  ]
}
```

---

## 6. 成功标准

第一阶段接入成功的**最低标准**:

| 验证项 | 标准 | 工件 |
|--------|------|------|
| 环境可重建 | 删除 .venv 后可重新构建 | build.log |
| 模型能 import | `from model import X` 无报错 | validation.log |
| 模型能 load | 模型对象可实例化并加载权重 | validation.log |
| 能跑通最小推理 | 给定测试样本输出结果 | validation.log |
| 输出满足契约 | 类型正确、非空、必要字段存在 | validation.log |
| 工件已保存 | spec/verdict/log/wrapper 都存在 | artifact_manifest.json |

---

## 7. 状态定义

### 7.1 DISCOVER

**输入**: repo URL / local repo, 初始模型信息  
**动作**: 扫描 repo 文件结构，收集 README、requirements、environment.yml 等；收敛 runtime identity（display identity / runtime load identity / local dir name）  
**输出**: repo_summary.json

**后续**: 可执行 [预检清单](../../../docs/playbooks/preflight_checklist.md) 生成 `preflight_summary.json`
  - host preflight: GPU/driver、磁盘、docker、系统工具
  - runtime preflight: package manager、Python、TMPDIR/extract 风险、CUDA 初始化风险

### 7.2 CLASSIFY

**动作**: 判断模型类型 (local/api)，判断任务类型，判断环境复杂度，确认 runtime family 与最小 callable path  
**输出**: classification.json

### 7.3 PLAN

**动作**: 
- Builder Agent 选择 backend
- 生成 model.spec.yaml
- 生成 build_plan.json
- 明确 runtime load identity、fixture 选择、CPU fallback / GPU 限制（若适用）

**输出**: 
- model.spec.yaml
- backend_choice.json
- build_plan.json

### 7.4 VALIDATE_SPEC

**动作**: 
- 检查 `model.spec.yaml` 是否完整
- 检查关键字段是否有 evidence 支撑
- 检查 `backend_choice.json` 是否记录冲突与理由
- 检查 `build_plan.json` 是否可执行
- 检查 fixture 是否可用
- 检查 `io_contract` 是否足以支持后续 contract test
- 检查 preflight 结果是否与 backend 选择相容
- 检查 runtime identity 是否已收敛
- 检查大权重 restore/extract 的临时目录策略是否明确
- 检查 GPU 风险是否已记录为 requirement、warning 或 fallback plan

**输出**: 
- spec_validation.json

**失败**: 
- 进入 DIAGNOSE / REPLAN
- **不允许**直接进入 BUILD_ENV

**参考**: [Spec Validation 契约](../../../docs/contracts/spec_validation.md)

### 7.5 BUILD_ENV

**动作**: 使用选定 backend 构建隔离环境，按 build plan 设置 cache/tmp/runtime 路径  
**输出**: environment ready / failure  
**工件**: build.log

### 7.5 FETCH_WEIGHTS

**动作**: 获取或验证权重，记录路径和校验信息  
**输出**: weights ready / failure

### 7.6 VALIDATE_IMPORT

**动作**: 运行 import test  
**输出**: import result  
**失败**: 进入 DIAGNOSE (python_dependency_missing)

### 7.7 VALIDATE_LOAD

**动作**: 运行 load test  
**输出**: load result  
**失败**: 进入 DIAGNOSE (missing_weights / cuda_version_mismatch / config_not_set)

### 7.8 VALIDATE_INFER

**动作**: 运行最小推理测试  
**输出**: infer result  
**失败**: 进入 DIAGNOSE (wrong_entrypoint / runtime_backend_incompatible)

### 7.9 VALIDATE_CONTRACT

**动作**: 
- 验证输出是否满足 `model.spec.yaml.io_contract`
- 检查 required_fields 是否存在
- 检查 nonempty_fields 是否非空
- 检查 primary_field 是否有效
- 检查 JSON serializability（若要求）

**输出**: contract validation result（记录到 `validation.log`）

**失败**: 
- 进入 DIAGNOSE (wrong_entrypoint / wrapper_contract_mismatch / io_contract_incomplete)

**说明**: 
- 第一阶段的 runtime validation 验证对象是 **repo-native entrypoint / minimal callable path**
- wrapper 在 contract 验证通过后生成，用于接入 SURE 框架
- 若 runtime path 已通过，wrapper smoke 仅验证 model-local wrapper，不要求顶层 `sure_eval` 包 extras 完整可用

### 7.10 GENERATE_WRAPPER

**动作**: 生成统一 wrapper skeleton，填写最小调用逻辑  
**输出**: wrapper 文件集
  - `model.py`: 核心模型包装类
  - `server.py`: MCP 服务器实现  
  - `__init__.py`: 包导出声明
  - (可选) `config.yaml`: MCP 工具配置

**参考**: [Wrapper 契约](../../../docs/specs/wrapper_contract.md) 定义各文件职责与最小接口

**约束**:
- wrapper 应复用已验证通过的 repo-native path
- wrapper smoke 若执行，应避免被无关全局依赖阻塞

### 7.11 SAVE_ARTIFACTS

**动作**: 保存 spec snapshot、log、lockfile、verdict、wrapper  
**输出**: artifact_manifest.json

### 7.12 DIAGNOSE / REPLAN

**动作**: 
- Evaluator Agent 结合 failure taxonomy 分类失败
- Builder Agent 给出 retry 建议

**输出**: 
- failure_classification.json
- retry_recommendation.json
- 决定：RETRY_FROM_CHECKPOINT / FAIL_STOP

**约束**: 重试必须遵守 [重试与升级政策](../../../docs/policies/retry_and_escalation.md)，禁止盲重试

---

## 8. 文档索引

### 8.1 Constitution (高层不变原则)

- [项目 Constitution](../../../docs/policies/constitution.md) - 所有组件必须遵守的 10 条核心规则

### 8.2 Policies (决策政策)

- [证据优先级政策](../../../docs/policies/evidence_priority.md) - 多源冲突时的判断依据
- [重试与升级政策](../../../docs/policies/retry_and_escalation.md) - 失败处理与人工介入规则
- [补丁记录政策](../../../docs/policies/patch_recording.md) - 非上游修改的留痕要求

### 8.3 Playbooks (执行手册)

- [预检清单](../../../docs/playbooks/preflight_checklist.md) - BUILD_ENV 前的环境检查
- [UV 环境策略](../../../docs/playbooks/env_uv.md)
- [Pixi/Conda 环境策略](../../../docs/playbooks/env_pixi_or_conda.md)
- [Docker 环境策略](../../../docs/playbooks/env_docker.md)
- [API 模型策略](../../../docs/playbooks/model_api.md)
- [失败分类体系](../../../docs/playbooks/failure_taxonomy.md)

### 8.4 Specs (规范定义)

- [Wrapper 契约](../../../docs/specs/wrapper_contract.md) - model.py/server.py/__init__.py 职责边界
- [Model Spec 模板说明](../../../docs/specs/model_spec_template.md)

### 8.5 Contracts (验证契约)

- [Spec Validation 契约](../../../docs/contracts/spec_validation.md) - spec 前置验证规范
- [Fixture 政策](../../../docs/contracts/fixture_policy.md) - 测试样本规范
- [最小验证契约](../../../docs/contracts/minimal_validation.md)

### 8.6 Registry (模型特异性记录)

- [已知问题注册表](../../../docs/registry/known_issues.md) - 模型级例外与工作区

### 8.7 Templates (模板文件)

- [model.spec.yaml](../../../templates/model.spec.yaml)
- [spec_validation.json](../../../templates/spec_validation.json)
- [verdict.json](../../../templates/verdict.json)
- [artifact_manifest.json](../../../templates/artifact_manifest.json)

---

## 9. 第一阶段约束重申

**不实现**:
- 完整 multi-agent team chat
- 自由式 planner agent
- 大规模 benchmark/leaderboard
- 复杂自动修复循环
- 过于庞大的 spec schema

**允许**:
- Agent + 人工共同完成
- 半自动化流程
- 失败时人工介入

**必须**:
- 流程、状态、失败点透明
- 所有工件可复查
- 环境可重建

---

## 附录: 变更日志

### v1.0 (2024-03-27)

- 重构 AGENTS.md 为 harness-first 入口文档
- 拆分环境策略到独立 playbooks
- 新增 failure taxonomy 分类体系
- 定义标准工件和模板
- 明确三角色分工和状态机
