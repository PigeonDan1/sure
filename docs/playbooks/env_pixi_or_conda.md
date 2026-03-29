# Pixi/Conda 环境策略 Playbook

## 何时使用 Pixi/Conda

| 条件 | 使用 Pixi/Conda |
|------|-----------------|
| 需要特定 CUDA 版本 | ✅ 推荐 |
| 复杂 C++ 扩展（如 k2, warp-transducer） | ✅ 推荐 |
| 系统库依赖（如 libsndfile, sox） | ✅ 推荐 |
| 有 embedded submodule | ✅ 推荐 |
| 需要严格版本锁定 | ✅ 推荐 |

## Conda 环境创建

```bash
cd src/sure_eval/models/{model_name}

# 从 environment.yml 创建
conda env create -f environment.yml
conda activate {env_name}

# 或手动创建
conda create -n {env_name} python=3.10 -y
conda activate {env_name}
conda install pytorch==2.4.0 torchaudio==2.4.0 -c pytorch
```

## Pixi 环境创建

```bash
cd src/sure_eval/models/{model_name}

# 初始化
pixi init

# 添加依赖
pixi add python=3.10 pytorch=2.4.0 torchaudio=2.4.0

# 安装
pixi install
```

## CUDA / PyTorch / System Libs 约定

### CUDA 版本匹配

```yaml
# environment.yml 示例
name: model_env
channels:
  - pytorch
  - nvidia
  - conda-forge
dependencies:
  - python=3.10
  - pytorch=2.4.0
  - torchaudio=2.4.0
  - pytorch-cuda=12.1
  - cudatoolkit=11.8
```

### System Packages

```bash
# apt 依赖（Ubuntu/Debian）
sudo apt-get install -y libsndfile1 ffmpeg

# conda 中安装 system 等价物
conda install -c conda-forge libsndfile
```

## 常见失败和修复

### 1. CUDA 版本不匹配

**症状**: `CUDA error: no kernel image is available`

**修复**: 检查 PyTorch CUDA 版本与驱动版本匹配

```bash
python -c "import torch; print(torch.version.cuda)"
nvidia-smi
```

### 2. 子模块未初始化

**症状**: `ModuleNotFoundError: No module named 'pyannote'`

**修复**:
```bash
git submodule update --init --recursive
pip install -e ./submodule/
```

### 3. 编译错误

**症状**: `error: command 'gcc' failed`

**修复**:
```bash
conda install -c conda-forge gcc_linux-64 gxx_linux-64
```
