#!/bin/bash
# Setup script for ASR Qwen3 model

set -e

echo "Setting up ASR Qwen3 model environment..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Create virtual environment
echo "Creating virtual environment..."
uv venv --python 3.11

# Install dependencies
echo "Installing dependencies..."
uv pip install -e .

echo "Setup complete!"
echo ""
echo "To activate environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To test the server:"
echo "  .venv/bin/python server.py"
