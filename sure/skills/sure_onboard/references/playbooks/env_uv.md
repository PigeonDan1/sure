# UV 环境策略 Playbook

## 何时使用 UV

| 条件 | 使用 UV |
|------|---------|
| 纯 Python 依赖 | ✅ 推荐 |
| 主要依赖为 PyPI 包 | ✅ 推荐 |
| 无复杂 C++ 扩展 | ✅ 推荐 |
| 无系统库依赖 | ✅ 推荐 |
| 无 CUDA 编译需求 | ✅ 推荐 |

## 环境创建方式

```bash
cd sure/models/{model_name}

# 创建环境
uv venv --python=python3.10

# 激活环境
source .venv/bin/activate

# 安装依赖
uv pip install -r requirements.txt
# 或
uv pip install -e .
```

## Lock/Sync 约定

```bash
# 导出精确依赖
uv pip freeze > requirements.lock

# 从 lock 恢复
uv pip install -r requirements.lock
```

## 常见失败和修复

### 1. 系统 PyTorch 与虚拟环境冲突

**症状**: `ModuleNotFoundError: No module named 'torch._utils'`

**原因**: UV 环境隔离导致无法访问系统已安装的 PyTorch

**修复**:
```bash
# 在虚拟环境中重新安装 PyTorch
uv pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cpu
```

### 2. NumPy 版本冲突

**症状**: `AttributeError: np.sctypes was removed in the NumPy 2.0`

**原因**: NeMo 等库不支持 NumPy 2.0

**修复**:
```bash
uv pip install numpy==1.26.4
```

### 3. Torchvision 版本不匹配

**症状**: `RuntimeError: operator torchvision::nms does not exist`

**修复**:
```bash
# 安装与 PyTorch 匹配的 torchvision
uv pip install torchvision==0.19.0 --index-url https://download.pytorch.org/whl/cpu
```

## 集群网络限制

### 常见环境约束

在部分集群环境中，以下网络限制可能影响模型 onboarding：

- **HuggingFace 被封锁**：无法访问 `https://huggingface.co`，必须使用 **ModelScope** 作为模型/数据下载源。
- **PyTorch CDN 被封锁**：`https://download.pytorch.org` 可能无法访问，安装 torch 时需使用清华镜像：
  ```bash
  uv pip install torch==2.4.0 --index-url https://pypi.tuna.tsinghua.edu.cn/simple
  ```
- **torchvision 版本兼容性**：安装依赖后必须验证 torchvision 与 torch 版本匹配：
  ```bash
  .venv/bin/python -c "import torch, torchvision; print(f'torch {torch.__version__} + torchvision {torchvision.__version__}')"
  ```
  若 torchvision 主版本号与 torch 不匹配（例如 torch 2.4.0 + torchvision 0.26.0），必须降级 torchvision 到匹配版本。参考对应关系：
  | torch | torchvision |
  |-------|-------------|
  | 2.4.0 | 0.19.0 |
  | 2.5.0 | 0.20.0 |
  | 2.6.0 | 0.21.0 |

### 验证清单（onboarding 后必做）

```bash
# 1. 验证 torchvision 兼容性
.venv/bin/python -c "import torch, torchvision; tv_major = int(torchvision.__version__.split('.')[1]); torch_major = int(torch.__version__.split('.')[1]); assert tv_major == torch_major - 5, f'torchvision {torchvision.__version__} incompatible with torch {torch.__version__}'"

# 2. 验证 ModelScope 可达
.venv/bin/python -c "from modelscope.hub.api import HubApi; api = HubApi(); print('ModelScope OK')"

# 3. 验证模型可加载（不触发下载）
.venv/bin/python -c "from model import ModelWrapper; m = ModelWrapper(); m.load(); print('Model load OK')"
```

## 验证命令

```bash
# 验证 Python 版本
.venv/bin/python --version

# 验证关键包
.venv/bin/python -c "import torch; print(torch.__version__)"
.venv/bin/python -c "import numpy; print(numpy.__version__)"
```
