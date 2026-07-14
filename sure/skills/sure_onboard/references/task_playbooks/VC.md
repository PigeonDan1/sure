# VC Model Onboarding Playbook

本文是 `docs/agents/model_tool_agent/AGENTS.md` 的 VC 任务补充，基于
`Plachtaa__seed-vc` 的接入经验。

## 1. 任务边界

Voice Conversion 是“源音频 + 参考音频 -> 转换后音频”。当前最小契约：

```json
{
  "source_audio_path": "fixture/zh/source.mp3",
  "reference_audio_path": "fixture/zh/ref.mp3"
}
```

输出：

```json
{
  "audio_path": "artifacts/outputs/seed_vc_v2_smoke.wav",
  "source_audio_path": "...",
  "reference_audio_path": "...",
  "task": "VC"
}
```

`model.spec.yaml` 推荐：

```yaml
task_type: "vc"
io_contract:
  input_type: "audio_pair"
  output_type: "audio_path"
  primary_field: "audio_path"
  required_fields: ["audio_path", "source_audio_path", "reference_audio_path"]
  nonempty_fields: ["audio_path"]
  json_serializable: true
```

VC 第一阶段不做音色相似度指标，只验证可加载、可推理、输出音频契约正确。

## 2. 目录与权重

VC 模型通常依赖上游源码、多个 checkpoint、HF cache 和配置文件。推荐结构：

```text
sure/models/{model}/
├── .runtime/
│   ├── source/<upstream-repo>/
│   ├── huggingface/
│   ├── cache/
│   └── matplotlib/
├── checkpoints/
├── fixture/zh/
├── artifacts/outputs/
├── model.py
├── validate.py
├── local_uv_setup.sh
├── local_uv_validate.sh
├── Dockerfile
├── docker_build.sh
└── docker_validate.sh
```

Seed-VC 经验：

- 上游源码放在 `.runtime/source/seed-vc`。
- V2 推理入口是 `inference_v2.py`。
- 上游默认使用相对路径 `./checkpoints`，wrapper 需要在调用时 `cwd` 到上游源码目录，
  使 checkpoint 解析留在 model-local 范围内。
- 如果首次推理会自动下载权重，要通过 `HF_ENDPOINT`、`HF_HOME`、`HF_HUB_CACHE`
  指向 `.runtime`。

## 2.5 Fixture

共享 fixture 库中的 VC 代表样例位于：

```text
fixtures/tasks/vc/seed_vc_zh_smoke/
```

索引见 `fixtures/tasks/vc/README.md`。接入新模型时，优先从该目录选择样例复制到
模型目录；当前共享 VC 样例只有音频，模型目录下仍需根据 wrapper contract 创建
`gt.jsonl`。

VC metric namespace:

```text
src/sure_eval/evaluation/vc/
```

当前阶段没有迁入共享 VC metric；phase-1 仍以 model-local `validate.py` 检查输出音频契约。

## 3. 下载与网络

VC 上游常依赖 Hugging Face。规则：

- 使用 `hf-mirror.com` 时不要开代理。
- 先关闭代理：

```bash
. /hpc_stor03/sjtu_home/junhao.du/.local/bin/ssr-off
```

- 再执行：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 <download-or-validate-command>
```

Seed-VC 经验：

- `facebook/hubert-large-ll60k/pytorch_model.bin` 可通过 hf-mirror 下载。
- 如果 Hugging Face Xet 下载很慢或留下 incomplete blob，要清理不完整文件并改用
  no-proxy mirror。
- 下载完成后用 `torch.load` 或模型 load 阶段验证，不只看文件存在。

## 4. Fixture

VC fixture 至少两条音频：

```text
fixture/zh/
├── source.mp3
├── reference.mp3
└── gt.jsonl
```

`gt.jsonl` 建议：

```json
{
  "key": "vc_smoke_1",
  "source_audio": "source.mp3",
  "reference_audio": "reference.mp3",
  "task": "VC"
}
```

可以复用 TTS fixture，但必须明确哪条是 source、哪条是 reference。VC 不依赖
target text；如果 fixture 来自带文本的数据集，文本只能作为说明，不作为主要输入。

## 5. Backend 选择

VC 优先 GPU。接入顺序：

1. 先尝试 model-local uv，快速验证上游 V2 推理链路。
2. 如果 uv 通过，再固化 Docker。
3. 如果上游依赖复杂或需要系统包，Docker 是对外运行的主路径。

Seed-VC local uv 经验：

- `resemblyzer` 会引入 `webrtcvad==2.0.10`，在缺 Python.h 时构建失败。
- 如果 V2 推理路径不 import `resemblyzer`，可从 local requirements 中去掉，并记录原因。
- 本地验证命令可用：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
HF_ENDPOINT=https://hf-mirror.com \
CUDA_VISIBLE_DEVICES=0 DEVICE=cuda DIFFUSION_STEPS=2 SURE_XFORGE_STATIC_ONLY=0 \
sure/models/Plachtaa__seed-vc/.venv/bin/python \
sure/models/Plachtaa__seed-vc/validate.py
```

## 6. Wrapper 要求

`model.py` 必须：

- import 阶段不加载大模型。
- `load()` 阶段加载所有 checkpoint。
- `predict()` 接收 `source_audio_path` 和 `reference_audio_path`。
- 输出写到 `artifacts/outputs`。
- 记录 sample rate、num samples、device、source_dir。
- 对上游 `cwd`、cache、checkpoint 路径做 model-local 重定向。

输出示例：

```json
{
  "audio_path": "artifacts/outputs/seed_vc_v2_smoke.wav",
  "source_audio_path": "fixture/zh/source.mp3",
  "reference_audio_path": "fixture/zh/reference.mp3",
  "task": "VC",
  "raw": {"sample_rate": 22050, "num_samples": 165632, "device": "cuda"}
}
```

## 7. Docker 验证

Docker 规则：

- 镜像内放 Python 环境和依赖。
- `.runtime/source`、`.runtime/huggingface`、`fixture`、`artifacts` 通过 volume 挂载。
- 权重和 HF cache 不 bake 进镜像。
- 对上游退出慢要用 timeout 包裹，但不能把真正失败吞掉。

Seed-VC 经验：

- 推理和 `VALIDATE_CONTRACT` 已通过后，上游进程可能不及时退出并继续占 GPU。
- `docker_validate.sh` 可用 `timeout ${VALIDATE_TIMEOUT_SECONDS}s python validate.py`。
- 如果 stdout 有 `exit status 124`，必须检查 `artifacts/validation.log` 是否已经写入
  `VALIDATE_CONTRACT passed`。只有 contract 已通过，才能记录为
  `passed_with_manual_container_stop_after_success` 或等价状态。

已验证镜像：

```text
docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-seed-vc:v1.0
```

## 8. Verdict 标准

`verdict.json` 至少记录：

- `status`
- `task: "VC"`
- configured backends: `uv`, `docker`
- Docker image、image id、registry digest
- output audio path、sample rate、num samples
- timeout 或手动 stop 说明
- HF mirror / proxy 规则

不能只看脚本 exit code。Seed-VC 的 `docker_validate.sh` 可能在 timeout 后仍 exit 0，
所以必须以 `validation.log` 的阶段记录为准。

## 9. 新 VC 模型接入检查表

- [ ] 已读 `AGENTS.md` 和本文。
- [ ] 明确 source audio 与 reference audio。
- [ ] 上游源码、checkpoint、HF cache 都在 model-local `.runtime`。
- [ ] `HF_ENDPOINT`、`HF_HOME`、`HF_HUB_CACHE` 指向可复现路径。
- [ ] wrapper 对上游相对 checkpoint 路径做 model-local 处理。
- [ ] local uv GPU 通过或失败原因结构化记录。
- [ ] Docker GPU 通过，timeout 行为已核验。
- [ ] `validation.log` 有 `VALIDATE_CONTRACT passed`。
- [ ] 镜像 push/pull digest 已记录。
