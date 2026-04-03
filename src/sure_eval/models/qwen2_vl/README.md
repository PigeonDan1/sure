# Qwen2-VL-2B-Instruct

Phase-1 onboarding for Alibaba's `Qwen/Qwen2-VL-2B-Instruct` in SURE-EVAL.

This directory captures the harness-first onboarding result for the model's minimal
repo-native image-text path. The phase-1 target is intentionally narrow: import the
Transformers/Qwen2-VL stack, load the checkpoint, run one local image prompt, and
verify that the model returns a non-empty JSON-serializable text field.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | `qwen2-vl-2b-instruct` |
| **Task** | VLM |
| **Model ID** | `Qwen/Qwen2-VL-2B-Instruct` |
| **Deployment** | Local |
| **Weight Source** | Hugging Face |
| **Phase-1 Status** | Passed |
| **Validated Device** | `mps:0` |
| **Preferred Hint** | `uv` |
| **Chosen Backend** | `conda` |

## Phase-1 Scope

Validated in phase-1:

- Import `Qwen2VLForConditionalGeneration`, `AutoProcessor`, and `qwen_vl_utils.process_vision_info`
- Load `Qwen/Qwen2-VL-2B-Instruct`
- Run a single-image understanding prompt on a local fixture
- Verify output contract: JSON serializable and non-empty `text`

Explicitly out of scope for phase-1:

- Multi-image reasoning
- Video understanding
- OCR benchmarking
- Grounding boxes
- Tool use / agent behavior
- Structured extraction

## Validation Summary

The onboarding succeeded end-to-end under the harness workflow:

- `VALIDATE_IMPORT`: passed
- `VALIDATE_LOAD`: passed
- `VALIDATE_INFER`: passed
- `VALIDATE_CONTRACT`: passed
- wrapper smoke: passed

The runtime output for the local fixture was:

```json
{
  "text": "The image depicts a simple, geometric house with a brown roof and two windows. The house is situated on a grassy lawn with a pathway leading up to it. To the right of the house, there is a green tree. The sky is blue with a yellow sun in the background. The overall scene is a cartoon-like representation of a house in a rural setting."
}
```

See:

- [verdict.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/verdict.json)
- [validation.log](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/validation.log)
- [runtime_output.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/runtime_output.json)

## Environment Notes

The input hint preferred `uv`, but preflight evidence showed that `uv` was not
available on this host. To satisfy the constitution's evidence-first rule, phase-1
used an isolated `conda` Python 3.10 environment instead.

Reproducibility artifacts:

- [pyproject.toml](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/pyproject.toml)
- [conda-explicit.txt](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/conda-explicit.txt)
- [pip-freeze.txt](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/pip-freeze.txt)
- [build_plan.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/build_plan.json)
- [build.log](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/build.log)

Installed runtime versions used for validation:

- `torch==2.11.0`
- `torchvision==0.26.0`
- `transformers==4.57.6`
- `accelerate==1.13.0`
- `qwen-vl-utils==0.0.14`
- `pillow==12.2.0`
- `sentencepiece==0.2.1`

## Weights and Fixture

Weights were resolved from Hugging Face and cached under:

- `/tmp/sure_eval_qwen2_vl_2b_instruct/hf_home`

Weight details are recorded in:

- [weights_manifest.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/weights_manifest.json)

The repository had no existing shared VLM image fixtures, so phase-1 created a
deterministic local fixture:

- [demo_image.jpg](/Users/wency/Desktop/sjtu/SURE/sure/tests/fixtures/shared/vlm/demo_image.jpg)

This fixture is task-specific for minimal single-image VLM validation, but it is not
intended to represent broader evaluation coverage.

## Wrapper Files

The generated wrapper reuses the validated repo-native path and is intended as the
model-local integration layer for SURE:

- [model.py](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/model.py)
- [server.py](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/server.py)
- [__init__.py](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/__init__.py)
- [config.yaml](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/config.yaml)

Wrapper behavior:

- `ModelWrapper.load()` loads the model and processor lazily
- `ModelWrapper.predict()` accepts an image path or request dict
- output contract is `{"text": "<non-empty string>"}`

## Known Constraints

- The host had no visible CUDA device during onboarding; successful validation used `mps:0`.
- Sandbox networking was unavailable by default, so this run required controlled
  network escalation for package installation, checkpoint fetch, and final infer.
- During inference, Qwen2-VL still performed auxiliary Hugging Face lookups even after
  the main checkpoint had already been cached; this is recorded in the failure/retry artifacts.

See:

- [preflight_summary.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/preflight_summary.json)
- [failure_classification.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/failure_classification.json)
- [retry_recommendation.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/retry_recommendation.json)
- [escalation.json](/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/qwen2_vl_2b_instruct/artifacts/escalation.json)

## Minimal Usage

### Direct wrapper usage

```python
from sure_eval.models.qwen2_vl_2b_instruct import ModelWrapper

wrapper = ModelWrapper({
    "hf_home": "/tmp/sure_eval_qwen2_vl_2b_instruct/hf_home"
})

result = wrapper.predict({
    "image_path": "/absolute/path/to/image.jpg",
    "prompt": "Describe this image briefly."
})

print(result["text"])
```

### Repo-native validation path

```python
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
```

## Next Steps

Suggested follow-up after this phase-1 success:

1. Add richer VLM fixtures that cover real photographs rather than only the synthetic phase-1 image.
2. Evaluate whether offline inference can be made fully cache-complete to avoid auxiliary Hugging Face lookups.
3. Define phase-2 coverage for multi-image, OCR-like content, or structured extraction if those capabilities matter for downstream SURE tasks.
