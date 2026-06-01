#!/bin/bash
# Setup script for ASR Parakeet model
# NVIDIA Parakeet-TDT-0.6B-v2

set -e

echo "Setting up ASR Parakeet model environment..."

if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

echo "Creating runtime cache directories..."
mkdir -p checkpoints .runtime artifacts eval_runs

echo "Creating virtual environment..."
uv venv --python 3.11

echo "Installing dependencies..."
uv pip install --python .venv/bin/python -e .

echo "Setup complete!"
echo ""
echo "To activate environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To download model weights from ModelScope:"
echo "  .venv/bin/modelscope download --model nv-community/parakeet-tdt-0.6b-v2 --local_dir checkpoints"
echo ""
echo "To test the server:"
echo "  .venv/bin/python server.py"
