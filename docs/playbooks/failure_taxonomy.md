# 失败分类体系 (Failure Taxonomy)

第一阶段将失败归纳为以下类别，每类包含典型症状、常见证据和推荐修复方向。

## 1. python_dependency_missing

**典型症状**:
```
ModuleNotFoundError: No module named 'nemo'
ImportError: cannot import name 'ASRModel'
```

**常见证据**:
- `pip list` 缺少预期包
- `requirements.txt` 未正确安装
- 包版本不匹配

**推荐修复方向**:
```bash
# 重新安装依赖
uv pip install -r requirements.txt
# 或
pip install nemo-toolkit[asr]
```

## 2. system_dependency_missing

**典型症状**:
```
OSError: libsndfile.so.1: cannot open shared object file
RuntimeError: ffmpeg not found
```

**常见证据**:
- 系统库文件缺失
- apt/yum 未安装

**推荐修复方向**:
```bash
# Ubuntu/Debian
sudo apt-get install -y libsndfile1 ffmpeg

# Conda 环境
conda install -c conda-forge libsndfile
```

## 3. cuda_version_mismatch

**典型症状**:
```
RuntimeError: CUDA error: no kernel image is available
UserWarning: CUDA initialization: The NVIDIA driver is too old
```

**常见证据**:
- `torch.version.cuda` 与 `nvidia-smi` 不匹配
- PyTorch CUDA 版本高于驱动支持

**推荐修复方向**:
```bash
# 降级 PyTorch 到匹配驱动的版本
uv pip install torch==2.0.0 --index-url https://download.pytorch.org/whl/cu118

# 或使用 CPU 版本
uv pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cpu
```

## 4. wrong_python_version

**典型症状**:
```
SyntaxError: invalid syntax (Python 3.8 遇到 3.10 语法)
TypeError: unsupported operand type (类型注解问题)
```

**常见证据**:
- Python 版本低于要求
- 类型注解语法不兼容

**推荐修复方向**:
```bash
# 重新创建指定版本环境
uv venv --python=python3.10
conda create -n env python=3.10
```

## 5. missing_weights

**典型症状**:
```
FileNotFoundError: model/pytorch_model.bin not found
OSError: nvidia/parakeet-tdt-0.6b-v2 does not exist
```

**常见证据**:
- 模型缓存目录为空
- HuggingFace 下载中断
- 路径配置错误

**推荐修复方向**:
```bash
# 重新下载
huggingface-cli download nvidia/parakeet-tdt-0.6b-v2

# 或设置本地路径
export MODEL_PATH=/path/to/local/model
```

## 6. wrong_entrypoint

**典型症状**:
```
AttributeError: 'ASRModel' object has no attribute 'transcribe'
TypeError: transcribe() got an unexpected keyword argument 'language'
```

**常见证据**:
- API 调用方式与模型不匹配
- 方法签名错误

**推荐修复方向**:
- 查阅官方文档确认入口点
- 调整 wrapper 方法签名

## 7. config_not_set

**典型症状**:
```
RuntimeError: DASHSCOPE_API_KEY not set
KeyError: 'MODEL_PATH'
```

**常见证据**:
- 环境变量未导出
- config.yaml 缺少必要字段

**推荐修复方向**:
```bash
export DASHSCOPE_API_KEY="your-key"
# 或写入 .env 文件
```

## 8. runtime_backend_incompatible

**典型症状**:
```
RuntimeError: operator torchvision::nms does not exist
AttributeError: np.sctypes was removed
RuntimeError: mentioning torchcodec during audio loading
```

**常见证据**:
- 库版本间不兼容
- NumPy 2.0 破坏性变更
- PyTorch/Torchvision 版本不匹配
- torchaudio 触发 torchcodec/CUDA 依赖链

**推荐修复方向**:
```bash
# 降级到兼容版本
uv pip install numpy==1.26.4
uv pip install torchvision==0.19.0 --index-url https://download.pytorch.org/whl/cpu
```

### Repair Hint: CPU-friendly torch audio models (e.g. Silero VAD)

When all of the following hold:
- `requires_gpu == false`
- the model is lightweight and CPU-friendly
- `torchaudio` is used mainly for audio I/O
- runtime error mentions `torchcodec`, `torchaudio load/save/info`, or CUDA shared libraries

Prefer the following repair order:
1. Switch to CPU-only PyTorch wheel index (https://download.pytorch.org/whl/cpu)
2. Use aligned torch / torchaudio versions instead of latest CUDA-enabled stack (e.g. 2.2.2+cpu)
3. Pin `numpy<2` if older PyTorch stack requires NumPy 1.x
4. Consider alternative audio I/O backend if the model logic itself does not depend on torchaudio internals

**Do NOT** default to a CUDA stack when the model spec explicitly says `requires_gpu: false`.

**参考**: [`src/sure_eval/models/snakers4_silero-vad/known_issues.yaml`](../../../src/sure_eval/models/snakers4_silero-vad/known_issues.yaml)

## 失败分类流程图

```
失败发生
    ↓
查看错误类型
    ↓
├── ImportError / ModuleNotFoundError
│   └── python_dependency_missing
│
├── OSError (共享库)
│   └── system_dependency_missing
│
├── RuntimeError (CUDA)
│   └── cuda_version_mismatch
│
├── SyntaxError / TypeError (语法)
│   └── wrong_python_version
│
├── FileNotFoundError (模型文件)
│   └── missing_weights
│
├── AttributeError / TypeError (API)
│   └── wrong_entrypoint
│
├── KeyError / RuntimeError (配置)
│   └── config_not_set
│
└── RuntimeError / AttributeError (运行时)
    └── runtime_backend_incompatible
```
