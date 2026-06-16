# API 模型策略 Playbook

## API 模型如何管理

API 模型（如 DashScope、OpenAI、Google Gemini）与本地模型的主要区别：

| 维度 | API 模型 | 本地模型 |
|------|----------|----------|
| 部署方式 | 远程服务 | 本地推理 |
| 环境依赖 | 仅需 HTTP 客户端 | 需完整 Python 环境 |
| 权重管理 | 服务商托管 | 本地下载/加载 |
| 密钥管理 | 需要 API Key | 不需要 |
| 离线可用性 | 否 | 是 |

## Key/Config/Healthcheck 基本要求

### 1. API Key 管理

```python
# 从环境变量读取
api_key = os.environ.get("DASHSCOPE_API_KEY")
if not api_key:
    raise RuntimeError("DASHSCOPE_API_KEY not set")
```

### 2. 配置文件

```yaml
# config.yaml
api:
  base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
  api_key_env: "DASHSCOPE_API_KEY"
  timeout: 120
  retry: 3
```

### 3. Healthcheck

```python
def healthcheck() -> bool:
    """验证 API 可访问性"""
    try:
        response = requests.get(
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5
        )
        return response.status_code == 200
    except:
        return False
```

## 与本地模型的差异

### 环境配置差异

```yaml
# API 模型 config.yaml
task: OMNI
deployment_type: api
server:
  command: [".venv/bin/python", "server.py"]
  env:
    DASHSCOPE_API_KEY: "${DASHSCOPE_API_KEY}"  # 从环境读取

# 本地模型 config.yaml  
task: ASR
deployment_type: local
server:
  command: [".venv/bin/python", "server.py"]
  env:
    MODEL_PATH: "nvidia/parakeet-tdt-0.6b-v2"
    DEVICE: "auto"
```

### Wrapper 差异

```python
# API 模型
class APIWrapper:
    def __init__(self):
        self.client = OpenAI(api_key=os.environ["API_KEY"])
    
    def infer(self, audio_path: str) -> str:
        with open(audio_path, "rb") as f:
            response = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=f
            )
        return response.text

# 本地模型
class LocalWrapper:
    def __init__(self):
        self.model = ASRModel.from_pretrained("model_id")
    
    def infer(self, audio_path: str) -> str:
        return self.model.transcribe(audio_path)
```

## 常见问题

### 1. API Key 无效

**症状**: `401 Unauthorized`

**修复**: 检查环境变量是否正确设置

### 2. 请求超时

**症状**: `ReadTimeout`

**修复**: 增加 timeout 配置，或减小请求音频时长

### 3. 速率限制

**症状**: `429 Too Many Requests`

**修复**: 实现指数退避重试

```python
import time
from functools import wraps

def retry_with_backoff(max_retries=3):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except RateLimitError:
                    time.sleep(2 ** i)
            raise
        return wrapper
    return decorator
```
