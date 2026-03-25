# SURE Benchmark 数据下载指南

## 概述

本指南说明如何下载 SURE Benchmark 数据集，包含：
- **SURE_Test_csv**: 标注文件（CSV格式，约 50MB）
- **SURE_Test_Suites**: 音频文件（约 11GB）

## 快速开始

### 1. 一键下载

```bash
cd /cpfs/user/jingpeng/workspace/sure-eval
python scripts/download_sure_data.py
```

### 2. 仅下载标注文件

```bash
python scripts/download_sure_data.py --csv
```

### 3. 仅下载音频文件

```bash
python scripts/download_sure_data.py --suites
```

### 4. 自定义数据目录

```bash
python scripts/download_sure_data.py --data-dir /path/to/data
```

## 详细步骤（手动）

如果脚本无法使用，可以手动执行：

### 安装依赖

```bash
pip install modelscope
```

### 下载标注文件（CSV）

```bash
modelscope download \
    --dataset SUREBenchmark/SURE_Test_csv \
    --local_dir ./data/datasets/sure_benchmark/SURE_Test_csv
```

### 下载音频文件

```bash
modelscope download \
    --dataset SUREBenchmark/SURE_Test_Suites \
    --local_dir ./data/datasets/sure_benchmark/SURE_Test_Suites
```

### 解压音频文件

```bash
cd ./data/datasets/sure_benchmark/SURE_Test_Suites

for f in *.tar.gz; do
    mkdir -p "${f%.tar.gz}"
    tar -xzf "$f" -C "${f%.tar.gz}"
done
```

## 数据目录结构

下载完成后，目录结构如下：

```
data/datasets/sure_benchmark/
├── SURE_Test_csv/                    # 标注文件
│   ├── aishell1-test_ASR.csv         # AISHELL-1 测试集 (7,175条)
│   ├── librispeech_test-clean_ASR.csv # LibriSpeech Clean
│   ├── IEMOCAP_SER_test.csv          # 情感识别
│   ├── CoVoST2_S2TT_en2zh_test.csv   # S2TT EN->ZH
│   ├── CoVoST2_S2TT_zh2en_test.csv   # S2TT ZH->EN
│   ├── kespeech.csv                  # KeSpeech
│   └── ... (共14个CSV文件)
│
└── SURE_Test_Suites/                 # 音频文件
    ├── aishell-1_test/               # 7,175 files (1.08 GB)
    ├── aishell-5_test/               # AISHELL-5
    ├── librispeech-test-clean/       # 2,619 files (0.58 GB)
    ├── librispeech-test-other/       # 2,939 files (0.57 GB)
    ├── CoVoST2_S2TT_en2zh_test/      # 15,530 files (2.65 GB)
    ├── CoVoST2_S2TT_zh2en_test/      # 4,898 files (0.88 GB)
    ├── CS-Dialogue_test/             # 代码切换对话
    ├── IEMOCAP_test/                 # 情感识别
    ├── kespeech_test/                # KeSpeech
    ├── mmsu_reasoning_test/          # MMSU 推理
    └── voxpopuli_en_test/            # VoxPopuli
```

## 使用数据

### Python API

```python
from sure_eval.datasets.sure_benchmark import SUREBenchmarkDownloader

downloader = SUREBenchmarkDownloader()

# 列出所有子集
subsets = downloader.list_subsets('SURE_Test_csv')
print(subsets)  # ['aishell1-test_ASR', 'librispeech_test-clean_ASR', ...]

# 加载 AISHELL-1 样本
samples = downloader.load_subset('SURE_Test_csv', 'aishell1-test_ASR')

# 样本格式
{
    "path": "aishell-1-test/BAC009S0764W0121.wav",
    "target": "甚至出现交易几乎停滞的情况",
    "task": "ASR",
    "language": "zh",
    "dataset": "aishell1-test_ASR"
}
```

### 验证数据完整性

```python
python scripts/download_sure_data.py --verify
```

## 数据集统计

| 数据集 | 样本数 | 大小 | 任务 | 语言 |
|--------|--------|------|------|------|
| aishell-1_test | 7,175 | 1.08 GB | ASR | 中文 |
| aishell-5_test | - | 1.3 GB | ASR | 中文 |
| librispeech-test-clean | 2,619 | 0.58 GB | ASR | 英文 |
| librispeech-test-other | 2,939 | 0.57 GB | ASR | 英文 |
| CoVoST2_S2TT_en2zh_test | 15,530 | 2.65 GB | S2TT | EN->ZH |
| CoVoST2_S2TT_zh2en_test | 4,898 | 0.88 GB | S2TT | ZH->EN |
| IEMOCAP_test | - | 215 MB | SER | 英文 |
| kespeech_test | - | 2.7 GB | ASR | 中文 |
| CS-Dialogue_test | - | 1.2 GB | ASR | 中英混合 |
| mmsu_reasoning_test | 2,416 | 0.69 GB | SLU | 中文 |
| voxpopuli_en_test | - | 584 MB | ASR | 英文 |

**总计**: ~40,000+ 音频文件，~11GB 数据

## 常见问题

### 1. 下载速度慢

使用国内镜像加速：
```bash
export MODELSCOPE_CACHE=./data/cache
modelscope download --dataset SUREBenchmark/SURE_Test_Suites ...
```

### 2. 磁盘空间不足

音频文件约需 15GB 空间（包含压缩包和解压后）。

### 3. ModelScope 命令不存在

```bash
pip install modelscope
```

### 4. 只想下载部分数据集

手动下载特定 tar.gz 文件后解压：
```bash
# 仅 AISHELL-1
cd data/datasets/sure_benchmark/SURE_Test_Suites
modelscope download --dataset SUREBenchmark/SURE_Test_Suites aishell-1_test.tar.gz
tar -xzf aishell-1_test.tar.gz
```

## 参考链接

- ModelScope 数据集主页: https://www.modelscope.cn/datasets/SUREBenchmark
- SURE_Test_csv: https://www.modelscope.cn/datasets/SUREBenchmark/SURE_Test_csv
- SURE_Test_Suites: https://www.modelscope.cn/datasets/SUREBenchmark/SURE_Test_Suites
