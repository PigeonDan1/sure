#!/bin/bash
# Setup script for Whisper large-v3-turbo model

set -e

echo "Setting up Whisper large-v3-turbo model environment..."

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
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -e .

echo "Setup complete!"
echo ""
echo "To activate environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To download weights from ModelScope:"
echo "  .venv/bin/modelscope download --model iic/Whisper-large-v3-turbo --local_dir checkpoints"
echo ""
echo "To test the server:"
echo "  WHISPER_DOWNLOAD_ROOT=$PWD/checkpoints .venv/bin/python server.py"
