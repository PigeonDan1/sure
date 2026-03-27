#!/bin/bash
set -e

echo "Setting up Qwen3-Omni model environment..."

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Install dependencies
.venv/bin/pip install -q openai soundfile numpy

echo "Setup complete!"
echo ""
echo "To use the model, set your API key:"
echo "  export DASHSCOPE_API_KEY='your-api-key'"
