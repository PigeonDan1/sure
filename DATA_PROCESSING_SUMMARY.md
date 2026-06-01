# SURE Benchmark 数据处理与评估迁移总结

## 完成的工作

### 1. 评估模块迁移（从 evaluation-pipeline）

将 `/cpfs/user/jingpeng/workspace/evaluation-pipeline` 的完整评估功能迁移到 `sure-eval`：

#### 新增/修改文件

| 文件 | 说明 |
|------|------|
| `src/sure_eval/evaluation/normalization/` | 完整的文本归一化模块 |
| `src/sure_eval/evaluation/wenet_compute_cer.py` | WER/CER 计算（WeNet 风格） |
| `src/sure_eval/evaluation/sure_evaluator.py` | 统一评估器（支持所有任务） |
| `scripts/convert_sure_to_jsonl.py` | CSV → JSONL 转换脚本 |
| `scripts/run_sure_evaluation.py` | 评估运行脚本 |

#### 支持的任务

| 任务 | 评估指标 | 状态 |
|------|----------|------|
| ASR | WER/CER | ✓ |
| SER | Accuracy | ✓ |
| GR | Accuracy | ✓ |
| S2TT | BLEU/chrF2 | ✓ |
| SLU | Accuracy | ✓ |
| SD | DER | ✓ (需 meeteval) |
| SA-ASR | cpWER + DER | ✓ (需 meeteval) |

### 2. 数据格式转换（CSV → JSONL）

将 SURE Benchmark CSV 文件转换为 JSONL 格式：

**转换前 (CSV)**:
```csv
Audio:FILE,Text:LABEL
aishell-1-test/BAC009S0764W0121.wav,甚至出现交易几乎停滞的情况
```

**转换后 (JSONL)**:
```json
{"key": "BAC009S0764W0121", "path": "aishell-1_test/BAC009S0764W0121.wav", "target": "甚至出现交易几乎停滞的情况", "task": "ASR", "language": "zh", "dataset": "aishell1-test_ASR"}
```

#### 路径修复

CSV 中的路径与实际音频目录存在差异，转换脚本自动修复：

| CSV 路径前缀 | 实际目录 |
|-------------|----------|
| `aishell-1-test/` | `aishell-1_test/` |
| `librispeech_test-clean/` | `librispeech-test-clean/` |
| `IEMOCAP_SER_test/wav/` | `IEMOCAP_test/` |

### 3. 数据转换结果

```
============================================================
Conversion Summary
============================================================
CS_dialogue                             :   5956 samples
CoVoST2_S2TT_en2zh_test                 :  15531 samples
CoVoST2_S2TT_zh2en_test                 :   4898 samples
IEMOCAP_SER_test                        :   1241 samples
aishell-5_eval1                         :  20368 samples
aishell1-test_ASR                       :   7176 samples
contextasr_english                      :  70037 samples
contextasr_mandarin                     :  73257 samples
kespeech                                :  19723 samples
librispeech_test-clean_ASR              :   2619 samples
librispeech_test-other_ASR              :   2939 samples
librispeech_test_clean_GR               :   2620 samples
mmsu                                    :   2416 samples
voxpopuli_test                          :   1842 samples
------------------------------------------------------------
Total                                   : 230623 samples
============================================================
```

### 4. 评估结果一致性验证

使用 AISHELL-1 的 100 条样本进行验证：

| 评估系统 | all | cor | sub | del | ins | WER |
|---------|-----|-----|-----|-----|-----|-----|
| evaluation-pipeline | 1406 | 1406 | 0 | 0 | 0 | 0.00% |
| sure-eval | 1406 | 1406 | 0 | 0 | 0 | 0.00% |

**结果完全一致 ✓**

## 使用指南

### 1. 数据转换（CSV → JSONL）

```bash
# 转换所有 CSV 文件
python scripts/convert_sure_to_jsonl.py \
    --csv-dir data/datasets/sure_benchmark/SURE_Test_csv \
    --output-dir data/datasets/sure_benchmark/jsonl

# 转换单个文件
python scripts/convert_sure_to_jsonl.py \
    --csv data/datasets/sure_benchmark/SURE_Test_csv/aishell1-test_ASR.csv \
    --output aishell1.jsonl
```

### 2. 运行评估

```bash
# 评估单个数据集
python scripts/run_sure_evaluation.py \
    --gt data/datasets/sure_benchmark/jsonl/aishell1-test_ASR.jsonl \
    --pred predictions/aishell1.txt \
    --task ASR \
    --language zh

# 批量评估
python scripts/run_sure_evaluation.py \
    --gt-dir data/datasets/sure_benchmark/jsonl \
    --pred-dir predictions \
    --output results/sure_results.json
```

### 3. 预测文件格式

预测文件为 TXT 格式，每行包含 `key` 和 `prediction`，用制表符分隔：

```
BAC009S0764W0121	甚至出现交易几乎停滞的情况
BAC009S0764W0122	一二线城市虽然也处于调整中
```

## 项目结构

```
sure-eval/
├── data/datasets/sure_benchmark/
│   ├── SURE_Test_csv/          # CSV 标注文件
│   ├── SURE_Test_Suites/       # 音频文件
│   └── jsonl/                  # 转换后的 JSONL 文件
├── src/sure_eval/evaluation/
│   ├── normalization/          # 文本归一化模块
│   ├── wenet_compute_cer.py    # WER/CER 计算
│   ├── sure_evaluator.py       # 统一评估器
│   └── metrics.py              # 其他评估指标
└── scripts/
    ├── convert_sure_to_jsonl.py    # 数据转换脚本
    ├── run_sure_evaluation.py      # 评估运行脚本
    └── download_sure_data.py       # 数据下载脚本
```

## 依赖安装

```bash
# 基础依赖
pip install sacrebleu

# 可选依赖（用于 SD/SA-ASR 任务）
pip install meeteval
```

## 注意事项

1. **路径格式**: JSONL 中的 `path` 是相对于 `SURE_Test_Suites` 的相对路径
2. **文本归一化**: ASR 评估会自动应用文本归一化（数字转文字等）
3. **中文评估**: 中文 ASR 使用 CER（字符错误率）而非 WER
4. **代码切换**: CS_dialogue 数据集会计算 MER/WER/CER 三个指标
