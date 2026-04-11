# VLM Qwen2-VL Model

Vision-Language inference using Alibaba's Qwen2-VL-2B-Instruct model.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | qwen2_vl_2b_instruct |
| **Task** | VLM (Vision-Language Model) |
| **Model** | Qwen/Qwen2-VL-2B-Instruct |
| **Size** | 2B parameters (~4.1G cached weights) |
| **Input** | Single local image + text prompt |
| **License** | See model page |
| **Source** | [HuggingFace](https://huggingface.co/Qwen/Qwen2-VL-2B-Instruct) |

## Capabilities

- **Image Description**: Describe a single image with a text response
- **Prompted Inference**: Supports custom prompts for the input image
- **JSON Output**: Returns JSON-serializable output with a non-empty `text` field
- **MCP Tooling**: Exposed through the `describe_image` tool

## Environment Setup

```bash
# Setup with conda environment file
cd /cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/qwen2_vl_2b_instruct
conda env create -f environment.yml
conda activate qwen2-vl-2b-instruct

# Or manual setup
cd /cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/qwen2_vl_2b_instruct
conda create -n qwen2-vl-2b-instruct python=3.10 -y
conda activate qwen2-vl-2b-instruct
uv pip install -e .
```
## Test Results

### Shared VLM Fixture

| Date | Samples | Result | Runtime | Notes |
|------|---------|--------|---------|-------|
| 2026-04-02 | 1 (phase-1 fixture) | Passed | mps:0 | Single-image description returned non-empty text |

### Inference Validation

| Date | Stage | Status | Notes |
|------|-------|--------|-------|
| 2026-04-02 | VALIDATE_IMPORT | Passed | Imported `Qwen2VLForConditionalGeneration`, `AutoProcessor`, and `process_vision_info` |
| 2026-04-02 | VALIDATE_LOAD | Passed | Model and processor loaded successfully |
| 2026-04-02 | VALIDATE_INFER | Passed | Local image fixture produced non-empty text |
| 2026-04-02 | VALIDATE_CONTRACT | Passed | Output was JSON serializable and `text` was non-empty |

### Other Datasets

| Dataset | Task | Status | Results |
|---------|------|--------|---------|
| Shared VLM fixture | Single-image description | Tested | Passed |
| OCR benchmark | OCR | Not tested | - |
| Multi-image benchmark | VLM | Not tested | - |
| Video benchmark | Video understanding | Not tested | - |
| Grounding benchmark | Vision grounding | Not tested | - |

## Usage

### As MCP Server

```yaml
# config/mcp_tools.yaml
tools:
  qwen2_vl_2b_instruct:
    name: "qwen2_vl_2b_instruct"
    command: ["python", "server.py"]
    working_dir: "/cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/qwen2_vl_2b_instruct"
    env:
      HF_HOME: "/tmp/sure_eval_qwen2_vl_2b_instruct/hf_home"
    timeout: 300
```
### Direct Usage

```python
from sure_eval.models.qwen2_vl_2b_instruct import ModelWrapper

model = ModelWrapper()
result = model.predict({
    "image_path": "tests/fixtures/shared/vlm/demo_image.jpg",
    "prompt": "Describe this image briefly.",
    "max_new_tokens": 128,
})
print(result["text"])
```
## API Reference

### Tools

- `describe_image(image_path, prompt, max_new_tokens)`: Describe a single local image

## Files

- `server.py` - MCP server implementation
- `model.py` - Core model wrapper
- `config.yaml` - MCP configuration
- `pyproject.toml` - Python dependencies
- `environment.yml` - Conda environment definition
- `__init__.py` - Package exports
- `pip-freeze.txt` - Frozen runtime dependencies
- `weights_manifest.json` - Cached weights record
- `artifacts/` - Validation and onboarding artifacts

## Notes

- First inference is slow due to model loading
- GPU is recommended, but the recorded phase-1 run succeeded on `mps:0`
- This wrapper validates only the minimal single-image repo-native path
- Multi-image, video, OCR benchmark, and grounding tasks are not covered in phase-1
- In restricted environments, inference may still trigger HuggingFace network checks

## See Also

- [Model Spec](./model.spec.yaml)
- [Model Config](./config.yaml)
- [SURE-EVAL Model README](../README.md)
