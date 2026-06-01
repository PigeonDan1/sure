# Qwen3-Omni Model

API client for Qwen3-Omni multi-modal model via DashScope.

## Setup

```bash
bash setup.sh
```

## Configuration

Set your DashScope API key:

```bash
export DASHSCOPE_API_KEY="your-api-key"
```

Or update `config.yaml` with your API key.

## Usage

```python
from model import Qwen3OmniModel

model = Qwen3OmniModel()

# Text-only chat
response = model.chat_text_only("Who are you?")
print(response)

# Chat with audio
result = model.chat(
    "Who are you?",
    generate_audio=True,
    output_audio_path="output.wav"
)
print(result.text)
print(f"Audio saved to: {result.audio_path}")
```

## API Reference

- Model: `qwen3-omni-flash`
- Base URL: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- Voice options: `Cherry`, etc.

## Tools

- `omni_chat`: Get text + audio response
- `omni_chat_text_only`: Get text response only (faster)
