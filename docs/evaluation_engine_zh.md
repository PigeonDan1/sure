# 评估引擎

SURE Harness 不直接实现 metric 逻辑。Metric 能力、route nodes、normalizer、精确
`pipeline_id` 选择、node-local 环境都来自独立的 `sure-evaluation` checkout。

## 安装

Engine 应保持本地化，不进入 harness 仓库 Git 历史：

```bash
mkdir -p sure/external
git clone https://github.com/PigeonDan1/sure-evaluation.git sure/external/sure-evaluation
```

也可以指向另一个 checkout：

```bash
export SURE_EVALUATION_HOME=/path/to/sure-evaluation
```

配置后运行 doctor：

```bash
npm run sure:doctor
```

## 分支关系

Harness 分支 `harness-tui-agent` 应持续跟随公开的 engine 分支：

```text
git@github.com:PigeonDan1/sure-evaluation.git main
```

常规本地 checkout 位置是：

```text
sure/external/sure-evaluation
```

在修改 `/sure_eval`、`/sure_reval`、route 选择、normalization 假设或 pipeline 兼容性文档前，先同步 engine：

```bash
git -C sure/external/sure-evaluation fetch origin main
git -C sure/external/sure-evaluation merge --ff-only origin/main
npm run sure:doctor
```

不要把 engine checkout 提交进本仓库。长期稳定的契约是每次 evaluation run 写出的运行证据：
`evaluation_route_plan.json` 必须记录本次使用的 engine path 和 commit。

## 数据集根目录

`/sure_eval` 和 `/sure_reval` 需要 benchmark JSONL 文件。默认位置是：

```text
data/datasets/sure_benchmark/jsonl
```

常见本地设置：

```bash
mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
```

也可以通过环境变量指向包含 `sure_benchmark/jsonl` 的数据根目录：

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

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
