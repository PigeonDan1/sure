# SURE-EVAL 模型接入 Harness 规范 (第一阶段)

**版本**: v1.0  
**目标**: 将语音模型仓库接入为**可复现的本地推理单元**  
**范围**: 第一阶段聚焦环境适配、本地格式化管理、最小推理调用

---

## 1. 目标与范围

> **Constitution**: 所有 harness 组件与 agent 必须遵守项目级 constitution 定义的高层不变原则，详见 [`policies/constitution.md`](policies/constitution.md)。

### 1.1 核心目标

### 1.0 Harness 分支边界覆盖规则

本 `sure_onboard` harness 版本以 **本地可运行模型部署闭环** 为默认成功条件：

- `package=none`：默认路径，只要求本地环境、权重、wrapper、fixture、import/load/infer/contract validation 和 artifact/verdict 完整。
- `package=docker-local`：在本地闭环通过后，额外要求 Docker build 和 Docker validate 通过。
- `package=docker-registry`：在 Docker local 通过后，额外要求 push 和 registry pull verify 通过。
- VC/HPC submit/validate 不属于核心 `/sure_onboard` 成功条件；如需支持，应作为独立 deployment plugin 或独立 slash command。

当本文档后续历史段落与以上边界冲突时，以本节和 `SKILL.md` 状态机为准。Docker/registry/VC 相关内容可以作为可选部署参考，不得让 `package=none` 的本地可运行模型被判失败。

本文档定义第一阶段模型接入的标准 workflow，确保：

- **环境可重建**: 通过规范化的 backend 选择和依赖管理
- **推理可复现**: 通过统一的验证契约和工件保存
- **过程可审计**: 通过结构化的 verdict 和 artifact 记录
- **权重可定位**: 默认将 ckpt / runtime cache 收敛到 model-local 路径

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
- model-local checkpoint / cache 路径收敛
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

### 2.0 Context Selection Unit

在读取任务经验、环境经验或失败案例前，必须先执行上下文选择。目标是让
tool agent 只读取当前模型真正需要的记忆，避免默认注入所有任务、所有环境、
所有 bad case。

**输入**:

- `MODEL_INPUT.task_type`
- `MODEL_INPUT.deployment_type`
- `MODEL_INPUT.environment_hint.preferred_backend`
- repo evidence: `pyproject.toml`, `requirements.txt`, `environment.yml`,
  `pixi.toml`, `Dockerfile`, README, setup scripts
- observed failure, if any

**默认读取**:

- `references/AGENTS.md`
- `references/memory/COMMON.md`
- `references/task_playbooks/ROUTING.md`
- `references/playbooks/env_ROUTING.md`

**按任务读取**:

- 先读 `task_playbooks/ROUTING.md`
- 只读取当前任务匹配的 task playbook
- 不允许默认读取全部 `task_playbooks/*.md`

**按环境读取**:

- 先读 `playbooks/env_ROUTING.md`
- 只读取当前 backend 匹配的环境 playbook
- 不允许默认读取全部 `playbooks/env_*.md`

**按失败读取**:

- 正常路径不读取 bad case
- 出现具体失败或高风险信号时，先读 `memory/ROUTING.md`
- 只有命中 bad-case trigger 时才读 `memory/bad_cases/README.md` 和对应案例文件

**记录要求**:

在 `backend_choice.json`、`build_plan.json`、`spec_validation.json`、
`failure_classification.json` 或 `tool_agent_run_report.json` 中记录实际读取的
context 文件和跳过理由。

禁止项：

- 不得把所有任务 playbook 当成默认上下文
- 不得把所有环境 playbook 当成默认上下文
- 不得把所有历史 bad case 当成默认上下文
- 不得用“读全量文档”代替任务类型、backend 或失败类型判断

### 2.1 主状态机

```
LOAD_MODEL_INPUT
    ↓ (解析 /sure_feed MODEL_INPUT 与运行参数)
CONTEXT_SELECTION
    ↓ (只读取当前任务/环境/契约需要的上下文)
DISCOVER
    ↓ (收集 repo 信息)
CLASSIFY
    ↓ (判断模型类型)
PLAN
    ↓ (选择 backend)
BUILD_PLAN
    ↓ (生成可执行构建计划)
VALIDATE_SPEC
    ↓ (验证 spec 完整性与证据充分性)
PREPARE_FIXTURE
    ↓ (把任务 fixture/payload 复制到 model-local fixture 目录)
BUILD_ENV
    ↓ (构建隔离环境)
FETCH_WEIGHTS
    ↓ (获取/验证权重)
VALIDATE_ENV_COMPAT
    ↓ (验证环境/设备/权重兼容性)
GENERATE_WRAPPER
    ↓ (生成统一 wrapper 与 validate.py)
VALIDATE_IMPORT
    ↓ (真实执行导入验证)
VALIDATE_LOAD
    ↓ (真实执行加载验证)
VALIDATE_INFER
    ↓ (真实执行最小推理)
VALIDATE_CONTRACT
    ↓ (真实执行输出满足 io_contract)
SAVE_ARTIFACTS
    ↓ (保存并检查所有本地部署工件)
PACKAGE_GATE
    ↓ (按 package profile 判定 local/docker/registry readiness)
VERDICT
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
| `model.spec.yaml` | `sure/models/{model}/model.spec.yaml` | 模型规范 |
| `backend_choice.json` | `sure/models/{model}/artifacts/backend_choice.json` | 后端选择记录 |
| `build.log` | `sure/models/{model}/artifacts/build.log` | 构建日志 |
| `validation.log` | `sure/models/{model}/artifacts/validation.log` | 验证日志 |
| `verdict.json` | `sure/models/{model}/artifacts/verdict.json` | 最终判定 |
| `wrapper` | `sure/models/{model}/model.py`, `sure/models/{model}/server.py`, `sure/models/{model}/__init__.py` | 模型 wrapper 文件集 |
| `validate.py` | `sure/models/{model}/validate.py` | 本地验证脚本：跑 fixture 推理并算指标 |
| `fixture` | `sure/models/{model}/fixture/<task>/<sub-task>/` | 测试音频 + gt.jsonl，评估落地推理 |
| `artifact_manifest.json` | `sure/models/{model}/artifacts/artifact_manifest.json` | 工件清单 |

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
scripts/templates/
├── model.spec.yaml          # 模型规范模板
├── validate.py              # harness runtime validation template
├── verdict.json             # 判定结果模板
└── artifact_manifest.json   # 工件清单模板

references/templates/
└── validate_metric_enrichment.md  # 原 model-tool metric enrichment 经验保留
```

### 4.3 子文档索引

| 主题 | 文档路径 |
|------|----------|
| 通用记忆 | `memory/COMMON.md` |
| 任务 playbook 路由 | `task_playbooks/ROUTING.md` |
| 任务级模型接入手册索引 | `task_playbooks/README.md` |
| ASR / Streaming ASR 接入手册 | `task_playbooks/ASR.md`，仅 ASR 路由命中时读取 |
| 语音理解多任务接入手册 | `task_playbooks/SPEECH_UNDERSTANDING.md`，仅多任务语音理解路由命中时读取 |
| TTS 接入手册 | `task_playbooks/TTS.md`，仅 TTS 路由命中时读取 |
| VC 接入手册 | `task_playbooks/VC.md`，仅 VC 路由命中时读取 |
| KWS 接入手册 | `task_playbooks/KWS.md`，仅 KWS 路由命中时读取 |
| 环境 playbook 路由 | `playbooks/env_ROUTING.md` |
| UV 环境策略 | `playbooks/env_uv.md`，仅 uv 路由命中时读取 |
| Pip 环境策略 | `playbooks/env_pip.md`，仅 pip 路由命中时读取 |
| Conda 环境策略 | `playbooks/env_conda.md`，仅 conda 路由命中时读取 |
| Pixi 环境策略 | `playbooks/env_pixi.md`，仅 pixi 路由命中时读取 |
| Docker 环境策略 | `playbooks/env_docker.md`，仅 docker 路由命中时读取 |
| API 模型策略 | `playbooks/model_api.md`，仅 API 路由命中时读取 |
| Optional memory 路由 | `memory/ROUTING.md` |
| Bad case 索引 | `memory/bad_cases/README.md`，仅失败 trigger 命中时读取 |
| 失败分类体系 | `playbooks/failure_taxonomy.md`，仅进入 DIAGNOSE 或失败分类时读取 |
| Model Spec 规范 | `specs/model_spec_template.md` |
| 验证契约 | `contracts/minimal_validation.md` |
| Model-local ckpt 规则 | `contracts/model_local_checkpoint_rule.md` |
| Metric enrichment 模板经验 | `templates/validate_metric_enrichment.md`，仅生成 wrapper、metric report 或修复 metric 语义时读取 |
| 经验资产同步清单 | `experience_loss_register.md`，仅审计 harness 与原 model-tool 经验同步时读取 |

---

## 4.4 Model-Local Checkpoint Rule

如果 `weights.required == true`，权重/缓存必须收敛到模型目录下，但
`checkpoints/` 和 `.runtime/` 的语义不同：

```text
sure/models/{model}/.runtime/modelscope_cache/  # ModelScope 等 provider cache
sure/models/{model}/checkpoints/                # 显式本地权重（如有）
```

其中：

- `.runtime/modelscope_cache/` 用于保存 ModelScope 远端模型下载后的
  provider cache，SURE wrapper 应通过 `weights_manifest.json` 中的
  `resolved_local_model_path` 加载实际路径。
- `checkpoints/` 只用于保存人工提供或明确 materialize 的本地权重；如果权重
  已在 `.runtime/modelscope_cache/`，`checkpoints/` 可以为空。
- `.runtime/` 还用于保存 model-local venv、HF cache、包缓存等运行期状态。

只有在明确受限时，才允许退回到 host-global 路径，例如：

- workspace 容量不足
- 权限限制
- 上游运行时强依赖全局缓存

一旦使用 fallback，必须在 `build_plan.json` 与 `weights_manifest.json` 中记录：

- fallback 原因
- fallback 目标路径
- 当前运行时如何重新定位本地权重

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
| 本地验证通过 | `validate.py` 跑完 fixture 并产出指标 | sample_output.json |
| 工件已保存 | spec/verdict/log/wrapper/validate.py/fixture 都存在 | artifact_manifest.json |

---

## 7. 状态定义

### 7.1 DISCOVER

**输入**: repo URL / local repo, 初始模型信息  
**动作**: 扫描 repo 文件结构，收集 README、requirements、environment.yml 等；收敛 runtime identity（display identity / runtime load identity / local dir name）  
**输出**: repo_summary.json

**后续**: 可执行 [预检清单](playbooks/preflight_checklist.md) 生成 `preflight_summary.json`
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

**参考**: [Spec Validation 契约](contracts/spec_validation.md)

### 7.5 BUILD_ENV

**动作**: 使用选定 backend 构建隔离环境，按 build plan 设置 cache/tmp/runtime 路径  
**输出**: environment ready / failure  
**工件**: build.log

### 7.5 FETCH_WEIGHTS

**动作**: 获取或验证权重，记录路径和校验信息  
**输出**: weights ready / failure

### 7.5a VALIDATE_ENV_COMPAT

**动作**: 验证环境兼容性
- 检查 torch / torchvision 版本匹配性
- 检查网络限制（HF/ModelScope 可达性）
- 记录环境约束到 `artifacts/build_plan.json`

**输出**: env_compat result
**失败**: 进入 DIAGNOSE (runtime_backend_incompatible / network_unreachable)

**必检命令**:
```bash
# 验证模型路径可解析（不触发下载）
.venv/bin/python -c "from model import ModelWrapper; m = ModelWrapper(); print(m._resolve_model_path())"
```

**可选检查（如遇 import/runtime 报错时执行）**:
```bash
# 验证 torchvision 与 torch 版本匹配（仅当出现 RuntimeError: operator torchvision::nms does not exist 时）
.venv/bin/python -c "import torch, torchvision; print(f'torch {torch.__version__} + torchvision {torchvision.__version__}')"
```

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

**动作**: 
1. 生成统一 wrapper skeleton，填写最小调用逻辑
2. 生成 `validate.py`：本地端到端验证脚本
3. 准备 `fixture/`：测试音频 + `gt.jsonl`

**输出**: 
- wrapper 文件集
  - `model.py`: 核心模型包装类
  - `server.py`: MCP 服务器实现  
  - `__init__.py`: 包导出声明
  - (可选) `config.yaml`: MCP 工具配置
- `validate.py`: 本地验证脚本
- `fixture/<task>/<sub-task>/`: 测试样本与 ground truth

**参考**: 
- [Wrapper 契约](specs/wrapper_contract.md) 定义各文件职责与最小接口
- [validate.py 模板](templates/validate.py) 提供通用验证脚本框架

**约束**:
- wrapper 应复用已验证通过的 repo-native path
- wrapper smoke 若执行，应避免被无关全局依赖阻塞
- `fixture/` 数据来源优先从 `tests/fixtures/` 或 `data/datasets/` 复制，保持与 task/sub-task 对应
- `gt.jsonl` 每行一条 JSON，至少包含 `key`、`audio`、`ground_truth` 字段
- **fixture 样本数量**：2–3 条最佳，**最多不超过 5 条**（控制验证耗时与磁盘占用）
- `validate.py` 执行流程：load fixture → import → load → infer → evaluate → 输出 `sample_output.json`

### 7.11 SAVE_ARTIFACTS

**动作**: 保存 spec snapshot、log、lockfile、verdict、wrapper、validate.py、fixture  
**输出**: artifact_manifest.json

### 7.12 DIAGNOSE / REPLAN

**动作**: 
- Evaluator Agent 结合 failure taxonomy 分类失败
- Builder Agent 给出 retry 建议

**输出**: 
- failure_classification.json
- retry_recommendation.json
- 决定：RETRY_FROM_CHECKPOINT / FAIL_STOP

**约束**: 重试必须遵守 [重试与升级政策](policies/retry_and_escalation.md)，禁止盲重试

---

## 8. 可选 Docker 镜像制作与集群提交

本节定义模型 onboarding 完成后的可选 Docker 化步骤。目标是让模型可以通过容器提交到集群运行，同时保持模型之间的环境隔离。本节不改变前述 harness 状态机；只有用户选择 `package=docker-local` 或 `package=docker-registry` 时，才把 Docker/registry readiness 纳入 `/sure_onboard` 成功条件。VC/HPC submit 仍然是外部部署能力，不是核心 `/sure_onboard` gate。

### 8.1 适用范围

**选择 Docker package profile 时需要制作独立 Docker 镜像**:
- `/sure_onboard package=docker-local`
- `/sure_onboard package=docker-registry`
- 需要本地 Python/系统依赖、模型权重、GPU/CPU runtime 的模型
- 需要通过未来独立部署命令提交到集群容器任务运行的模型

**可以跳过或降级记录**:
- 默认 `package=none` 且本地 import/load/infer/contract validation 已通过
- 纯 API 模型，且运行时只需要远程 endpoint/token
- 工具本身已经是系统命令且无 Python runtime 依赖

跳过 Docker 时，在 `package_gate.json` / `verdict.json` 中记录 `readiness.local_ready=true`、`docker_ready=false` 即可；这不构成失败。

### 8.2 镜像命名规则

学校团队级公共镜像和个人镜像统一使用：

```text
docker.v2.aispeech.com/sjtu/<image_name>:<image_label>
```

SURE-EVAL 模型镜像必须使用：

```text
docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model_name}:v1.0
```

后续同一模型每次环境或脚本变更，递增 tag：

```text
v1.1, v1.2, v1.3, ...
```

不要复用已经推送过且语义不同的 tag。

### 8.3 镜像边界

Docker 镜像只固化可复现运行环境：
- Python 版本
- torch/CUDA 或 CPU runtime
- 模型依赖包
- SURE-EVAL evaluator 依赖
- 必要系统包，如 `ffmpeg`, `libsndfile1`

Docker 镜像不应固化会频繁变化或与宿主强绑定的内容：
- 当前开发中的模型代码
- fixture / dataset
- checkpoint / runtime cache
- 本地 `.venv`
- 验证输出目录

这些内容应通过 `docker run -v <absolute_host_path>:<container_path>` 在运行时挂载。本文档没有要求把 `.runtime` 中的权重迁移到 `checkpoints/`；只要求路径可定位、可挂载、可复现。

### 8.4 必备文件

每个本地模型目录应包含：

```text
sure/models/{model}/
├── Dockerfile
├── Dockerfile.dockerignore
├── docker_build.sh
├── docker_validate.sh
└── docker_artifacts/
```

`docker_build.sh` 负责构建镜像；`docker_validate.sh` 负责以容器方式运行 `validate.py`。

### 8.5 Dockerfile 最小改动构建原则

维护已有模型镜像时，优先采用最小改动策略，避免无关层缓存失效：

- 新增系统包、Python 包或模型专用补丁优先追加在 `Dockerfile` 尾部，不要无故重排已有 `RUN` / `COPY` 层。
- 需要加速 apt/pip 下载时，使用 BuildKit cache mount，例如 `RUN --mount=type=cache,target=/var/cache/apt ...` 和 `RUN --mount=type=cache,target=/root/.cache/pip ...`。
- 如果调试时曾在镜像内部通过 `pip install` 临时安装包，必须同步更新对应模型目录下的 `Dockerfile`，否则下一次构建或集群运行不可复现。
- `docker_build.sh` 应固定默认 `IMAGE_TAG`，并允许通过环境变量覆盖 `IMAGE_TAG` 和 `BASE_IMAGE`。
- 构建完成后必须用 `docker image inspect <image>` 或一次容器验证确认本机镜像存在。

### 8.6 构建与推送步骤

本地调试可以直接使用本机镜像；集群运行任务必须使用已经推送到公司仓库、且能从公司仓库重新 `docker pull` 的镜像。

1. 检查本地镜像：

```bash
docker images
```

2. 如果基础镜像来自仓库且本机不存在，先拉取：

```bash
docker pull docker.v2.aispeech.com/<namespace>/<base_image>:<tag>
```

3. 构建模型镜像。本地调试镜像名可以先用短名，但最终提交集群前必须使用公司仓库完整 tag：

```bash
cd /absolute/path/to/sure-eval
sure/models/{model}/docker_build.sh
```

`docker_build.sh` 必须支持覆盖：

```bash
IMAGE_TAG=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model}:v1.1 \
BASE_IMAGE=<base-image> \
sure/models/{model}/docker_build.sh
```

4. 确认镜像存在：

```bash
docker image inspect docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model}:v1.1
```

5. 推送镜像到公司仓库：

```bash
docker push docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model}:v1.1
```

6. 等待仓库生效后，从公司仓库重新拉取验证。仓库生效可能需要十几分钟：

```bash
docker pull docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model}:v1.1
```

注意：`docker pull` 是从仓库拉取到本机；将本机镜像上传到仓库应使用 `docker push`。如果 `docker pull` 返回 `manifest unknown`，说明该 tag 当前不可从仓库拉取，不能作为集群任务镜像。

如果 agent 执行 `docker push` 失败，不要立即放弃。公司仓库 push 可能受当前 shell 的代理变量或执行权限影响；`docker pull` 可成功不代表 `docker push` 一定走同一条网络路径。典型可恢复信号：

- `docker push` 输出 `请求失败，状态码：502` 或其他 registry/proxy 5xx。
- `docker push` 退出码看似成功，但没有正常 layer push / digest 输出。
- 清除代理后变成 `operation not permitted`，说明沙箱网络拦截了直连 registry。
- `docker image inspect`、`docker images`、`docker version` 或 `docker info` 触发 Docker CLI panic，例如 `docker-cli/modules.PassDocker()`，但 `docker pull` 仍可成功。

遇到这些情况时，agent 必须：

1. 记录原始失败输出，不要误判为 Dockerfile 或镜像 tag 问题。
2. 优先清除代理变量后重试 push：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  docker push docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model}:v1.1
```

3. 如果清除代理后因为沙箱网络返回 `operation not permitted`，应请求非沙箱/完整网络权限后用同一条命令重试。
4. 如果 registry 返回 `镜像已存在,请更新tag`，说明 push 请求已经到达公司仓库，且当前 tag 不允许覆盖；此时递增 tag，例如 `v1.2`，重新 build/tag/push。
5. push 成功或 tag 已存在后，都必须用 `docker pull` 验证公司仓库可拉取：

```bash
docker pull docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_{model}:v1.1
```

只有 `docker pull` 返回 digest / `Image is up to date`，才可将公司仓库镜像标记为可用于集群任务。如果上述恢复步骤仍失败，再在最终汇报中明确请用户在登录态完整的交互终端手动执行 push/pull。

也可以按通用变量形式执行：

```bash
dockerfile=Dockerfile.$image
docker build -f $dockerfile -t $image .
docker tag $image $REPO/$image
docker push $REPO/$image
docker pull $REPO/$image
```

### 8.7 容器验证

容器验证必须证明：
- 未挂载宿主 `.venv`
- Python 来自镜像内 venv，例如 `/opt/{model}_venv/bin/python`
- 模型代码、fixture、权重使用宿主绝对路径挂载
- `validate.py` 在容器内通过
- 结果写入可持久化的挂载目录

对于当前 `validate.py` 会查找 `REPO_ROOT/.venv/bin/python` 的模型，`docker_validate.sh` 应在容器启动后创建运行时链接：

```bash
ln -sfn /opt/{model}_venv /workspace/sure-eval/.venv
```

这个链接必须指向镜像内环境，禁止挂载宿主 `.venv`。

`docker_validate.sh` 必须使用宿主绝对路径，例如：

```bash
MODEL_DIR=/hpc_stor03/.../sure/models/{model}
MODELSCOPE_CACHE=/hpc_stor03/.../sure/models/{model}/.runtime/modelscope_cache
ARTIFACTS_DIR=/hpc_stor03/.../sure/models/{model}/docker_artifacts
```

### 8.8 vc submit 注意事项

提交到集群时，所有宿主路径必须是绝对路径。不要依赖当前工作目录或相对路径。

推荐提交命令形态：

```bash
MODEL_DIR=/absolute/path/to/sure/models/{model} \
MODELSCOPE_CACHE=/absolute/path/to/sure/models/{model}/.runtime/modelscope_cache \
ARTIFACTS_DIR=/absolute/path/to/sure/models/{model}/docker_artifacts \
/absolute/path/to/sure/models/{model}/docker_validate.sh
```

如果模型需要 `.env`，应使用绝对路径注入，例如：

```bash
--env-file /absolute/path/to/.env
```

只有模型确实需要 API token、endpoint 或密钥时才挂载 `.env`。本地模型验证一般不需要。

### 8.9 asr_qwen3 验证参考

`asr_qwen3` 的验证镜像：

```text
docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_qwen3:v1.1
```

验证结果：

```text
overall: PASSED
en WER: 0.08771929824561403
zh CER: 0.0
```

结果目录：

```text
sure/models/asr_qwen3/docker_artifacts/
```

---

## 9. 文档索引

### 9.1 Constitution (高层不变原则)

- [项目 Constitution](policies/constitution.md) - 所有组件必须遵守的 10 条核心规则

### 9.2 Policies (决策政策)

- [证据优先级政策](policies/evidence_priority.md) - 多源冲突时的判断依据
- [重试与升级政策](policies/retry_and_escalation.md) - 失败处理与人工介入规则
- [补丁记录政策](policies/patch_recording.md) - 非上游修改的留痕要求

### 9.3 Playbooks (执行手册)

- [预检清单](playbooks/preflight_checklist.md) - BUILD_ENV 前的环境检查
- [UV 环境策略](playbooks/env_uv.md)
- [Pip 环境策略](playbooks/env_pip.md)
- [Conda 环境策略](playbooks/env_conda.md)
- [Pixi 环境策略](playbooks/env_pixi.md)
- [Docker 环境策略](playbooks/env_docker.md)
- [API 模型策略](playbooks/model_api.md)
- [失败分类体系](playbooks/failure_taxonomy.md)

### 9.4 Specs (规范定义)

- [Wrapper 契约](specs/wrapper_contract.md) - model.py/server.py/__init__.py 职责边界
- [Model Spec 模板说明](specs/model_spec_template.md)

### 9.5 Contracts (验证契约)

- [Spec Validation 契约](contracts/spec_validation.md) - spec 前置验证规范
- [Fixture 政策](contracts/fixture_policy.md) - 测试样本规范
- [最小验证契约](contracts/minimal_validation.md)

### 9.6 Registry (模型特异性记录)

- [已知问题注册表](../../registry/known_issues.md) - 模型级例外与工作区

### 9.7 Templates (模板文件)

- [model.spec.yaml](templates/model.spec.yaml)
- [spec_validation.json](templates/spec_validation.json)
- [verdict.json](templates/verdict.json)
- [artifact_manifest.json](templates/artifact_manifest.json)

---

## 10. 第一阶段约束重申

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
