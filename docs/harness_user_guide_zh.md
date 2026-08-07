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

Harness 不替代指标引擎。metric 能力、route nodes、精确 `pipeline_id` 选择、node-local 环境检查都来自 `sure/external/sure-evaluation` 下的 `sure-evaluation` submodule，也可以用高级覆盖项 `SURE_EVALUATION_HOME` 指定其他 checkout。

## 从 0 到第一次成功运行

1. 克隆并安装 harness。

先备好：Node.js >= 22.19.0、git、Python >= 3.10（一句话版本见 README 快速开始）。

```bash
git clone --recurse-submodules --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
pip install -r requirements.txt
npm run sure:doctor
```

2. 准备指标引擎和 benchmark 数据。

```bash
git submodule update --init --recursive

pip install -e "sure/external/sure-evaluation[download]"
python sure/external/sure-evaluation/scripts/download_sure_data.py --csv
python sure/external/sure-evaluation/scripts/convert_sure_to_jsonl.py --csv-dir data/datasets/sure_benchmark/SURE_Test_csv --output-dir data/datasets/sure_benchmark/jsonl
npm run sure:doctor
```

音频档案按数据集单独下——来源、体量、解压校验、数据集命名见下面的
[Benchmark 数据](#benchmark-数据)一节。只有 `/sure_eval` 和 `/sure_reval`
用得到 benchmark 数据,这步可以等要出指标了再做。已经有现成 JSONL 的话
直接软链:`ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl`。

3. 启动 TUI。

```bash
./pi-test.sh --approve
```

启动不需要 API key,供应商和模型在下一步配。`--approve` 的作用是信任
本项目配置。

4. 初始化项目。

```text
/sure_init
```

`/sure_init` 负责选供应商和默认模型:内置供应商、`~/.pi/agent/models.json`
里已有的网关、或现场新建一个 OpenAI 兼容网关(名称、地址、API key)。
支持在线查询的供应商,模型列表实时拉取。成功后写 `.sure/init.json`
(所选供应商与模型、python 检查、发现的技能),并把当前会话切到所选
模型——它不跑 doctor。之后 `/model` 默认只列当前供应商的模型,按 Tab
切换范围。

非交互运行要传全参数:
`/sure_init --option <供应商id> --model <模型id> --api-key <key>`;
新建网关再加 `--name <名称> --base-url <地址>`。

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
sure/models/<model_name>/artifacts/verdict.json
sure/models/<model_name>/artifacts/
```

7. 评估已接入模型。

```text
/sure_eval model=<model_name> datasets=aishell1-test_ASR metrics=cer max_samples=5 execution=local device=auto
```

期望输出——`<eval_run_dir>` 默认是
`sure/models/<model>/eval_runs/<run_id>`,可用 `output_dir=` 覆盖：

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
/sure_reval source=<results_or_run_dir> datasets=aishell1-test_ASR max_samples=5 pipeline_id=asr.zh.cer.wetext_norm_zh_itn_v1.wenet_cer_v1 pipeline_id=asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1
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

## Benchmark 数据

评估真正要读的是两样东西:JSONL 标注和音频。CSV 只是原料,转完就用不上了。

哪些命令需要 benchmark 数据:

| 命令 | 是否需要 |
| --- | --- |
| `/sure_init`、`/sure_feed` | 否 |
| `/sure_onboard` | 否——用仓库自带的 `fixtures/` |
| `/sure_eval` | 是——所评数据集的 JSONL 加音频 |
| `/sure_reval` | 要的是已有 run 的 predictions,不是原始数据 |

### 来源与体量

两个仓库都在 ModelScope 公开(Apache-2.0,不用登录):

| 仓库 | 内容 | 体量 |
| --- | --- | --- |
| `SUREBenchmark/SURE_Test_csv` | 14 个标注 CSV | 约 34 MB |
| `SUREBenchmark/SURE_Test_Suites` | 12 个音频档案 | 共 52.5 GB |

档案从 225 MB(`IEMOCAP_test.tar.gz`)到 41.5 GB(`contextasr_test.tar.gz`)
不等。注意:转换脚本把 `contextasr_*` 两个数据集的音频指到了
`librispeech-test-clean/` 和 `aishell-1_test/`,41.5 GB 那个档案在这条
转换链路上读不到;跳过它,音频总量降到 11 GB 左右。磁盘要同时容下
压缩包和解压后的文件。

### 下载、解压、转换

```bash
pip install -e "sure/external/sure-evaluation[download]"
python sure/external/sure-evaluation/scripts/download_sure_data.py --csv
modelscope download --dataset SUREBenchmark/SURE_Test_Suites aishell-1_test.tar.gz --local_dir data/datasets/sure_benchmark/SURE_Test_Suites
cd data/datasets/sure_benchmark/SURE_Test_Suites && mkdir -p aishell-1_test && tar -xzf aishell-1_test.tar.gz -C aishell-1_test && cd -
python sure/external/sure-evaluation/scripts/convert_sure_to_jsonl.py --csv-dir data/datasets/sure_benchmark/SURE_Test_csv --output-dir data/datasets/sure_benchmark/jsonl
npm run sure:doctor
```

`download_sure_data.py --suites` 一次全下,最长跑两个小时,中间一个字都
不打印——想看进度就照上面的样子用 `modelscope download` 一个档案一个
档案地下。

解压完检查目录层级。期望结构:

```text
data/datasets/sure_benchmark/SURE_Test_Suites/aishell-1_test/<key>.wav
```

档案自带一层同名目录的话就会变成
`aishell-1_test/aishell-1_test/<key>.wav`,JSONL 里的相对路径全对不上。
把里层文件提上来一层就好。下载脚本末尾那行 `Total audio files` 只数第
一层——解压明明成功了这个数却是 0,多半就是多套了一层。

`npm run sure:doctor` 报 `PASS ...(14 jsonl files)` 只说明目录和文件名
摆对了——它不看文件内容,也不查音频。真正的证据是 `max_samples=5` 的
评估跑出 metric report。

### JSONL 行格式

每行六个字段:`key`、`path`、`target`、`task`、`language`、`dataset`。
`path` 是相对路径,运行时按这个顺序找音频:

```text
<repo_root>/data/datasets/sure_benchmark/SURE_Test_Suites/<path>
<repo_root>/<path>
```

JSONL 和音频必须同时就位。只有 JSONL 也能让 `npm run sure:doctor`
变 PASS,但推理阶段会因为找不到音频挂掉。

### 数据集命名

转出来的文件按 CSV 命名。`datasets=` 填文件名去掉 `.jsonl` 后缀:

| 命名形式 | 示例 | 自己下的数据能用吗 |
| --- | --- | --- |
| JSONL 文件名 | `aishell1-test_ASR` | 能用,推荐 |
| 代码里的短别名 | `aishell1` | 能用,但有副作用:音频没备齐时,短别名会触发一次 52.5 GB 的全量下载,不提示、不出进度 |
| 内部版本名 | `aishell1__v1.0.2__asr` | 不能用,这个名字只存在于内部共享数据目录 |

数据集和常用 metric 的对应:

| 数据集 | 任务 | metric |
| --- | --- | --- |
| `aishell1-test_ASR`、`aishell-5_eval1`、`kespeech`、`contextasr_mandarin` | 中文 ASR | `cer` |
| `librispeech_test-clean_ASR`、`librispeech_test-other_ASR`、`voxpopuli_test`、`contextasr_english` | 英文 ASR | `wer` |
| `CS_dialogue` | 中英混说 ASR | `mer` |
| `CoVoST2_S2TT_en2zh_test`、`CoVoST2_S2TT_zh2en_test` | S2TT | `bleu` |
| `IEMOCAP_SER_test`、`librispeech_test_clean_GR`、`mmsu` | SER / GR / SLU | 见引擎 catalog |

### 自定义数据根目录

`SURE_EVAL_DATASETS_ROOT` 只挪 JSONL 的查找位置——那个目录下必须有
`sure_benchmark/jsonl`。音频永远按上面写的仓库根固定路径解析,音频要
挪走的话,仓库里得留一条软链接指过去。

## 功能参考

| Command | 用途 | 必需输入 | 常见可选输入 | 期望输出 | 成功标准 |
| --- | --- | --- | --- | --- | --- |
| `/sure_init` | 配置 harness 项目和 agent 运行环境:选择供应商(内置服务、models.json 网关、或新建 OpenAI 兼容网关),并从供应商模型列表中选默认模型(支持在线查询则实时拉取,否则用内置目录)。非交互:`--option <id> --model <模型id>`;新建网关再加 `--name <名称> --base-url <地址> --api-key <key>`。 | 无 | 供应商/鉴权/模型配置 | `.sure/init.json`(所选供应商与模型、python 检查、发现的技能);会话切到所选模型 | hooks 发现技能与依赖 |
| `/sure_feed` | 发现模型并生成接入输入。 | 直接模型 URL 或 `source/query` | `max_models`, `max_retries`, `download`, `handoff_root` | `sure/handoffs/<model>/model_input.yaml`, feed artifacts | 选中模型有任务证据、repo、weights source、fixture、IO contract |
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
| `datasets` | 数据集名或别名。任务和语言由 dataset JSONL 元数据决定。`aishell1` 这类短别名只有在能唯一匹配到版本化数据集 ID 时才会展开。 |
| `metrics` | 标准报告指标，例如 `cer`, `wer`, `spk_sim`, `dnsmos`。 |
| `max_samples` | 有界样本数。`0` 或省略表示全量数据集。 |
| `execution` | `local`, `vc`, `auto`。`vc` 必须真实提交 VC job，不允许静默 fallback。 |
| `device` | `auto`, `cpu`, `cuda`, `cuda:<index>`。 |
| `vc_partition` | `execution=vc` 时指定 VC 分区;不传则自动选择。分区名不在可用范围内会在输入解析阶段直接报错并列出可用分区。 |
| `vc_gpu` / `vc_mem` / `vc_cpu` / `vc_image` / `vc_job_name` | 可选 VC 资源覆写:GPU 数、内存(GB)、CPU 数、Docker 镜像、作业名。 |

### Re-evaluation 输入

`/sure_reval` 接受已有 source：

| Source Kind | 含义 |
| --- | --- |
| `results_dir` | 镜像 results 目录，包含 `predictions/`、`report.jsonl`，通常也有 `protocol.yaml`。 |
| `run_dir` | 标准 evaluation run 目录，包含 `predictions/`。 |
| `predictions_dir` | 裸 `<dataset>.txt` 和可选 `<dataset>.jsonl` 目录；当无法推断元数据时需要显式传 `model` 和 `datasets`。 |

`/sure_reval` 会按和 `/sure_eval` 一致的规则解析 prediction source 中的数据集名：
优先精确匹配文件 stem；否则 `aishell1` 这类短名可以解析到唯一的版本化 prediction
stem，例如 `aishell1-test_ASR`（在内部共享数据目录上跑出来的 run 则是
`aishell1__v1.0.2__asr` 这种带版本的 stem）。如果短名存在歧义，则失败而不是猜测。

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
| `runtime_inventory.json` | 模型级 runtime provenance：backend、Python、weights 摘要、runtime probe、证据链接。 |
| `runtime_links/` | 只链接小型证据文件，不链接 checkpoint payload。 |

### Eval 输出

| Artifact | 含义 |
| --- | --- |
| `predictions/<dataset>.txt` | scoring 使用的 key-tab-prediction 文件。 |
| `predictions/<dataset>.jsonl` | 结构化 prediction rows。`raw_response` 是模型输出证据，不是超参数来源。 |
| `prediction_generation_status.json` | 真实推理 server command、环境 key snapshot、显式 tool args、protocol resolver 输出、dataset 生成状态。 |
| `protocol.yaml` | 只记录推理协议：模型、runtime、参数、prediction contract、reuse flag、provenance。 |
| `validation_payload.json` | missing/extra/duplicate/empty prediction 检查。 |
| `evaluation_route_plan.json` | engine commit、selected routes、pipeline IDs、nodes、环境 readiness。 |
| `evaluation_payload.json` | 机器可读 dataset-metric 结果。 |
| `metrics/<dataset>/<metric_slug>/report.json` | Submodule engine 的 metric report。 |
| `metrics/<dataset>/<metric_slug>/pipeline_description.json` | 选中 pipeline identity 和 node chain。 |
| `sample_reports/<dataset>/<metric_slug>.jsonl` | 可用时的 per-sample 细节。 |
| `report.jsonl` | 标准 per dataset-metric report 行。 |
| `report_snapshot.md` | 面向人类阅读的评估协议和结果快照。 |
| `main_agent_run_report.json` | 最终 run provenance、execution path、sample scope 和 artifacts。 |

### Reval 输出

| Artifact | 含义 |
| --- | --- |
| `prediction_source_resolved.json` | source kind、模型/数据集推断、prediction 文件路径。 |
| `prediction_reuse_manifest.json` | 复制/过滤后的 prediction 文件、样本数、source/destination hash。 |
| `source_inference_provenance.json` | 可用时链接源 `protocol.yaml`、`prediction_generation_status.json` 和 runtime inventory。 |
| `protocol.yaml` | 与 `/sure_eval` 相同的 inference-protocol 形态，且 `prediction_reuse.enabled=true`。 |
| `validation_payload.json` | 请求样本范围内的 copied predictions 校验。 |
| `evaluation_route_plan.json` | engine commit 和 selected re-evaluation routes。 |
| `evaluation_payload.json` | 新 metric 结果。旧 metric payload 不复用。 |
| `metrics/<dataset>/<metric__pipeline_id>/report.json` | exact pipeline route 的新 metric report。 |
| `report.jsonl` / `report_snapshot.md` | 全新机器可读和人类可读报告。 |
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

### 推理 Protocol 与 Provenance

`protocol.yaml` 只记录推理参数和 runtime evidence。Metric 选择、route
nodes、分数和样本级报告属于 `evaluation_route_plan.json`、`report.jsonl` 和
`report_snapshot.md`。

参数来源优先级：

1. `prediction_generation_status.json`
2. `sure/models/<model>/artifacts/runtime_inventory.json`
3. 模型 `config.yaml` protocol 设置
4. 当前进程环境兜底

对于 `/sure_reval`，`prediction_reuse.generation_policy` 必须是
`reused_predictions_no_inference`；如果来源 run 提供推理证据，则会建立链接。

## 验收清单

### Onboarding

- `sure/models/<model>/artifacts/verdict.json` 存在(顶层
  `sure/models/<model>/verdict.json` 位置也被接受)。
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

用 submodule engine 查看可用 route：

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
| 作业想投指定分区 | 未传 `vc_partition` 时由 harness 自动选分区 | `/sure_eval` 加 `vc_partition=<分区名>`;传错会当场报错并列出可用分区。 |
| `/sure_reval` 无法从裸 predictions 目录推断元数据 | source 附近没有 report/protocol | 显式传 `model=<name>` 和 `datasets=<dataset>`。 |
| exact `pipeline_id` 失败 | route 节点环境缺失 | 按 `evaluation_route_plan.json` 中的 setup 提示准备对应 node 环境。 |
