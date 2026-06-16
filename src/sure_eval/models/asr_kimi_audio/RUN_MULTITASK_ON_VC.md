# Kimi-Audio multitask validation on VC

This document is the handoff path for agents that need to validate Kimi-Audio on the five fixture tasks:

```text
ASR, S2TT, SER, SLU, GR
```

Use the existing image:

```text
docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_kimi_audio:v1.0
```

Do not build or require a v1.1 image for this validation path.

## Correct Entry Point

Run this file:

```bash
/opt/asr_kimi_audio_venv/bin/python validate_multitask.py
```

The working directory must be:

```text
/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src/sure_eval/models/asr_kimi_audio
```

The multitask script calls these repo files:

```text
validate_multitask.py
model.py
kimia_infer/
fixture/asr/
fixture/s2tt/
fixture/ser/
fixture/slu/
fixture/gr/
.runtime/modelscope_cache/models/moonshotai/Kimi-Audio-7B-Instruct/
```

`validate_multitask.py` uses task-specific model methods:

```text
ASR  -> ModelWrapper.predict()
S2TT -> ModelWrapper.translate()
SER  -> ModelWrapper.recognize_emotion()
SLU  -> ModelWrapper.understand()
GR   -> ModelWrapper.recognize_gender()
```

For SLU, keep the direct-audio understanding path. Do not convert SLU into ASR plus text-only reasoning, and do not inject external MMSU metadata into the fixture prompt. The fixture audio already contains the spoken question and choices.

## Do Not Use These For Five-Task Validation

Do not use `validate.py` or `docker_validate.sh` for the five-task run. They are the older single-output validation path and write:

```text
artifacts/sample_output.json
```

That file is not the multitask result. It can be misleading for S2TT, SER, SLU, and GR because the older path calls the ASR-style `wrapper.predict()` flow.

The correct multitask output file is:

```text
artifacts/multitask_sample_output.json
```

## Submit On VC

Submit on A10 with proxy variables cleared:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
vc submit \
  -p pdgpu-a10 \
  -i docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_kimi_audio:v1.0 \
  -j kimi-audio-five-task \
  -n 1 -c 8 -m 32G -g 1 \
  -pj sjtu \
  -d /hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src/sure_eval/models/asr_kimi_audio \
  -e PYTHONPATH=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src \
     KIMI_AUDIO_VALIDATE_TASKS=ASR,S2TT,SER,SLU,GR \
     KIMI_AUDIO_LOAD_IN_8BIT=0 \
     PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
     KIMI_AUDIO_MODEL_PATH=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src/sure_eval/models/asr_kimi_audio/.runtime/modelscope_cache/models/moonshotai/Kimi-Audio-7B-Instruct \
  --cmd '/opt/asr_kimi_audio_venv/bin/python validate_multitask.py'
```

Track the job:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy vc list -j <job-id>
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy vc logs -t <job-id>-master-0
```

## Local Docker Smoke Path

VC is the preferred path. Local GPUs with about 11GB memory can OOM for the full model, especially with multiple visible GPUs and automatic placement.

If a local Docker smoke is still needed, run:

```bash
KIMI_AUDIO_LOAD_IN_8BIT=1 \
KIMI_AUDIO_DEVICE_MAP=auto \
KIMI_AUDIO_MAX_MEMORY=0:4500MiB,1:4500MiB,cpu:80GiB \
src/sure_eval/models/asr_kimi_audio/docker_validate_multitask.sh
```

To bind Docker to a specific GPU:

```bash
DOCKER_GPUS=device=0 src/sure_eval/models/asr_kimi_audio/docker_validate_multitask.sh
```

To validate a task subset while debugging:

```bash
KIMI_AUDIO_VALIDATE_TASKS=SLU src/sure_eval/models/asr_kimi_audio/docker_validate_multitask.sh
```

The local Docker script is:

```text
src/sure_eval/models/asr_kimi_audio/docker_validate_multitask.sh
```

It mounts the repo files read-only, mounts `.runtime/modelscope_cache`, and runs `validate_multitask.py` inside the v1.0 image.

## Outputs

VC writes outputs under:

```text
/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src/sure_eval/models/asr_kimi_audio/artifacts/
```

Local Docker writes outputs under:

```text
src/sure_eval/models/asr_kimi_audio/docker_artifacts_multitask/
```

Inspect these files:

```text
validation_multitask.log
multitask_sample_output.json
ref_asr.txt
hyp_asr.txt
ref_s2tt.txt
hyp_s2tt.txt
ref_ser.txt
hyp_ser.txt
ref_slu.txt
hyp_slu.txt
prompt_slu.jsonl
ref_gr.txt
hyp_gr.txt
```

Useful summary command:

```bash
jq '{status, tasks, task_results: (.task_results | map_values({status, num_samples, metrics}))}' artifacts/multitask_sample_output.json
```

## Status Semantics

`status=COMPLETE` means the evaluation ran to completion and metrics are available. Low metrics, wrong labels, and `MISMATCH` log lines are normal model evaluation results.

`status=ERROR` or a non-zero script exit means a real runtime issue occurred, such as model loading failure, missing fixture files, missing weights, or an exception in the validation code.

Do not describe model wrong answers as validation failure. Use "mismatch", "wrong sample", or "metric result" for those cases.

## Current Fixture Result To Expect

The last full VC run on `2026-06-02` used `validate_multitask.py` with the v1.0 image and produced these fixture-level results:

```text
ASR:  COMPLETE, CER=0.0 on 3 samples
S2TT: COMPLETE, BLEU=36.7265 and chrF=26.5046 on 3 samples
SER:  COMPLETE, accuracy=1.0 on 3 IEMOCAP samples
SLU:  COMPLETE, accuracy=0.6667 on 3 MMSU smoke samples
GR:   COMPLETE, accuracy=1.0 on 3 LibriSpeech samples
```

The known SLU mismatch was:

```text
key=deixis_resolution_34bad028-6bad-4086-855a-bac86cd5f253 expected=B got=C
```

That mismatch should remain in the output as a model result unless the model behavior changes.

## Troubleshooting Checklist

If S2TT output is still English, check `model.py::translate()` and confirm `result.raw.stage` is not an ASR-only stage.

If SER outputs a sentence instead of one of `neu`, `hap`, `ang`, `sad`, check `ModelWrapper.recognize_emotion()` and `extract_choice_label()`/label parsing in `model.py`.

If GR outputs a transcript instead of `male` or `female`, check `ModelWrapper.recognize_gender()`.

If SLU calls `predict()` before `understand()`, the script has regressed to ASR-first behavior. The intended path is direct audio understanding through `ModelWrapper.understand()`.
