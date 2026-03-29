# Model Spec 规范

`model.spec.yaml` 是每个模型接入时必须创建的核心规范文件，用于描述模型的基本信息、依赖和验证要求。

## 字段说明

### 基本信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model_id` | string | ✅ | 模型唯一标识，如 `nvidia/parakeet-tdt-0.6b-v2` |
| `model_name` | string | ✅ | 模型显示名称 |
| `task_type` | string | ✅ | 任务类型：asr/sd/sa_asr/ser/s2tt/slu/omni/api |
| `deployment_type` | string | ✅ | 部署类型：local/api |

### 仓库信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo.url` | string | ✅ | 模型仓库 URL |
| `repo.commit` | string | ❌ | 特定 commit hash |

### 权重信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `weights.source` | string | ✅ | 来源：huggingface/modelscope/local |
| `weights.local_path` | string | ❌ | 本地路径（如已下载）|
| `weights.required` | bool | ✅ | 是否需要下载权重 |

### 环境配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `environment.preferred_backend` | string | ✅ | 推荐后端：uv/pixi/docker/api |
| `environment.python_version` | string | ✅ | Python 版本要求 |
| `environment.requires_gpu` | bool | ✅ | 是否需要 GPU |
| `environment.system_packages` | list | ❌ | 系统包依赖列表 |

### 入口点定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `entrypoints.import_test` | string | ✅ | import 测试代码 |
| `entrypoints.load_test` | string | ✅ | load 测试代码 |
| `entrypoints.infer_test` | string | ✅ | inference 测试代码 |

### IO 契约

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `io_contract.input_type` | string | ✅ | 输入类型：audio_path/text |
| `io_contract.output_type` | string | ✅ | 输出类型：text/json |

### 验收标准

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `acceptance.must_import` | bool | ✅ | 必须能通过 import 测试 |
| `acceptance.must_load` | bool | ✅ | 必须能加载模型 |
| `acceptance.must_infer` | bool | ✅ | 必须能跑通推理 |
| `acceptance.must_return_nonempty` | bool | ✅ | 输出必须非空 |

## 完整示例

```yaml
model_id: "nvidia/parakeet-tdt-0.6b-v2"
model_name: "Parakeet-TDT-0.6B-v2"
task_type: "asr"
deployment_type: "local"

repo:
  url: "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2"
  commit: null

weights:
  source: "huggingface"
  local_path: null
  required: true

environment:
  preferred_backend: "uv"
  python_version: "3.10"
  requires_gpu: false
  system_packages: []

entrypoints:
  import_test: "import nemo.collections.asr"
  load_test: |
    import nemo.collections.asr as nemo_asr
    model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v2")
  infer_test: |
    result = model.transcribe(["test.wav"])
    assert len(result) > 0

io_contract:
  input_type: "audio_path"
  output_type: "text"

acceptance:
  must_import: true
  must_load: true
  must_infer: true
  must_return_nonempty: true
```

## 生成时机

`model.spec.yaml` 应在 **PLAN** 阶段生成，在 **BUILD_ENV** 前完成。它是后续所有步骤的输入依据。
