# SURE-EVAL DashScope Integration Guide

## Overview

This guide explains how to use Alibaba Cloud Bailian (DashScope) as the central orchestrator for the SURE-EVAL framework.

## Architecture

```
User Request
    ↓
OrchestratorAgent (DashScope LLM)
    ↓
Tool Planning & Execution
    ↓
Results & RPS Calculation
```

## Components

### 1. OrchestratorAgent (`src/sure_eval/agent/orchestrator.py`)

Central agent powered by DashScope LLM that:
- Understands evaluation tasks
- Plans execution steps
- Calls tools (download, evaluate, metrics)
- Reports results with reasoning

**Key Features:**
- Function calling support
- Multi-turn conversation
- Tool result integration
- Streaming responses

### 2. DashScopeAdapter (`src/sure_eval/tools/dashscope_adapter.py`)

Adapter for using DashScope models as evaluation tools:
- `transcribe()`: ASR with qwen-audio-asr
- `translate_audio()`: S2TT with qwen-audio-chat
- `analyze_emotion()`: SER with qwen-audio-chat
- `analyze_content()`: General audio analysis

**Supported Models:**
- `qwen-plus`: General purpose LLM
- `qwen-max`: High capability LLM
- `qwen-audio-asr`: Audio transcription
- `qwen-audio-chat`: Audio understanding

### 3. AutonomousEvaluator (`src/sure_eval/agent/evaluator.py`)

Core evaluation engine that:
- Downloads datasets automatically
- Runs tools on test samples
- Computes metrics
- Updates RPS scores
- Records results

## Quick Start

### 1. Set API Key

```bash
export DASHSCOPE_API_KEY="sk-f8ae3fc37bdd4953977e813f77b7324f"
```

### 2. Use Interactive Mode

```python
from sure_eval.agent.orchestrator import InteractiveOrchestrator

orchestrator = InteractiveOrchestrator()
orchestrator.run()
```

Commands:
- `/eval <tool> <dataset> [n]` - Direct evaluation
- `/datasets` - List datasets
- `/recommend <dataset>` - Get recommendation
- `/clear` - Clear conversation
- `/quit` - Exit

### 3. Programmatic Usage

```python
from sure_eval.agent.orchestrator import OrchestratorAgent

# Initialize agent
agent = OrchestratorAgent(api_key="your-api-key")

# Direct evaluation
result = agent.evaluate_direct(
    tool_name="dashscope_qwen",
    dataset="aishell1",
    max_samples=100,
)

print(f"Score: {result.score}")
print(f"RPS: {result.rps}")

# Chat with LLM for complex tasks
response = agent.chat(
    "Evaluate dashscope_qwen on aishell1 and recommend the best tool",
    stream=False,
)
print(response["content"])

# Batch evaluation
results = agent.evaluator.batch_evaluate(
    tool_name="dashscope_qwen",
    datasets=["aishell1", "librispeech_clean"],
    max_samples=100,
)

# Compare tools
comparison = agent.evaluator.compare_tools(
    tool_names=["dashscope_qwen", "whisper", "wenet"],
    dataset="aishell1",
)
```

### 4. Use DashScope as a Tool

```python
from sure_eval.tools.dashscope_adapter import DashScopeToolWrapper

# Create tool
tool = DashScopeToolWrapper(
    name="dashscope_qwen",
    api_key="your-api-key",
    model="qwen-audio-asr",
)

# Evaluate single file
result = tool.invoke("audio.wav", task="ASR")
print(result["text"])

# Batch evaluation
results = tool.batch_invoke(
    ["audio1.wav", "audio2.wav"],
    task="ASR",
)
```

## Workflow Examples

### Example 1: Single Tool Evaluation

```python
from sure_eval.agent.orchestrator import OrchestratorAgent

agent = OrchestratorAgent()

# 1. Check if dataset exists, download if needed
# 2. Run evaluation
# 3. Compute metrics and RPS
# 4. Record results

result = agent.evaluate_direct("dashscope_qwen", "aishell1", max_samples=100)

# Output:
# ============================================================
# Evaluation Result: dashscope_qwen on aishell1
# ============================================================
# Metric: cer
# Score: 0.0856
# RPS: 0.9346
# Samples: 100
# Duration: 45.23s
# ============================================================
```

### Example 2: Autonomous Evaluation with LLM

```python
# The LLM will automatically:
# 1. Check dataset availability
# 2. Download if needed
# 3. Run evaluation
# 4. Report results with analysis

response = agent.chat("Evaluate dashscope_qwen on aishell1", stream=False)
```

### Example 3: Tool Comparison

```python
comparison = agent.evaluator.compare_tools(
    ["dashscope_qwen", "whisper", "wenet"],
    "aishell1",
)

# Output:
# | Rank | Tool          | Score  | RPS    | Duration |
# |------|---------------|--------|--------|----------|
# | 1    | dashscope_qwen| 0.0856 | 0.9346 | 45.23s   |
# | 2    | whisper       | 0.0923 | 0.8667 | 38.12s   |
# | 3    | wenet         | 0.0987 | 0.8105 | 52.45s   |
```

### Example 4: Get Recommendation

```python
rec = agent.evaluator.recommend_tool("aishell1")

# Output:
# Recommendation for aishell1:
#   Best tool: dashscope_qwen (RPS: 0.9346)
#
# Tool Ranking:
#   1. dashscope_qwen (RPS: 0.9346)
#   2. whisper (RPS: 0.8667)
#   3. wenet (RPS: 0.8105)
```

## Configuration

### config/default.yaml

```yaml
datasets:
  definitions:
    aishell1:
      name: "AISHELL-1"
      task: "ASR"
      language: "zh"
      source: "modelscope"
      dataset_id: "speech_asr_aishell1_trainsets"

rps:
  baselines:
    aishell1:
      metric: "cer"
      score: 0.80
      higher_is_better: false
```

### config/mcp_tools.yaml

```yaml
tools:
  dashscope_qwen:
    name: "dashscope_qwen"
    # For API-based tools, use a wrapper script
    command: ["python", "-m", "sure_eval.tools.dashscope_server"]
    working_dir: "."
    env:
      DASHSCOPE_API_KEY: "${DASHSCOPE_API_KEY}"
    timeout: 120
```

## Advanced Usage

### Custom Tool Adapter

```python
from sure_eval.tools.api_adapter import BaseAPIAdapter

class MyCustomAdapter(BaseAPIAdapter):
    def transcribe(self, audio_path):
        # Your implementation
        pass

# Register
from sure_eval.tools.api_adapter import APIAdapterRegistry
APIAdapterRegistry.register("my_adapter", MyCustomAdapter)
```

### Custom Evaluation Pipeline

```python
from sure_eval.agent.evaluator import AutonomousEvaluator

class CustomEvaluator(AutonomousEvaluator):
    def _run_tool(self, tool_name, samples, dataset):
        # Custom tool execution logic
        pass
```

## API Reference

### OrchestratorAgent

```python
class OrchestratorAgent:
    def __init__(self, api_key: str | None = None, config: Config | None = None)
    def chat(self, user_input: str, conversation_history: list | None = None, stream: bool = True) -> dict
    def evaluate_direct(self, tool_name: str, dataset: str, max_samples: int | None = None) -> EvaluationResult
```

### DashScopeAdapter

```python
class DashScopeAdapter:
    def transcribe(self, audio_path: str | Path, model: str = "qwen-audio-asr", language: str | None = None) -> dict
    def translate_audio(self, audio_path: str | Path, target_lang: str = "en", model: str = "qwen-audio-chat") -> dict
    def analyze_emotion(self, audio_path: str | Path, model: str = "qwen-audio-chat") -> dict
    def batch_process(self, audio_paths: list, task: str = "transcribe", **kwargs) -> list[dict]
```

## Troubleshooting

### API Key Issues

```bash
# Check if key is set
echo $DASHSCOPE_API_KEY

# Set in Python
import os
os.environ["DASHSCOPE_API_KEY"] = "your-key"
```

### Model Not Available

```python
# Use alternative models
adapter = DashScopeAdapter()

# For ASR
result = adapter.transcribe("audio.wav", model="qwen-audio-asr")

# For general tasks
result = adapter.transcribe("audio.wav", model="qwen-plus")
```

### Timeout Issues

```python
# Increase timeout
adapter = DashScopeAdapter()
adapter.client.timeout = 120
```

## Next Steps

1. **Configure Datasets**: Edit `config/default.yaml`
2. **Register Tools**: Edit `config/mcp_tools.yaml`
3. **Test API**: Run `examples/test_dashscope.py`
4. **Interactive Mode**: Run `InteractiveOrchestrator().run()`
5. **Batch Evaluation**: Use `batch_evaluate()` for multiple datasets

## Support

For issues or questions:
- Check logs in `./logs/sure-eval.log`
- Verify API key is valid
- Ensure datasets are properly configured
- Check tool configurations in `config/mcp_tools.yaml`
