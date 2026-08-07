# 评估引擎

SURE Harness 不直接实现 metric 逻辑。Metric 能力、route nodes、normalizer、精确
`pipeline_id` 选择、node-local 环境都来自 `sure-evaluation` submodule。

## 安装

Clone harness 时拉取 submodule：

```bash
git clone --recurse-submodules --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
```

已有 clone 可以初始化 submodule：

```bash
git submodule update --init --recursive
```

高级用户仍然可以指向另一个 checkout：

```bash
export SURE_EVALUATION_HOME=/path/to/sure-evaluation
```

装一次引擎的 Python 包——`sure-eval` CLI 就是它提供的,`npm install`
不会装:

```bash
pip install -e sure/external/sure-evaluation
```

可选依赖组和节点本地环境见引擎自己的文档:
`sure/external/sure-evaluation/` 下的 `docs/installation.md` 和
`docs/environment.md`。

配置后运行 doctor：

```bash
npm run sure:doctor
```

## 分支关系

Harness 分支 `harness-tui-agent` 通过 Git submodule 跟随公开 engine：

```text
https://github.com/PigeonDan1/sure-evaluation.git main
```

Submodule 路径是：

```text
sure/external/sure-evaluation
```

在修改 `/sure_eval`、`/sure_reval`、route 选择、normalization 假设或 pipeline 兼容性文档前，先同步并验证 submodule：

```bash
git submodule sync --recursive
git submodule update --remote --merge sure/external/sure-evaluation
npm run sure:doctor
```

验证通过后只提交更新后的 gitlink：

```bash
git add .gitmodules sure/external/sure-evaluation
git commit -m "chore(sure): bump sure-evaluation submodule"
```

不要把 engine 源文件 vendor 进父仓库。长期稳定的运行契约是每次 evaluation run 写出的证据：
`evaluation_route_plan.json` 必须记录本次使用的 engine path 和 commit。

## 数据集根目录

`/sure_eval` 和 `/sure_reval` 需要 benchmark JSONL 文件和配套音频。JSONL 默认位置是：

```text
data/datasets/sure_benchmark/jsonl
```

数据本身来自 ModelScope——下载和转换命令见用户指南的「Benchmark 数据」一节。
已经有现成 JSONL 的话直接软链：

```bash
mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
```

`SURE_EVAL_DATASETS_ROOT` 可以指向另一个包含 `sure_benchmark/jsonl` 的数据根，
但它只挪 JSONL 的查找位置：

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

音频永远按仓库根固定路径解析
（`data/datasets/sure_benchmark/SURE_Test_Suites/`），音频放在别处的话，
仓库里要留一条软链接指过去。

## Route 选择

当用户想使用某个 reported metric 的当前默认链路时，传 `metrics`：

```text
/sure_eval model=<model> datasets=<dataset> metrics=wer max_samples=5 execution=local
/sure_reval source=<run_dir> datasets=<dataset> metrics=wer max_samples=5
```

当用户需要精确 route variant 时，传 `pipeline_id`：

```text
/sure_reval source=<run_dir> datasets=<dataset> max_samples=5 pipeline_id=<exact_pipeline_id>
```

可以重复传 `pipeline_id=...` 对比同一 dataset 和 metric 的多条链路。Harness 会写入独立
metric artifact 目录，避免互相覆盖。

## 典型链路

| 任务 | 链路形态 |
| --- | --- |
| ASR 中文 CER | `normalization/wetext_norm -> scoring/wenet_cer` |
| TTS/VC 中文 CER | `frontend/funasr_loader_16k_mono -> transcription/paraformer_zh -> normalization/punctuation_strip_norm -> scoring/wenet_cer` |
| TTS 英文 WER | `transcription/whisper_large_v3 -> normalization/whisper_norm -> scoring/wenet_wer` |

这张表只用于帮助理解。真正的 source of truth 是本次运行选择的本地 `sure-evaluation`
engine。

## 输出证据

完成的 evaluation 或 re-evaluation 应能在这些 artifact 中看到实际 route：

| Artifact | 字段 |
| --- | --- |
| `evaluation_route_plan.json` | engine path、engine commit、请求的 metrics 或 pipeline IDs |
| `evaluation_payload.json` | `results[].pipeline_id`、`results[].nodes`、metric artifact directory |
| `metrics/<dataset>/<metric_slug>/pipeline_description.json` | 精确 route 元数据 |
| `metrics/<dataset>/<metric_slug>/report.json` | score 和 pipeline trace |

对于 `/sure_reval`，还要检查 `reval_run_report.json`：

```text
evaluation_only=true
old_evaluation_reused=false
```
