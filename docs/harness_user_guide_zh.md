# SURE Harness 用户指南

这份文档面向刚拿到仓库的用户，说明从 0 到 1 怎么使用、每个功能需要什么输入、会产生什么输出，以及如何判断一次运行是否成功。

产品演示站点：https://sure-eval.com/harness

相关文档：

| 需求 | 阅读 |
| --- | --- |
| 指标引擎设置和精确 pipeline 选择。 | [评估引擎](./evaluation_engine_zh.md) |
| 常见安装、provider、数据集和 VC 问题。 | [排错指南](./troubleshooting_zh.md) |
| Skill 包结构和维护者检查。 | [开发指南](./development_zh.md) |
| 英文指南。 | [User guide](./harness_user_guide.md) |

## 核心理解

SURE Harness 把音频模型评估拆成一组带 artifact 门禁的 TUI slash commands。

```text
发现模型 -> 接入模型 -> 正式评估 -> 复用已有 predictions 重新评估
```

Harness 不替代指标引擎。metric 能力、route nodes、精确 `pipeline_id` 选择、node-local 环境检查都来自独立的 `sure-evaluation` checkout，默认位置是 `sure/external/sure-evaluation`，也可以用 `SURE_EVALUATION_HOME` 指定。

## 从 0 到第一次成功运行

1. 克隆并安装 harness。

```bash
git clone --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
npm run sure:doctor
```

2. 准备指标引擎和 benchmark JSONL。

```bash
mkdir -p sure/external
git clone https://github.com/PigeonDan1/sure-evaluation.git sure/external/sure-evaluation

mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
npm run sure:doctor
```

3. 启动 TUI。

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

4. 初始化项目。

```text
/sure_init
```

5. 发现或提供模型。

```text
/sure_feed https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf
```

期望输出：

```text
sure/handoffs/<model_name>/model_input.yaml
sure/handoffs/<model_name>/artifacts/feed_report.json
```

6. 把模型接入为可运行的本地单元。

```text
/sure_onboard model=<model_name> device=auto package=none
```

期望输出：

```text
sure/models/<model_name>/model.py
sure/models/<model_name>/model.spec.yaml
sure/models/<model_name>/config.yaml
sure/models/<model_name>/server.py
sure/models/<model_name>/verdict.json
sure/models/<model_name>/artifacts/
```

7. 评估已接入模型。

```text
/sure_eval model=<model_name> datasets=aishell1__v1.0.2__asr metrics=cer max_samples=5 execution=local device=auto
```

期望输出：

```text
<eval_run_dir>/predictions/<dataset>.txt
<eval_run_dir>/predictions/<dataset>.jsonl
<eval_run_dir>/validation_payload.json
<eval_run_dir>/evaluation_route_plan.json
<eval_run_dir>/evaluation_payload.json
<eval_run_dir>/report.jsonl
<eval_run_dir>/metrics/<dataset>/<metric_slug>/report.json
<eval_run_dir>/metrics/<dataset>/<metric_slug>/pipeline_description.json
<eval_run_dir>/sample_reports/<dataset>/<metric_slug>.jsonl
<eval_run_dir>/main_agent_run_report.json
```

8. 基于已有结果重新评估，不重新推理。

```text
/sure_reval source=<results_or_run_dir> datasets=aishell1__v1.0.2__asr max_samples=5 pipeline_id=asr.zh.cer.wetext_norm_zh_itn_v1.wenet_cer_v1 pipeline_id=asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1
```

期望输出：

```text
<tmp_reval_run>/prediction_source_resolved.json
<tmp_reval_run>/prediction_reuse_manifest.json
<tmp_reval_run>/validation_payload.json
<tmp_reval_run>/evaluation_payload.json
<tmp_reval_run>/evaluation_route_plan.json
<tmp_reval_run>/metrics/
<tmp_reval_run>/sample_reports/
<tmp_reval_run>/main_agent_run_report.json
<tmp_reval_run>/reval_run_report.json
```

## 功能参考

| Command | 用途 | 必需输入 | 常见可选输入 | 期望输出 | 成功标准 |
| --- | --- | --- | --- | --- | --- |
| `/sure_init` | 配置 harness 项目和 agent 运行环境。 | 无 | provider/auth 配置 | 项目配置和 doctor 证据 | 能发现 skills 和必要依赖 |
| `/sure_feed` | 发现模型并生成接入输入。 | 直接模型 URL 或 `source/query` | `max_models`, `download`, `handoff_root` | `sure/handoffs/<model>/model_input.yaml`, feed artifacts | 选中模型有任务证据、repo、weights source、fixture、IO contract |
| `/sure_onboard` | 将模型变成可运行的本地推理单元。 | `model=<handoff>` 或 `model_input_path=...` | `device`, `package`, `preferred_backend`, `skip_download` | `sure/models/<model>/` 下的 wrapper、spec、config、fixture、verdict | import/load/infer/contract 校验通过，`verdict.json` ready |
| `/sure_eval` | 执行 prediction 生成和 route-backed evaluation。 | `model`, `datasets` | `metrics`, `max_samples`, `execution`, `device`, VC 资源 | predictions、validation、route plan、metric reports、run report | 记录正式执行面；predictions 校验通过；metric reports 存在 |
| `/sure_reval` | 只基于已有 predictions 重新计算指标。 | `source` | `datasets`, `metrics`, 重复 `pipeline_id`, `max_samples`, `output_dir`, `device` | 新 tmp run，复制后的 predictions 和新 metric artifacts | `evaluation_only=true`，`old_evaluation_reused=false`，report pipeline ID 与请求 route 一致 |

## 输入准备

### 模型发现输入

当用户有模型链接、provider 查询词或 curated source 时，使用 `/sure_feed`。

支持的输入形式：

```text
/sure_feed https://huggingface.co/<owner>/<model>
/sure_feed https://www.modelscope.cn/models/<owner>/<model>
/sure_feed source=huggingface query="english asr" max_models=20
/sure_feed source=modelscope query="speech recognition" max_models=20
```

输出的 `model_input.yaml` 是 `/sure_onboard` 唯一的标准输入。

### Onboarding 的 MODEL_INPUT

`/sure_onboard` 可以从 feed handoff 启动：

```text
/sure_onboard model=<model_name>
```

也可以显式指定 YAML：

```text
/sure_onboard model_input_path=sure/handoffs/<model_name>/model_input.yaml
```

接入输入应能解析这些字段：

| 字段组 | 期望内容 |
| --- | --- |
| identity | model id、规范化 model name、source URL 或 local path |
| task | ASR、TTS、VC、KWS、S2TT、SD、SA-ASR、SLU、SER、GR 等 SURE 任务族 |
| weights | HuggingFace、ModelScope、local、API、pip、release/PyPI 来源 |
| environment | Python 版本、backend 偏好、依赖证据 |
| entrypoints | import/load/infer/contract surface 或 runtime strategy |
| fixture | task registry 中的小型 smoke sample，不是 benchmark 证据 |
| IO contract | 输入角色和 prediction 输出形态 |

### Evaluation 输入

`/sure_eval` 评估已接入模型：

```text
/sure_eval model=<model_name> datasets=<dataset> metrics=<metric> max_samples=5 execution=local
```

关键字段：

| 字段 | 含义 |
| --- | --- |
| `model` | `sure/models/<model>/` 下已有 ready `verdict.json` 的模型名。 |
| `datasets` | 数据集名或别名。任务和语言由 dataset JSONL 元数据决定。 |
| `metrics` | 标准报告指标，例如 `cer`, `wer`, `spk_sim`, `dnsmos`。 |
| `max_samples` | 有界样本数。`0` 或省略表示全量数据集。 |
| `execution` | `local`, `vc`, `auto`。`vc` 必须真实提交 VC job，不允许静默 fallback。 |
| `device` | `auto`, `cpu`, `cuda`, `cuda:<index>`。 |

### Re-evaluation 输入

`/sure_reval` 接受已有 source：

| Source Kind | 含义 |
| --- | --- |
| `results_dir` | 镜像 results 目录，包含 `predictions/`、`report.jsonl`，通常也有 `protocol.yaml`。 |
| `run_dir` | 标准 evaluation run 目录，包含 `predictions/`。 |
| `predictions_dir` | 裸 `<dataset>.txt` 和可选 `<dataset>.jsonl` 目录；当无法推断元数据时需要显式传 `model` 和 `datasets`。 |

指标选择模式：

| 模式 | 使用场景 | 示例 |
| --- | --- | --- |
| `metrics` | 使用当前 reported metric 的默认 route。 | `metrics=cer` |
| `pipeline_id` | 精确选择 route variant、normalizer、transcriber 或 scorer。 | `pipeline_id=asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1` |

可以重复传 `pipeline_id` 对比同一 metric 的多条链路。Harness 会使用 `metric__pipeline_id` 写入独立 metric 目录，结果不会互相覆盖。

## 输出契约

### Feed 输出

| Artifact | 含义 |
| --- | --- |
| `model_input.yaml` | 标准 onboarding 输入。 |
| `feed_report.json` | 面向用户的发现总结。 |
| `scan_result.json` | provider 原始候选。 |
| `match_task_result.json` | 任务匹配证据。 |
| `metadata_result.json` | repo、weights、依赖、entrypoint 证据。 |
| `rank_select_result.json` | 选中模型和排序原因。 |
| `handoff_manifest.json` | 跨 skill handoff 审计 manifest。 |

### Onboard 输出

| Artifact | 含义 |
| --- | --- |
| `model.py` / wrapper files | 可运行本地 adapter。 |
| `model.spec.yaml` | 模型身份、任务、IO contract、运行时元数据。 |
| `fixture_manifest.json` | 用于 validation 的 smoke fixture。 |
| `import_result.json`, `load_result.json`, `infer_result.json`, `contract_result.json` | 运行时校验阶段。 |
| `verdict.json` | 被 `/sure_eval` 消费的最终 readiness 判断。 |
| `artifact_manifest.json` | 接入产物索引。 |

### Eval 输出

| Artifact | 含义 |
| --- | --- |
| `predictions/<dataset>.txt` | scoring 使用的 key-tab-prediction 文件。 |
| `predictions/<dataset>.jsonl` | 结构化 prediction rows。 |
| `validation_payload.json` | missing/extra/duplicate/empty prediction 检查。 |
| `evaluation_route_plan.json` | engine commit、selected routes、pipeline IDs、nodes、环境 readiness。 |
| `evaluation_payload.json` | 机器可读 dataset-metric 结果。 |
| `metrics/<dataset>/<metric_slug>/report.json` | 独立 engine 的 metric report。 |
| `metrics/<dataset>/<metric_slug>/pipeline_description.json` | 选中 pipeline identity 和 node chain。 |
| `sample_reports/<dataset>/<metric_slug>.jsonl` | 可用时的 per-sample 细节。 |
| `main_agent_run_report.json` | 最终 run provenance、execution path、sample scope 和 artifacts。 |

### Reval 输出

| Artifact | 含义 |
| --- | --- |
| `prediction_source_resolved.json` | source kind、模型/数据集推断、prediction 文件路径。 |
| `prediction_reuse_manifest.json` | 复制/过滤后的 prediction 文件、样本数、source/destination hash。 |
| `validation_payload.json` | 请求样本范围内的 copied predictions 校验。 |
| `evaluation_route_plan.json` | engine commit 和 selected re-evaluation routes。 |
| `evaluation_payload.json` | 新 metric 结果。旧 metric payload 不复用。 |
| `metrics/<dataset>/<metric__pipeline_id>/report.json` | exact pipeline route 的新 metric report。 |
| `reval_run_report.json` | 面向用户的重评估总结和对比表。 |

重要 `reval_run_report.json` 字段：

| 字段 | 期望含义 |
| --- | --- |
| `evaluation_only` | 必须是 `true`。 |
| `old_evaluation_reused` | 必须是 `false`。 |
| `pipeline_ids` | 用户请求的 exact pipeline IDs。使用 `metrics` 模式时为空。 |
| `summary.comparisons[].pipelines[]` | 每条 pipeline 的 `pipeline_id`、有序 `nodes` 和 `score`。 |
| `summary.comparisons[].score_spread` | 当前 dataset/metric 分组中最大分数与最小分数的差。 |
| `artifacts` | 本次生成的 run artifacts 路径。 |

## 验收清单

### Onboarding

- `sure/models/<model>/verdict.json` 存在。
- 如果实际只通过 CPU fallback，不应声称 GPU ready，除非 device policy 记录了 fallback。
- `model.py` 或等价 wrapper 存在，并被 `model.spec.yaml` 引用。
- `import/load/infer/contract` validation artifacts 存在并通过。

### Evaluation

- `main_agent_run_report.json` 存在，并记录 `execution_path_requested` 和 `execution_path_actual`。
- `execution=vc` 时必须有 VC submission evidence，不允许静默 local fallback。
- `validation_payload.json` 中 `is_valid=true`。
- `evaluation_route_plan.json` 记录 `sure-evaluation` engine commit。
- 每个结果都有 `report.json`、`pipeline_description.json` 和 sample report artifact。

### Re-evaluation

- `main_agent_run_report.json` 说明没有启动 model server，也没有运行 inference。
- `reval_run_report.json` 中 `evaluation_only=true`。
- `reval_run_report.json` 中 `old_evaluation_reused=false`。
- 有界运行时，`prediction_reuse_manifest.imported[].imported_samples` 与 `max_samples` 一致。
- `evaluation_payload.results[].pipeline_id == pipeline_description.pipeline_id == report.pipeline_id`。
- `evaluation_payload.results[].nodes` 与 `report.pipeline_trace` 一致。
- 同一 metric 的多个 exact pipeline 会写入不同 artifact 目录。

## Route 和 Pipeline ID

用独立 engine 查看可用 route：

```bash
cd sure/external/sure-evaluation
sure-eval metric describe asr --language zh --metric cer --json
sure-eval metric describe asr --pipeline-id asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1 --json
sure-eval agent plan asr --language zh --metric cer --json
```

engine 也提供机器可读目录：

```text
sure/external/sure-evaluation/docs/pipeline_catalog.jsonl
sure/external/sure-evaluation/docs/pipeline_catalog.md
```

当同一个 reported metric 有多个 route variants 时，应使用 exact `pipeline_id`，不要发明新的 metric 名。

## 常见失败

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| `/sure_feed` 找不到模型 | 网络/provider 问题或 query 太窄 | 使用直接 URL，或扩大 query 后重跑。 |
| `/sure_onboard` 卡在 model input | repo、weights、fixture 或 IO contract 缺失 | 修复 handoff，或显式传 `model_input_path`。 |
| `/sure_eval` 无法解析 dataset | benchmark JSONL root 缺失 | 软链 `data/datasets/sure_benchmark/jsonl` 或设置 `SURE_EVAL_DATASETS_ROOT`。 |
| `/sure_eval execution=vc` 失败 | VC CLI 不可用或提交失败 | 修复 VC 权限/资源；harness 不会静默 fallback。 |
| `/sure_reval` 无法从裸 predictions 目录推断元数据 | source 附近没有 report/protocol | 显式传 `model=<name>` 和 `datasets=<dataset>`。 |
| exact `pipeline_id` 失败 | route 节点环境缺失 | 按 `evaluation_route_plan.json` 中的 setup 提示准备对应 node 环境。 |
