# sure-xsy 功能改动说明：GitLab 行为再实现

| 项 | 值 |
|---|---|
| **性质** | 行为再实现（非 cherry-pick / 非历史合并） |
| **源** | GitLab `sure` · `rollback/liutao-port-oref` · `d7dfd06`（不含）→ `daf5e62` |
| **目标** | GitHub `sure-xsy` · `harness-tui-agent` |
| **路径映射** | GitLab 单体 `sure_eval` → xsy `sure_infer`（推理）+ `sure_eval`（打分） |
| **日期** | 2026-09-21 |
| **状态** | 代码已落地，**未自动 commit** |

对照矩阵（规划）：`openspec/changes/port-gitlab-d7dfd06-to-head/`  
源仓时间线（GitLab 本地文档）：`docs/changes-d7dfd06-to-daf5e62.md`

---

## 1. 改了什么（按能力）

### 1.1 系统 Docker 解析 + PATH 去 agent shim

**问题：** agent 自带目录里的 `docker` shim 抢 PATH，导致推镜像/检图走错二进制。

**改动：**

- 新增 `resolve_docker_binary()`：
  - **infer**：`SURE_EVAL_DOCKER_BIN` → `SURE_DOCKER_BIN` → `/usr/bin|/usr/local/bin|/bin/docker` → `PATH`
  - **onboard**：`SURE_ONBOARD_DOCKER_BIN` → `SURE_DOCKER_BIN` → 同上
- 接线：`container_execution`、`check_execution_surface_compliance`、`check_container_package`
- `sure_infer` / `sure_eval` hooks `preStart`：`demoteAgentBinDir`（与 onboard/trans 一致）
- **`daf5e62`**：onboard 状态机 `package_container.helperScripts` 放行 `docker_runtime.py`（否则 preToolCall 会拦解析脚本）

**主要文件：**

- `sure/skills/sure_infer/scripts/docker_runtime.py`（新）
- `sure/skills/sure_onboard/scripts/docker_runtime.py`（新）
- `sure/skills/sure_{infer,eval}/hooks/index.ts`
- `sure/skills/sure_onboard/hooks/state-machine.ts`
- `packages/coding-agent/test/suite/sure-onboard-state-machine.test.ts`

---

### 1.2 宿主侧执行溯源（host-owned provenance）

**问题：** 原先在容器内 `git rev-parse` 推 harness/engine commit，容器无 repo 时拿不到真值。

**改动：**

- 新增 `execution_provenance.py`（schema `sure.eval.execution_provenance.v1`）
- 启动前写 sidecar：`artifacts/execution_provenance.json`
- **注入子进程/容器 env**：`execution_provenance_env(...)`  
  （`SURE_EVAL_EXECUTION_PROVENANCE`、`SURE_HARNESS_COMMIT`、`SURE_EVALUATION_ENGINE_COMMIT` 等）
- `protocol_writer` 优先读 sidecar / env / runtime binding，并给出 `*_unavailable_reason`
- 删除已无引用的 `_git_commit` 死路径

**主要文件：**

- `sure/skills/sure_infer/scripts/execution_provenance.py`（新）
- `container_execution.py` / `python_execution.py`
- `protocol_writer.py`
- `test_protocol_provenance.py` / `test_execution_provenance_env.py`

---

### 1.3 Docker 交付不被宿主 Python 环境误伤

**问题：** 本地 backend 失败时，Docker 交付路径也被含糊挡住。

**改动（对齐 GitLab，非“env_ready=false 仍放行”）：**

- `backend=docker` 且 `env_ready=true` → 短路通过
- 本地 backend 失败 + docker delivery profile → 失败信息明确提示 Docker 路径不应被宿主 Python 问题单独阻塞

**主要文件：** `sure/skills/sure_onboard/scripts/check_env.py` + `test_check_env_docker.py`

---

### 1.4 Onboard 制品路径可移植

**问题：** finalize 后 `package_gate` 等仍残留绝对路径，换机难复用。

**改动：**

- `portable_path` / `normalize_portable_paths`：对 `path` / `*_path` 在 model/run 根内改为相对路径
- 根外绝对路径保留（与 GitLab 同形）；越界拒绝仍靠既有 `ensure_safe_bundle_targets`

**主要文件：** `finalize_model_bundle.py` + `test_docker_delivery_contract.py`（normalize 回归）

---

### 1.5 报告侧数据集身份保持两段式

**问题：** 多任务投影后，报告 `dataset.name` 可能带上 task 段。

**改动：**

- `_report_dataset_name` / RPS payload：尽量 `source__version`，**不含 task 后缀**
- **不**采用 GitLab 后期三元组 formal id（`source__version__task`）

**主要文件：** `sure/skills/sure_infer/scripts/evaluate_predictions.py`

---

### 1.6 数据集格式文档 + OpenBench 校验

**改动：**

- `references/dataset_formats.md`：`ds_pool` vs OpenBench 布局、字段映射、陷阱
- `validate_openbench_dataset.py`：stdlib 校验脚本
- `sure_infer/SKILL.md` 一句话指针 + Docker env 说明

**说明：** 默认推理主路径不依赖该文档；供导出/排错使用。

---

### 1.7 sure_trans 任务/依赖/框架契约

**改动：**

- 新增共享契约模块：
  - `task_contract.py`（含 SV）
  - `dependency_contract.py`（shell `bash -n`、路径证据、review 信号）
  - `framework_contract.py`
- `inspect_dependencies.py` / `detect_framework.py` 改为契约驱动
- `run_trans_validate.py`：TTS/VC `audio_contract` 等
- python `source_kind` 做了适配（不强制 dockerfile）

**主要文件：** `sure/skills/sure_trans/scripts/*`

---

## 2. 明确不做 / 跳过

| 项 | 原因 |
|---|---|
| oref / HPC `site.bundled`、`ai_oref-` registry | 站点私有，不进公开默认配置 |
| `1d215ea` 三元组 dataset formal id | 与 xsy **两段式** `source__version` + projection 设计冲突 |
| runtime pin 中间态反复 bump | xsy 已在最终 lock |
| `vc_precheck` / `vc_submitter` | xsy 无对应树 |
| 纯 merge / import 排序 | 无产品行为 |
| VAD 评测桥（若仅 GitLab eval 有） | 已知缺口，未在本次强行补齐 |

---

## 3. 文件清单（工作区）

### 新增

```
sure/skills/sure_infer/scripts/docker_runtime.py
sure/skills/sure_infer/scripts/execution_provenance.py
sure/skills/sure_infer/scripts/validate_openbench_dataset.py
sure/skills/sure_infer/scripts/test_docker_runtime.py
sure/skills/sure_infer/scripts/test_execution_provenance_env.py
sure/skills/sure_infer/references/dataset_formats.md
sure/skills/sure_onboard/scripts/docker_runtime.py
sure/skills/sure_onboard/scripts/test_docker_runtime.py
sure/skills/sure_onboard/scripts/test_check_env_docker.py
sure/skills/sure_trans/scripts/task_contract.py
sure/skills/sure_trans/scripts/dependency_contract.py
sure/skills/sure_trans/scripts/framework_contract.py
```

### 修改（要点）

```
sure/skills/sure_infer/hooks/index.ts          # demoteAgentBinDir
sure/skills/sure_eval/hooks/index.ts           # demoteAgentBinDir
sure/skills/sure_infer/scripts/container_execution.py
sure/skills/sure_infer/scripts/python_execution.py
sure/skills/sure_infer/scripts/protocol_writer.py
sure/skills/sure_infer/scripts/evaluate_predictions.py
sure/skills/sure_infer/scripts/check_execution_surface_compliance.py
sure/skills/sure_infer/scripts/test_protocol_provenance.py
sure/skills/sure_infer/SKILL.md
sure/skills/sure_onboard/hooks/state-machine.ts   # daf5e62 helperScripts
sure/skills/sure_onboard/scripts/check_container_package.py
sure/skills/sure_onboard/scripts/check_env.py
sure/skills/sure_onboard/scripts/finalize_model_bundle.py
sure/skills/sure_onboard/scripts/test_docker_delivery_contract.py
sure/skills/sure_onboard/SKILL.md
sure/skills/sure_trans/scripts/inspect_dependencies.py
sure/skills/sure_trans/scripts/detect_framework.py
sure/skills/sure_trans/scripts/run_trans_validate.py
packages/coding-agent/test/suite/sure-onboard-state-machine.test.ts
```

### 建议提交范围（公开 GitHub）

只 stage `sure/skills/**` + 上述 coding-agent 单测 + 本说明文档即可。  
`openspec/changes/port-gitlab-d7dfd06-to-head/` 含内网路径/作者/patch 摘录，**公开仓建议不提交或脱敏**。

---

## 4. 与源 commit 的对应（摘要）

| 能力 | 代表 commit | xsy 结果 |
|---|---|---|
| onboard/eval PATH demote | `cbf372c` `79954eb` | done |
| system docker eval/onboard | `6619045` `dc4327b` | done |
| 放行 docker_runtime helper | **`daf5e62`** | done |
| host provenance + env inject | `3e9f2aa` | done |
| check_env docker 隔离 | `76a1bed` | done |
| finalize 相对路径 | `b99e66d` | done |
| report 2-seg name | `f307afe` | done |
| dataset format docs | `bae78be` | done |
| sure_trans contracts | `1d2f05d` | done (adapt) |
| 3-seg formal ids | `1d215ea` | **skip** |
| oref site | `0744757` `9abe8ee` | **skip** |

---

## 5. 验证（已跑过的）

- `test_docker_runtime`（infer/onboard）
- `test_execution_provenance_env`
- `test_protocol_provenance`（sidecar + unavailable_reason）
- `test_check_env_docker`
- `test_normalize_portable_paths_rewrites_nested_abs`
- vitest：`allows package_container to resolve the Docker binary…`

---

## 6. 一句话

把 GitLab 上「系统 Docker、PATH 去 shim、宿主 provenance、Docker 交付不被宿主 Python 误伤、制品相对路径、报告两段式数据集名、OpenBench 文档、trans 契约」等行为，按 xsy 的 infer/eval 分叉结构重新实现；站点私有配置与 3-seg id 政策不迁入。
