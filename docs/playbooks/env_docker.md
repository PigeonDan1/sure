# Docker 环境策略 Playbook

## 何时必须容器化

| 条件 | 使用 Docker |
|------|-------------|
| 宿主机污染风险高 | ✅ 必须 |
| 依赖特定 OS 版本 | ✅ 必须 |
| 复杂系统库依赖 | ✅ 必须 |
| 多模型环境冲突 | ✅ 必须 |
| 需要可复现的完整环境 | ✅ 推荐 |

## Docker 基本要求

### Dockerfile 模板

```dockerfile
FROM nvidia/cuda:12.1-devel-ubuntu22.04

# 基础依赖
RUN apt-get update && apt-get install -y \
    python3.10 python3-pip \
    libsndfile1 ffmpeg \
    git wget \
    && rm -rf /var/lib/apt/lists/*

# 设置 Python
RUN ln -s /usr/bin/python3.10 /usr/bin/python

# 安装 PyTorch
RUN pip install torch==2.4.0 torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/cu121

# 工作目录
WORKDIR /workspace

# 复制代码
COPY . .

# 安装模型依赖
RUN pip install -e .

# 入口
CMD ["python", "server.py"]
```

### 构建和运行

```bash
# 构建
docker build -t sure-eval-model:{model_name} .

# 运行（CPU）
docker run -v $(pwd)/data:/data sure-eval-model:{model_name}

# 运行（GPU）
docker run --gpus all -v $(pwd)/data:/data sure-eval-model:{model_name}
```

## DevContainer 配置

### .devcontainer/devcontainer.json

```json
{
  "name": "SURE-EVAL Model Environment",
  "image": "nvidia/cuda:12.1-devel-ubuntu22.04",
  "features": {
    "ghcr.io/devcontainers/features/python:1": {
      "version": "3.10"
    }
  },
  "runArgs": ["--gpus=all"],
  "postCreateCommand": "pip install -e .",
  "customizations": {
    "vscode": {
      "extensions": ["ms-python.python"]
    }
  }
}
```

## 高风险仓库隔离原则

1. **不修改宿主机**：所有操作在容器内完成
2. **只读挂载**：代码以只读方式挂载
3. **输出分离**：工件输出到独立卷
4. **网络隔离**：按需开启外网访问

## 常见失败和修复

### 1. GPU 不可用

**症状**: `RuntimeError: No CUDA GPUs are available`

**修复**:
```bash
# 检查 nvidia-docker
docker run --gpus all nvidia/cuda:12.1-base nvidia-smi

# 重新安装 nvidia-container-toolkit
```

### 2. 权限问题

**症状**: `Permission denied`

**修复**:
```dockerfile
# Dockerfile 中添加
RUN useradd -m -u 1000 modeluser
USER modeluser
```

### 3. 模型下载超时

**症状**: 容器内下载 HuggingFace 模型超时

**修复**:
```bash
# 挂载 host cache
docker run -v ~/.cache/huggingface:/root/.cache/huggingface ...
```
