# SURE-EVAL 架构文档

## 重构后的架构

### 核心原则

1. **单一评估标准**: 所有评估都通过 `SUREEvaluator` 进行，确保结果一致性
2. **统一数据格式**: JSONL 格式包含 `key`, `path`, `target`, `task`, `language`, `dataset`
3. **配置-数据映射**: 配置文件使用友好名称，`dataset` 字段建立映射
4. **模块化设计**: 各模块职责清晰，通过公共接口交互

---

## 模块架构

```
sure-eval/
├── src/sure_eval/
│   ├── __init__.py                 # 公共接口导出
│   ├── cli.py                      # 命令行接口
│   ├──
│   ├── agent/
│   │   ├── __init__.py
│   │   └── evaluator.py            # AutonomousEvaluator - 自动化流程编排
│   │                               #   - 调用 DatasetManager 加载数据
│   │                               #   - 调用 Tool 进行推理
│   │                               #   - 调用 SUREEvaluator 进行评估
│   │                               #   - 调用 RPSManager 计算RPS
│   │
│   ├── core/
│   │   ├── config.py               # 配置管理
│   │   └── logging.py              # 日志系统
│   │
│   ├── datasets/
│   │   ├── __init__.py
│   │   └── dataset_manager.py      # DatasetManager - 统一数据管理
│   │                               #   - 下载 SURE Benchmark (CLI方式)
│   │                               #   - 下载 HuggingFace/ModelScope
│   │                               #   - CSV → JSONL 转换
│   │                               #   - 路径映射和解析
│   │
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── sure_evaluator.py       # SUREEvaluator - 唯一评估标准
│   │   ├── wenet_compute_cer.py    # WER/CER计算核心
│   │   ├── normalization/          # 文本归一化
│   │   ├── metrics.py              # 基础指标（备用）
│   │   └── rps.py                  # RPS计算
│   │
│   ├── models/                     # 模型管理
│   └── tools/                      # 工具管理
│
├── scripts/
│   ├── convert_sure_to_jsonl.py    # CSV转JSONL（独立脚本）
│   ├── run_sure_evaluation.py      # 评估运行（独立脚本）
│   └── download_sure_data.py       # 数据下载（独立脚本）
│
└── config/
    └── default.yaml                # 配置文件（匹配实际数据命名）
```

---

## 数据流

### 1. 数据准备流程

```
CSV (SURE Benchmark)
    ↓
DatasetManager.download_and_convert()
    - 使用 modelscope CLI 下载
    - 解压 tar.gz
    - CSV → JSONL 转换
    - 路径映射修复
    ↓
JSONL (统一格式)
    {
        "key": "BAC009S0764W0121",
        "path": "aishell-1_test/BAC009S0764W0121.wav",
        "target": "甚至出现交易几乎停滞的情况",
        "task": "ASR",
        "language": "zh",
        "dataset": "aishell1"  // 配置名称
    }
```

### 2. 评估流程

```
AutonomousEvaluator.evaluate_tool()
    ↓
DatasetManager.get_jsonl_path() 或 download_and_convert()
    ↓
_load_samples()  →  JSONL 加载
    ↓
_run_tool()       →  调用 Tool 进行推理
    ↓
_save_predictions() →  保存预测结果 (key\tpred)
    ↓
SUREEvaluator.evaluate()  →  标准评估
    - 文本归一化
    - WER/CER 计算
    ↓
RPSManager.calculate()  →  RPS 分数
    ↓
EvaluationResult
```

### 3. 独立评估脚本流程

```
run_sure_evaluation.py
    ↓
加载 JSONL (GT)
加载 TXT (Predictions)
    ↓
SUREEvaluator.evaluate()
    ↓
输出结果
```

---

## 关键类说明

### DatasetManager

```python
# 获取数据集路径
jsonl_path = manager.get_jsonl_path("aishell1")  # 支持配置名或CSV名

# 检查可用性
if manager.is_available("aishell1"):
    ...

# 下载并转换
jsonl_path = manager.download_and_convert("aishell1")

# 列出可用数据集
available = manager.list_available()

# 获取信息
info = manager.get_info("aishell1")
# {
#     "name": "aishell1",
#     "csv_name": "aishell1-test_ASR",
#     "config_name": "aishell1",
#     "task": "ASR",
#     "language": "zh",
#     "source": "sure_benchmark",
#     "is_available": True
# }
```

### SUREEvaluator

```python
# 创建评估器
evaluator = SUREEvaluator(language="zh")

# 评估 ASR
result = evaluator.evaluate("ASR", ref_file, hyp_file)
# 返回: {"all": 100, "cor": 95, "sub": 3, "del": 1, "ins": 1, "wer": 0.05, "wer_percent": 5.0}

# 评估 SER
accuracy = evaluator.evaluate("SER", ref_file, hyp_file)

# 评估 S2TT
result = evaluator.evaluate("S2TT", ref_file, hyp_file)
# 返回: {"bleu": 45.2, "chrf": 52.1}
```

### AutonomousEvaluator

```python
# 创建评估器
evaluator = AutonomousEvaluator(config)

# 评估工具
result = evaluator.evaluate_tool("asr_qwen3", "aishell1", max_samples=100)
# 返回: EvaluationResult

# 批量评估
results = evaluator.batch_evaluate("asr_qwen3", ["aishell1", "librispeech_clean"])

# 工具对比
comparison = evaluator.compare_tools(["asr_qwen3", "asr_whisper"], "aishell1")
```

---

## 配置映射

### 配置文件 (default.yaml)

```yaml
datasets:
  definitions:
    aishell1:              # 配置名称（友好名称）
      name: "AISHELL-1"
      task: "ASR"
      language: "zh"
      source: "sure_benchmark"
      dataset_id: "SUREBenchmark/SURE_Test_csv/aishell1-test_ASR.csv"
      subset: "aishell-1_test"  # 实际音频目录名
      num_samples: 7176
```

### DatasetManager 映射表

```python
CSV_DATASETS = {
    "aishell1-test_ASR": {           # CSV 文件名
        "config_name": "aishell1",    # 配置名称
        "audio_dir": "aishell-1_test", # 实际音频目录
        "task": "ASR",
        "language": "zh",
        "path_mappings": {
            "aishell-1-test/": "aishell-1_test/",  # CSV路径 → 实际路径
        },
    },
    ...
}
```

---

## 使用示例

### 1. 独立评估（已有预测结果）

```bash
python scripts/run_sure_evaluation.py \
    --gt data/datasets/sure_benchmark/jsonl/aishell1-test_ASR.jsonl \
    --pred predictions/aishell1.txt \
    --task ASR \
    --language zh
```

### 2. Agent 完整流程

```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()
result = evaluator.evaluate_tool("asr_qwen3", "aishell1", max_samples=100)

print(f"Score: {result.score}")
print(f"RPS: {result.rps}")
print(f"Details: {result.details}")
```

### 3. 数据管理

```python
from sure_eval import DatasetManager

manager = DatasetManager()

# 下载并转换
manager.download_and_convert("aishell1")

# 获取路径
jsonl_path = manager.get_jsonl_path("aishell1")

# 加载样本
with open(jsonl_path) as f:
    for line in f:
        sample = json.loads(line)
        # sample: {"key", "path", "target", "task", "language", "dataset"}
```

---

## 路径约定

### 数据目录结构

```
data/datasets/
└── sure_benchmark/
    ├── SURE_Test_csv/              # CSV 标注文件
    │   ├── aishell1-test_ASR.csv
    │   └── ...
    ├── SURE_Test_Suites/           # 音频文件
    │   ├── aishell-1_test/
    │   ├── librispeech-test-clean/
    │   └── ...
    └── jsonl/                      # 转换后的 JSONL
        ├── aishell1-test_ASR.jsonl
        └── ...
```

### JSONL 路径格式

- `path` 字段是相对于 `SURE_Test_Suites` 的相对路径
- 例如: `"aishell-1_test/BAC009S0764W0121.wav"`
- Agent 会自动拼接完整路径

---

## 注意事项

1. **评估一致性**: 所有评估都通过 `SUREEvaluator`，确保与 evaluation-pipeline 结果一致
2. **数据格式**: JSONL 必须包含 `key` 字段，用于预测文件匹配
3. **预测文件格式**: `key\tprediction`，与原始 evaluation-pipeline 兼容
4. **路径映射**: CSV 中的路径会自动转换为实际路径
5. **配置名称**: 可以使用配置名（`aishell1`）或 CSV 名（`aishell1-test_ASR`）
