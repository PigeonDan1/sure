# Preflight Checklist

**Version**: 1.0  
**Scope**: Harness Controller (DISCOVER → PLAN transition)  
**Purpose**: Run minimal host and environment checks before BUILD_ENV

---

## When to Run

This checklist should be executed after `DISCOVER` completes and before `PLAN` begins.

**Output**: `artifacts/preflight_summary.json`

**Usage**: Input to backend selection and risk assessment

---

## Checklist Items

### 1. Python Availability

**Check**:
```bash
python --version  # or python3
which python
```

**Pass**: Python 3.8+ available
**Fail**: No Python or version < 3.8
**Action**: Install Python or abort

**Record**:
```json
{
  "python": {
    "available": true,
    "version": "3.10.12",
    "path": "/usr/bin/python3"
  }
}
```

---

### 2. Package Manager Availability

**Check**:
```bash
which uv        # preferred
which pip       # fallback
which conda     # fallback
which docker    # container option
```

**Pass**: At least one package manager available
**Fail**: None available
**Action**: Install package manager or use Docker

**Record**:
```json
{
  "package_managers": {
    "uv": {"available": true, "version": "0.4.0"},
    "pip": {"available": true, "version": "23.0"},
    "conda": {"available": false},
    "docker": {"available": true, "version": "24.0"}
  }
}
```

---

### 3. GPU / CUDA Visibility (if required)

**Check**:
```bash
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

**Pass**: GPU visible and CUDA available (if model requires GPU)
**Warn**: GPU not available but model can use CPU
**Fail**: GPU required but not available

**Record**:
```json
{
  "gpu": {
    "required": true,
    "available": true,
    "cuda_version": "12.1",
    "gpu_count": 1,
    "gpu_name": "NVIDIA A100"
  }
}
```

---

### 4. Disk Space

**Check**:
```bash
df -h .                    # Current directory
df -h ~/.cache             # Cache directory
```

**Minimums**:
- Model code: 1 GB
- Python environment: 5 GB
- Model weights: As specified in spec (check HuggingFace/model card)
- Working buffer: 10 GB

**Pass**: Available space > required + 20% buffer
**Warn**: Available space > required but < 20% buffer
**Fail**: Available space < required

**Record**:
```json
{
  "disk_space": {
    "current_dir_gb": 45.2,
    "cache_dir_gb": 120.5,
    "required_gb": 20.0,
    "status": "pass"
  }
}
```

---

### 5. Network / Model Source Reachable

**Check**:
```bash
# 基础连通性
ping -c 1 8.8.8.8

# 主要模型源（HF 可能被封，优先检查 ModelScope）
curl -I https://www.modelscope.cn
curl -I https://modelscope.cn/models/test/summary

# 镜像源可用性（PyTorch CDN 可能被封）
curl -I https://pypi.tuna.tsinghua.edu.cn/simple/torch/

# 实际下载速度测试（小文件）
time curl -L -o /dev/null https://modelscope.cn/models/test/summary
```

**Pass**: ModelScope 可达，清华镜像可达，下载速度 > 100KB/s
**Warn**: 镜像源可用但下载速度慢 (< 500KB/s)；或 HF 不可达但 ModelScope 可达
**Fail**: ModelScope 不可达 或 镜像源不可达

**Action on Fail**:
- 检查代理配置 (`http_proxy`, `https_proxy`)
- 切换至内网镜像
- 记录网络限制到 `artifacts/build_plan.json`
- 若 HF 不可达，强制使用 ModelScope 作为权重源

**Record**:
```json
{
  "network": {
    "internet_reachable": true,
    "modelscope_reachable": true,
    "huggingface_reachable": false,
    "mirror_reachable": true,
    "latency_ms": 150,
    "download_speed_kbps": 850,
    "constraints": ["huggingface_blocked"]
  }
}
```

---

### 6. Git LFS and Submodules

**Check**:
```bash
git lfs version
ls -la .git/lfs  # if repo uses LFS
```

**Pass**: Git LFS available (if repo requires it)
**Warn**: Git LFS not installed but may not be needed
**Fail**: Git LFS required but not available

**Record**:
```json
{
  "git": {
    "lfs_available": true,
    "submodules_present": false,
    "submodules_initialized": true
  }
}
```

---

### 7. Required System Tools

**Tools to check**:
- `ffmpeg`: Audio processing
- `libsndfile`: Audio I/O
- `gcc`/`g++`: Compilation for C++ extensions
- `git`: Version control

**Check**:
```bash
which ffmpeg
ffmpeg -version | head -1
which gcc
gcc --version | head -1
```

**Pass**: All required tools available
**Warn**: Optional tools missing
**Fail**: Required tools missing

**Record**:
```json
{
  "system_tools": {
    "ffmpeg": {"available": true, "version": "4.4.2"},
    "libsndfile": {"available": true, "version": "1.0.31"},
    "gcc": {"available": true, "version": "11.3.0"}
  }
}
```

---

### 8. Secrets / Config (for API models)

**Check** (if `deployment_type == "api"`):
```bash
echo $DASHSCOPE_API_KEY
echo $OPENAI_API_KEY
ls -la .env  # if using dotenv
```

**Pass**: Required API keys set
**Fail**: Required keys missing

**Record**:
```json
{
  "secrets": {
    "DASHSCOPE_API_KEY": {"set": true, "source": "env"},
    "OPENAI_API_KEY": {"set": false}
  }
}
```

---

### 9. Target Directory Permissions

**Check**:
```bash
ls -la sure/models/{model_name}/
touch sure/models/{model_name}/.write_test
rm sure/models/{model_name}/.write_test
```

**Pass**: Read/write access to model directory
**Fail**: Permission denied

**Record**:
```json
{
  "permissions": {
    "model_dir_readable": true,
    "model_dir_writable": true,
    "cache_dir_writable": true
  }
}
```

---

## Preflight Summary Format

```json
{
  "timestamp": "2024-03-27T21:30:00Z",
  "model_id": "nvidia/parakeet-tdt-0.6b-v2",
  "overall": "pass",
  "checks": {
    "python": {"status": "pass", "details": {...}},
    "package_managers": {"status": "pass", "details": {...}},
    "gpu": {"status": "warn", "details": {...}},
    "disk_space": {"status": "pass", "details": {...}},
    "network": {"status": "pass", "details": {...}},
    "git": {"status": "pass", "details": {...}},
    "system_tools": {"status": "pass", "details": {...}},
    "secrets": {"status": "pass", "details": {...}},
    "permissions": {"status": "pass", "details": {...}}
  },
  "recommendations": [
    "GPU available but model can run on CPU; recommend setting DEVICE=auto",
    "Git LFS not installed; not required for this model"
  ],
  "blockers": []
}
```

---

## Integration with PLAN

Builder Agent should consume `preflight_summary.json`:

- Use `package_managers` to inform backend choice
- Use `gpu` to set `requires_gpu` and `device` defaults
- Use `disk_space` to validate weight download feasibility
- Use `network` to plan download strategy (mirror, cache)
- Use `system_tools` to add required packages to environment

If `overall != "pass"`, halt and escalate before BUILD_ENV.
