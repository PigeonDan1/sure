# Wrapper Contract

**Version**: 1.0  
**Scope**: All model wrappers  
**Purpose**: Define `model.py`, `server.py`, `__init__.py`, `config.yaml` responsibilities

---

## File Responsibilities

### model.py

**Purpose**: Model-facing local wrapper

**Responsibilities**:
- Abstract model-specific API into unified interface
- Handle device placement (CPU/GPU)
- Manage lazy loading
- Provide `predict()` or task-specific entry point

**Must Provide**:
```python
class ModelWrapper:
    def __init__(self, config=None):
        """Initialize wrapper, not necessarily load model"""
        pass
    
    def load(self):
        """Load model weights into memory"""
        pass
    
    def predict(self, input_data):
        """Run inference and return result"""
        pass
    
    def healthcheck(self):
        """Quick check if model is ready"""
        pass
```

**Must NOT**:
- Handle HTTP/网络 (that's server's job)
- Parse command line arguments
- Write to files outside model directory

---

### server.py

**Purpose**: Service/MCP entrypoint only

**Responsibilities**:
- Implement MCP protocol (initialize, tools/list, tools/call)
- Parse JSON-RPC requests
- Call `model.py` methods
- Format responses

**Must Provide**:
```python
class MCPServer:
    def handle_initialize(self, request):
        pass
    
    def handle_tools_list(self, request):
        pass
    
    def handle_tools_call(self, request):
        # Call model.predict() and return result
        pass
```

**Must NOT**:
- Import heavy dependencies at module level (delay until needed)
- Handle business logic (delegate to model.py)
- Write logs to stdout (use stderr)

**Input/Output**:
- Input: stdin (JSON-RPC)
- Output: stdout (JSON-RPC)
- Logs: stderr

---

### __init__.py

**Purpose**: Stable exports

**Responsibilities**:
- Define public API of model package
- Re-export wrapper class and result types

**Must Provide**:
```python
from .model import ModelWrapper, ResultType

__all__ = ["ModelWrapper", "ResultType"]
```

**Must NOT**:
- Import heavy dependencies (keep import fast)
- Execute side effects

---

### config.yaml

**Purpose**: Optional runtime configuration

**Status**: Optional but recommended

**When Required**:
- MCP tool registration
- Environment variable defaults
- Resource requirements documentation

**Structure**:
```yaml
name: model_name
task: ASR
description: "..."

model:
  id: "org/model-id"
  size: "0.6B"

server:
  command: [".venv/bin/python", "server.py"]
  env:
    MODEL_PATH: "org/model-id"
  timeout: 300

tools:
  - name: "task_name"
    description: "..."
    input_schema:
      type: object
      properties: {...}
```

**When Optional**:
- Simple models with no MCP integration
- API-only models configured via env vars

---

## Minimal Wrapper Interface

Every wrapper must implement at minimum:

### 1. Healthcheck

```python
def healthcheck(self) -> dict:
    """
    Returns:
        {
            "status": "ready" | "loading" | "error",
            "message": "...",
            "model_loaded": bool
        }
    """
```

### 2. Load

```python
def load(self) -> None:
    """
    Load model weights. Should be lazy (called on first predict if not explicit).
    Raises:
        RuntimeError: If loading fails
    """
```

### 3. Predict

```python
def predict(self, input_data: Any) -> Any:
    """
    Run inference.
    
    Args:
        input_data: Task-specific input (audio path, text, etc.)
    
    Returns:
        Task-specific output (transcription, labels, etc.)
    
    Raises:
        RuntimeError: If inference fails
    """
```

---

## Result Type Contract

Wrapper should define result types for type safety:

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class TranscriptionResult:
    text: str
    language: Optional[str] = None
    confidence: Optional[float] = None
    
    def __post_init__(self):
        assert isinstance(self.text, str)
        assert len(self.text) > 0, "Empty transcription"
```

Result types must:
- Be JSON-serializable (for server response)
- Have clear field documentation
- Validate invariants in `__post_init__`

---

## Error Handling

### model.py

Raise specific exceptions:
```python
class ModelLoadError(RuntimeError): pass
class InferenceError(RuntimeError): pass
class ConfigurationError(ValueError): pass
```

### server.py

Catch and format as JSON-RPC error:
```python
except ModelLoadError as e:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32000,
            "message": f"Model load failed: {e}"
        }
    }
```

---

## Configuration Loading Priority

1. Environment variables (highest)
2. `config.yaml` in model directory
3. Constructor arguments
4. Hardcoded defaults (lowest)

Example:
```python
def __init__(self, model_path=None, device=None):
    self.model_path = (
        model_path or 
        os.environ.get("MODEL_PATH") or
        self._load_from_config("model.id") or
        DEFAULT_MODEL_PATH
    )
```

---

## Testing Contract

Each wrapper file must be independently testable:

### model.py test
```python
def test_model_wrapper():
    from model import ModelWrapper
    model = ModelWrapper(device='cpu')
    model.load()
    result = model.predict("test.wav")
    assert result.text
```

### server.py test
```python
def test_mcp_server():
    # Test JSON-RPC protocol without full model
    from server import MCPServer
    server = MCPServer()
    response = server.handle_initialize({"id": 1})
    assert response["result"]["protocolVersion"] == "2024-11-05"
```

---

## Documentation Contract

Each wrapper must include module docstring:

```python
"""
{ModelName} Wrapper for SURE-EVAL.

Responsibilities:
- Load model from HuggingFace/local path
- Provide ASR inference interface
- Manage device placement

Entry Points:
- ModelWrapper: Main wrapper class
- TranscriptionResult: Output type

Dependencies:
- torch>=2.0
- transformers>=4.30

Example:
    model = ModelWrapper(device='cpu')
    result = model.predict("audio.wav")
    print(result.text)
"""
```
