# 最小验证契约

> **重要说明**：本文档中的测试代码仅为**示例模板**，展示四类验证的基本形式。
> 
> **实际执行时**：
> - 应以 `model.spec.yaml` 中定义的 `entrypoints.import_test`、`entrypoints.load_test`、`entrypoints.infer_test` 为准
> - 测试样本路径应使用模型专属的 fixture，而非文档示例中的固定路径（详见 [Fixture 政策](./fixture_policy.md)）
> - 类名、方法名应根据具体模型实现调整
>
> 本文档的目的是定义验证的**类别和标准**，而非强制所有模型共享相同的类名和测试路径。

第一阶段模型接入必须通过以下四类测试，每类测试都有明确的通过标准和失败判定。

## 1. Import Test

**目标**: 验证模型依赖可以正确导入

**测试代码示例** (实际以 model.spec.yaml 定义为准):
```python
def test_import():
    """验证模型可以导入"""
    try:
        from model import ModelClass  # 由 model.spec.yaml 定义
        return True, "Import successful"
    except ImportError as e:
        return False, f"Import failed: {e}"
```

**通过标准**:
- 无 ImportError
- 无 ModuleNotFoundError

**失败判定**:
- 属于 `python_dependency_missing`
- 需要检查 requirements 和安装步骤

## 2. Load Test

**目标**: 验证模型可以加载到内存

**测试代码示例** (实际以 model.spec.yaml 定义为准):
```python
def test_load():
    """验证模型可以加载"""
    try:
        from model import ModelClass  # 由 model.spec.yaml 定义
        model = ModelClass(device='cpu')
        # 触发懒加载
        model.load()
        return True, "Load successful"
    except Exception as e:
        return False, f"Load failed: {e}"
```

**通过标准**:
- 模型对象创建成功
- 权重加载无报错
- 内存占用合理

**失败判定**:
- 属于 `missing_weights` 或 `cuda_version_mismatch`
- 需要检查模型路径和 CUDA 兼容性

## 3. Infer Test

**目标**: 验证可以跑通最小推理

**测试代码示例** (实际以 model.spec.yaml 定义和专属 fixture 为准):
```python
def test_infer():
    """验证可以跑通推理"""
    try:
        from model import ModelClass  # 由 model.spec.yaml 定义
        model = ModelClass(device='cpu')
        
        # 使用模型专属 fixture 路径 (由 model.spec.yaml 或 harness 配置)
        result = model.predict("path/to/model_specific_fixture.wav")
        
        return True, f"Infer successful: {result}"
    except Exception as e:
        return False, f"Infer failed: {e}"
```

**通过标准**:
- 推理完成无异常
- 返回结果非空
- 耗时合理（< 60s）

**失败判定**:
- 属于 `wrong_entrypoint` 或 `runtime_backend_incompatible`
- 需要检查 API 签名和依赖兼容性

## 4. Contract Test

**目标**: 验证输出满足基础格式要求

**测试代码示例** (实际以 model.spec.yaml 中 io_contract 定义为准):
```python
def test_contract():
    """验证输出满足契约"""
    from model import ModelClass, ResultClass  # 由 model.spec.yaml 定义
    
    model = ModelClass(device='cpu')
    result = model.predict("path/to/model_specific_fixture")
    
    # 检查类型 (由 model.spec.yaml io_contract.output_type 定义)
    assert isinstance(result, ResultClass), "Output type mismatch"
    
    # 检查非空
    assert result.output_field, "Output is empty"
    
    # 检查字段 (由 model.spec.yaml 定义的契约决定)
    assert hasattr(result, 'required_field'), "Missing required field"
    
    return True, "Contract check passed"
```

**通过标准**:
- 输出类型正确
- 必要字段存在
- 值非空且有效

**失败判定**:
- 属于 `wrong_entrypoint` 或 wrapper 实现问题
- 需要调整 wrapper 输出格式

## 验证顺序

```
IMPORT -> LOAD -> INFER -> CONTRACT
   ↓        ↓       ↓         ↓
 失败     失败    失败      失败
   ↓        ↓       ↓         ↓
DIAGNOSE -> REPLAN -> RETRY
```

## 验证结果记录

每次验证必须记录到 `validation.log`:

```json
{
  "timestamp": "2024-03-27T21:30:00Z",
  "tests": {
    "import": {"passed": true, "duration_ms": 500},
    "load": {"passed": true, "duration_ms": 3000},
    "infer": {"passed": true, "duration_ms": 5000},
    "contract": {"passed": true, "duration_ms": 100}
  },
  "overall": "PASSED"
}
```
