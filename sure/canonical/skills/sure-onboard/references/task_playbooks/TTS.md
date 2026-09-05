# TTS Model Onboarding Playbook

本文是 `docs/agents/model_tool_agent/AGENTS.md` 的 TTS 任务补充，基于
`SWivid__F5-TTS_Emilia-ZH-EN` 和 `IndexTeam__IndexTTS-2` 的接入经验。

## 1. 任务边界

TTS 在当前 SURE harness 中按“文本 + 可选音色参考 -> 生成音频”处理。对于
F5-TTS / IndexTTS-2 这类 voice-cloning TTS，最小输入应包含：

- `text`: 需要合成的目标文本。
- `prompt_audio_path` 或 `reference_audio_path`: 提供音色的参考音频。
- `prompt_text` 或 `reference_text`: 参考音频对应文本。若 fixture 有 gt，可从
  `gt.jsonl` 解析。

最小输出：

```json
{"audio_path": "artifacts/outputs/example.wav", "text": "...", "language": "zh"}
```

`model.spec.yaml` 推荐：

```yaml
task_type: "tts"
io_contract:
  input_type: "text"
  output_type: "audio_path"
  primary_field: "audio_path"
  required_fields: ["audio_path"]
  nonempty_fields: ["audio_path"]
  json_serializable: true
```

## 2. 目录与权重

TTS 模型通常同时需要主模型权重、vocoder、speaker encoder、semantic codec 或
上游源码 patch。所有内容必须 model-local：

```text
sure/models/{model}/
├── .runtime/
│   ├── modelscope_cache/      # ModelScope 权重
│   ├── huggingface/           # HF hub/cache
│   ├── source/                # 上游源码或本地 patched copy
│   ├── vocoder/               # F5 vocos 等
│   └── uv-cache/
├── checkpoints/               # 显式人工权重；如无可为空
├── fixture/zh/
├── artifacts/outputs/
└── ...
```

关键规则：

- 权重不 bake 进 Docker 镜像；通过 `.runtime` 挂载。
- `checkpoints/` 可以为空，只要 `weights_manifest.json` 指向 `.runtime` 中的真实路径。
- 如果上游代码有硬编码 cache 路径，必须在 model-local source copy 中 patch，并写
  `patch_report.json` 或 artifact notes。

F5 特别项：

- 主权重：`.runtime/modelscope_cache/SWivid/F5-TTS_Emilia-ZH-EN/`
- vocoder：`.runtime/vocoder/vocos-mel-24khz/`
- vocoder 至少包含 `config.yaml` 和 `pytorch_model.bin`。

IndexTTS-2 特别项：

- 主权重：`.runtime/modelscope_cache/IndexTeam/IndexTTS-2/`
- 依赖 HF cache 中的 MaskGCT、campplus、bigvgan 等资源。
- 上游 index-tts 源码可放在 `.runtime/source/index-tts`，必要 path fix 必须记录。

## 3. Fixture

共享 fixture 库中的 TTS 代表样例位于：

```text
fixtures/tasks/tts/indextts2_zh_smoke/
```

索引见 `fixtures/tasks/tts/README.md`。接入新模型时，优先从该目录选择样例复制到
模型目录；如果模型需要特殊 speaker、语言或参考音频格式，再创建 model-local fixture
并记录原因。

TTS fixture 推荐：

```text
fixture/zh/
├── ZH_B00000_S00000_W000002.mp3
├── ZH_B00001_S00000_W000000.mp3
├── ZH_B00000.jsonl
└── gt.jsonl
```

`gt.jsonl` 至少包含：

```json
{
  "key": "sample_1",
  "prompt_audio": "ZH_B00000_S00000_W000002.mp3",
  "prompt_text": "参考音频文本",
  "target_text": "要合成的新文本",
  "task": "TTS"
}
```

验证时必须避免“直接返回参考音频”。IndexTTS-2 接入时已踩过这个坑；`validate.py`
应使用与 prompt 不同的 `target_text`，并检查输出文件路径、采样率、样本数和文件大小。

TTS metric namespace:

```text
src/sure_eval/evaluation/tts/
```

`/sure_onboard` 的本地验证以 task-local `validate.py` 检查 import/load/infer/audio
contract 为最小门槛，但不能把“输出音频存在、可解码、不是 prompt copy”当作 TTS
评测完成。正式 TTS 指标必须通过 SURE evaluation 工具链计算。

强制经验规则：

- 评测入口必须使用 `TTSSample` + `TTSMetricPipeline`，优先使用
  `build_default_tts_metric_pipeline()` 或 `sure-eval metric describe/run`。
- 如果某些重模型 provider 缺 checkpoint 或资源不可用，必须记录为
  `blocked` / `not_available`，不能退回手写相似度、手写 MOS 或只用文件大小作为
  metric。
- `tts_metric_report.json` 必须记录 sample、prediction audio、reference text、
  reference audio、metric backend、results 和 provider failures/blockers。
- 如果只是修正 TTS metric 口径，不允许重新跑 TTS 推理；必须复用已有生成音频和
  `sample_output.json` 中的 prompt / target text，再用 TTS evaluation 工具重算。
- 出现 TTS metric bypass 时读取
  `references/memory/bad_cases/tts_metric_bypass.md`。

推荐新入口：

```bash
sure-eval metric describe tts \
  --language zh \
  --metrics tts_cer,sim/wavlm-large \
  --output /tmp/tts_pipeline.json \
  --json

sure-eval metric run \
  --pipeline /tmp/tts_pipeline.json \
  --samples-jsonl /tmp/tts_samples.jsonl \
  --output-dir /tmp/sure_eval/tts_eval \
  --device cuda \
  --cache-dir <sure-eval-cache-root>/tts-metrics \
  --validate-env \
  --json
```

`samples_jsonl` 每行必须显式给出角色：

```json
{"sample_id":"tts_smoke","prediction_audio":"outputs/tts.wav","reference_text":"目标文本","reference_audio":"prompt.wav","language":"zh"}
```

逻辑分工：

- 模型自身的 `validate.py` / Docker validate 只负责 import/load/infer/audio contract，
  并产出 `sample_output.json` 和合成音频。
- 正式 TTS 指标复用 `sure-eval metric describe/run`：读取已有合成音频、prompt /
  reference audio、target / reference text，通过 `src/sure_eval/evaluation/tasks/tts`
  的 `TTSSample` / `TTSMetricPipeline` 计算指标，并输出标准 `report.json` 和
  `pipeline_description.json`。

历史 wrapper 仍可作为已有镜像的兼容入口：

```text
scripts/run_tts_metric_pipeline.py
scripts/run_tts_metric_pipeline_docker.py
scripts/run_tts_metric_pipeline_docker.sh
```

新接入和 agent 调用优先使用 `sure-eval metric describe/run`；旧 wrapper 只作为已验证
镜像或离线修复路径。

## 4. Backend 选择

TTS 依赖通常重，默认 GPU-first：

1. 如果已有可复用基础镜像且两模型运行时兼容，可以复用镜像，但不能把这个经验泛化到所有模型。
2. 如果本地 uv 可稳定 GPU 初始化，可以保留 `local_uv_setup.sh` / `local_uv_validate.sh`。
3. 对外评测优先 Docker，因为 torch/CUDA wheel、driver、HF 资源和上游动态库更稳定。

本地 uv 必须 pin torch/torchaudio，不能让安装漂到不兼容 CUDA wheel。
F5 的经验：

- 错误环境：`torch 2.12.0+cu130` 在 d6 debug GPU 上无法初始化 CUDA。
- 修复环境：`torch==2.8.0+cu128`, `torchaudio==2.8.0+cu128`。
- `requirements-local.txt` 需要：

```text
--index-url https://download.pytorch.org/whl/cu128
--extra-index-url https://pypi.org/simple
torch==2.8.0+cu128
torchaudio==2.8.0+cu128
```

- `local_uv_setup.sh` 使用：

```bash
uv pip install --index-strategy unsafe-best-match --python .venv/bin/python -r requirements-local.txt
```

如果 PyTorch wheel 下载超时，可以临时：

```bash
. <proxy-on-script>
# install
. <proxy-off-script>
```

使用代理只限下载场景；Docker、GPU、registry 操作仍清代理。

## 5. Wrapper 要求

`model.py` 应提供：

- `ModelWrapper.load()`
- `ModelWrapper.predict(payload)`
- `ModelWrapper.health()`

`predict()` 输入建议支持：

```json
{
  "text": "目标合成文本",
  "prompt_audio_path": "fixture/zh/ref.mp3",
  "prompt_text": "参考音频文本",
  "language": "zh"
}
```

输出：

```json
{
  "text": "目标合成文本",
  "audio_path": "artifacts/outputs/model_smoke.wav",
  "language": "zh",
  "raw": {"sample_rate": 24000, "num_samples": 153856}
}
```

注意：

- import 阶段不要加载权重。
- `load()` 阶段解析权重路径并加载模型。
- `predict()` 必须创建 `artifacts/outputs`。
- 输出音频必须是新文件，不能覆盖 fixture。
- 如果上游 API 会自动下载资源，必须把 cache 指到 model-local `.runtime`。

## 6. local_uv_validate.sh

TTS 本地验证命令必须显式指定 GPU：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
DEVICE=cuda:0 \
sure/models/{model}/local_uv_validate.sh
```

脚本必须：

- 写 `artifacts/local_uv_validate.log`。
- 成功后写 `artifacts/local_uv_validation.json`。
- 失败时保留原始 traceback。
- 不把 CPU fallback 当作成功。

验证前建议加轻量 CUDA check：

```bash
.venv/bin/python - <<'PY'
import torch
print(torch.__version__, torch.version.cuda, torch.cuda.is_available())
PY
```

## 7. Docker 验证

TTS Docker 验证应遵守：

- 镜像只放环境，不放权重。
- `.runtime`、`fixture`、`artifacts` 用 volume 挂载。
- `GPU_DEVICE` 是宿主 GPU id；容器内 `DEVICE` 通常仍为 `cuda:0`。
- 外部用户评测可设置 `ARTIFACTS_DIR` 到自己的目录，避免写模型目录。

示例：

```bash
cd <legacy-sure-eval-root>
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
GPU_DEVICE=0 DEVICE=cuda:0 \
ARTIFACTS_DIR=<artifact-output-root>/f5tts_eval_outputs \
sure/models/SWivid__F5-TTS_Emilia-ZH-EN/docker_validate.sh
```

已验证镜像：

```text
registry.example.com/sure/f5tts:v1.0
registry.example.com/sure/sure_tts_indextts2:v1.0
```

推送/拉取成功后记录 registry digest。

## 8. 验证标准

`validation.log` 至少要有：

- `VALIDATE_SPEC passed`
- `VALIDATE_IMPORT passed`
- `VALIDATE_LOAD passed`
- `VALIDATE_CONTRACT passed`

TTS 当前不强制自动音质指标，但必须记录：

- output audio path
- sample rate
- num samples
- file size
- prompt audio path
- target text
- 若已运行正式指标，记录 `tts_metric_report.json`、`report.json`、
  `pipeline_description.json`、metric backend 和 provider blocker。

人工试听可以作为补充结论，但不能替代 contract validation。

## 9. 新 TTS 模型接入检查表

- [ ] 已读 `AGENTS.md` 和本文。
- [ ] 明确是普通 TTS 还是 voice-cloning TTS。
- [ ] fixture 有 prompt audio、prompt text、target text。
- [ ] 权重、vocoder、HF cache 都在 model-local `.runtime`。
- [ ] wrapper 不返回参考音频本身。
- [ ] 本地 uv torch/CUDA 版本已 pin 并通过 GPU 初始化。
- [ ] Docker 验证通过，权重通过 volume 挂载。
- [ ] 输出音频、采样率、样本数写入 artifacts。
- [ ] 如执行指标，复用已有合成音频并产出 TTS metric report；没有手写 MOS/SIM。
- [ ] 镜像 push/pull digest 已记录。
