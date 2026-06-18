# 复现工作流

## 目的

`examples/reproduction/` 用于存放本地论文复现流程的轻量入口脚本和案例化脚本。

它不存放模型 wrapper，不存放大规模运行结果，也不承担模型接入职责。模型接入应放在 `src/sure_eval/models/` 下，长期保存的复现运行产物应放在 `runs/reproduction/` 下。

## 范围

本地复现流程覆盖：

1. 论文 claim 抽取
2. 模型就绪状态验证
3. 数据集子集或完整数据集准备
4. prediction 生成
5. 指标计算
6. 论文数值对比
7. 复现评估报告生成

该流程可以支持 subset trend check、partial reproduction 和 full reproduction。最终结论必须由数据规模、配置一致性、指标实现口径和方法差异共同决定。

## 目录职责

- `src/sure_eval/reproduction/`：通用复现 schema、对比 helper、就绪状态报告 helper 和 workflow 工具。
- `src/sure_eval/evaluation/`：共享指标实现、归一化和评估工具。
- `src/sure_eval/models/`：模型接入代码、server/wrapper 文件、验证脚本、onboarding artifacts、checkpoints 和 runtime cache。
- `examples/reproduction/`：本地复现流程的轻量入口脚本。
- `runs/reproduction/`：所有复现实验运行产物，统一放在 `runs/reproduction/<run_id>/` 下。
- `runs/paper_to_userspec/`：论文解析、claim 抽取、证据、confidence 和 user-spec 产物，统一放在 `runs/paper_to_userspec/<paper_id>/` 下。

模型目录只应包含模型访问代码、server 文件、wrapper、验证脚本、onboarding artifacts、checkpoints 和 runtime state。复现运行输出不应存放在模型目录中。

## 工作流概览

1. 解析论文并抽取 claims。
2. 构建或验证模型就绪状态。
3. 准备可复现的数据集子集或完整数据集。
4. 使用标准输出 schema 生成 predictions。
5. 验证 prediction 覆盖率和非空输出。
6. 通过共享指标层计算 metrics。
7. 将本地数值与论文数值进行对比。
8. 生成 assessment report。
9. 记录方法差异和失败原因。

## `workflow.py` 的职责

`src/sure_eval/reproduction/workflow.py` 当前提供与具体模型、具体数据集无关的通用复现 helper。它包含 metric direction helper、task reference-field routing、model readiness report、dataset readiness report、paper-vs-local comparison 和 JSON artifact 写出能力。

它负责共享状态组织、schema、就绪检查、对比逻辑和 artifact 组织。

## `metrics.py` 的职责

指标公式应集中在 `src/sure_eval/evaluation/metrics.py` 中。`examples/reproduction/` 下的脚本应调用共享指标函数，而不是在一次性脚本里重复实现同一个指标公式。

这样可以让 SURE-EVAL 的评价体系保持可审计、可复用，并且在不同复现 case 之间保持一致。

当前共享指标层包含通用 metric classes，以及 long-form ASR 相关 helper：

- `compute_asr_error_counts`
- `compute_wer`
- `compute_ier`
- `compute_five_dup`
- `compute_asr_longform_metrics`

WER 和 IER 应基于同一套 edit-distance alignment counts。重复统计类指标应在输出 JSON 中记录 counting definition，确保对比口径可审计。

## 复现运行目录结构

推荐目录结构：

```text
runs/reproduction/<run_id>/
├── run_config.json
├── predictions/
├── logs/
├── eval_payload.json
├── metric_results.json
├── paper_value_comparison.json
├── assessment_report.json
└── README.md
```

- `run_config.json`：记录数据集、模型、运行环境、seed、subset/full-run 选择和评估配置。
- `predictions/`：标准化 prediction 文件，以及可选的 raw prediction records。
- `logs/`：setup、validation、prediction 和 evaluation 日志。
- `eval_payload.json`：供下游工具使用的结构化本地评估 payload。
- `metric_results.json`：规范化 metric 数值、counts、normalization 和 metric definitions。
- `paper_value_comparison.json`：论文数值、本地数值、差距、状态和可比性说明。
- `assessment_report.json`：最终复现评估、失败信息、方法差异和下一步建议。
- `README.md`：面向人工阅读的运行摘要。

## 论文数值对比规则

论文数值必须来自论文证据或 claim extraction artifacts。本地数值必须来自真实 predictions 和真实指标计算。

不要手填或伪造 local results。如果 prediction 或 evaluation 失败，local value 必须为 `null`，并且报告中必须记录 `failure_type` 和 `failure_reason`。

## 方法差异记录策略

每次运行都应记录会影响可比性的方法差异：

- 数据集子集与完整数据集的差异
- 模型权重或版本差异
- 硬件差异
- batch size 和 decoding 配置差异
- normalization 和 metric implementation 差异
- 外部依赖或 gated weights 差异

## 指标可比性

指标可比性必须谨慎处理。

比例型指标可以用于 subset trend check，但小样本子集存在方差。raw count 型指标不能直接跨不同语料规模比较，除非进行了归一化或明确写出 caveat。long-form 指标不能自动等同于短 segment subset 的结果。

报告中应明确说明一次运行属于 trend check、partial reproduction 还是 full reproduction。

## 失败处理

运行失败时，也应尽可能写出结构化输出。失败记录应包含：

- `failed_stage`
- `failure_type`
- `failed_command`
- `log_path`
- `next_action`

常见 `failure_type` 包括：

- `dependency_missing`
- `dataset_download_failed`
- `missing_weights`
- `gpu_unavailable`
- `wrapper_contract_mismatch`
- `prediction_empty`
- `metric_computation_failed`

## 如何新增一个复现 case

1. 定义论文 claim 和目标 metric。
2. 决定运行是 subset trend check、partial reproduction 还是 full reproduction。
3. 通过 onboarding 和 validation artifacts 验证模型就绪状态。
4. 按标准 JSONL 格式准备数据集。
5. 使用标准输出 schema 生成 predictions。
6. 调用 `src/sure_eval/evaluation/` 中的共享 metrics。
7. 写出论文对比报告。
8. 将所有运行产物归档到 `runs/reproduction/<run_id>/` 下。

## 不要做什么

- 不要把 run artifacts 存放在 `src/sure_eval/models//eval_runs/` 下。
- 当共享指标已存在时，不要在一次性脚本里实现指标公式。
- 不要把 subset 结果报告成 full reproduction。
- 不要在没有归一化或 caveat 的情况下跨不同语料规模比较 raw count metrics。
- 不要在没有新 `run_id` 的情况下覆盖之前的运行结果。

注：本文基于 `src/sure_eval/reproduction/workflow.py`、`src/sure_eval/reproduction/schema.py`、`src/sure_eval/evaluation/metrics.py`、`src/sure_eval/models/README.md`、`src/sure_eval/models/AGENTS.md`、根目录 `README.md`、`src/sure_eval/agent/README.md`、`scripts/run_reproduction_workflow.py`，以及当前观察到的 `examples/reproduction/`、`runs/reproduction/` 和 `runs/paper_to_userspec/` 目录结构整理。
