# ASR / Streaming ASR Model Onboarding Playbook

本文是 `docs/agents/model_tool_agent/AGENTS.md` 的 ASR 任务补充。新 agent 接入 ASR
模型时必须先读总规范，再读本文。本文覆盖普通离线 ASR 和流式 ASR，例如
`example-org__streaming-asr-onnx`。

## 1. 任务边界

ASR 的 SURE 最小契约是：

```json
{"audio_path": "fixture/asr/asr_zh/sample.wav"} -> {"text": "..."}
```

必须满足：

- 输入以本地音频路径为主，不在 wrapper 内隐式下载数据。
- 输出主字段为 `text`，必须 JSON serializable。
- 如果模型支持语言字段，可返回 `language`，但不能把语言识别当作 ASR 文本替代。
- 第一阶段只做最小 smoke，不做完整 WER/CER leaderboard。

推荐 `model.spec.yaml`：

```yaml
task_type: "asr"
io_contract:
  input_type: "audio_path"
  output_type: "text"
  primary_field: "text"
  required_fields: ["text"]
  json_serializable: true
```

## 2. 目录与权重

遵守总规范中的 model-local checkpoint rule：

```text
sure/models/{model}/
├── .runtime/
│   ├── modelscope_cache/
│   ├── huggingface/
│   └── source/                 # 需要源码运行时才使用
├── checkpoints/                # 显式本地权重；如已在 .runtime，可为空
├── fixture/asr/<subtask>/
├── artifacts/
├── docker_artifacts/           # Docker 验证输出可单独放这里
├── model.py
├── server.py
├── validate.py
├── local_uv_setup.sh
├── local_uv_validate.sh
├── Dockerfile
├── docker_build.sh
└── docker_validate.sh
```

下载 agent 只负责把模型权重、源码或 provider cache 放进 `.runtime/`，并写
`weights_manifest.json`。SURE model tool-agent 负责 wrapper、环境、Docker 和验证。

如果只需要仓库中的部分权重文件，必须在 `weights_manifest.json` 和任务文档中写清楚。
某些流式 ONNX ASR 模型的 SURE smoke 只需要 `deployment/models/chunk-*ms-model/` 中的
ONNX 文件和 `tokens.txt`，不需要下载 demo 视频或桌面 app 包。

## 3. Fixture

共享 fixture 库中的 ASR 代表样例位于：

```text
fixtures/tasks/asr/qwen3_asr_smoke/
```

索引见 `fixtures/tasks/asr/README.md`。接入新模型时，优先从该目录选择样例复制到
模型目录；如果模型需要特殊语言、采样率或格式，再创建 model-local fixture 并记录原因。

ASR fixture 应放在：

```text
fixture/asr/asr_en/
fixture/asr/asr_zh/
```

每个子任务建议 1-3 条，最多 5 条。`gt.jsonl` 每行至少包含：

```json
{"key": "sample_1", "audio": "sample.wav", "ground_truth": "reference text", "task": "ASR"}
```

注意：

- 中英文模型至少各放一条 smoke 样本，除非模型明确单语。
- 如果是流式 ASR，要选择短音频和一条稍长音频，避免只验证单 chunk。
- `validate.py` 不应因转写内容和 GT 有小差异直接失败；第一阶段 contract 只要求
  输出字段和类型正确。WER/CER 可作为 artifact 记录。
- ASR metric 脚本索引：
  - `src/sure_eval/evaluation/asr/metrics.py`
  - `src/sure_eval/evaluation/asr/wenet_compute_cer.py`
- ASR metric 环境：
  - `src/sure_eval/evaluation/asr/pyproject.toml`
  - `src/sure_eval/evaluation/asr/README.md`

`CERMetric` / `WERMetric` 是正式 SURE ASR scoring 的类名封装，内部使用
`SUREEvaluator.evaluate("ASR", ...)` 和 `asr/wenet_compute_cer.py`，不是旧的简单
edit-distance 实现。

强制经验规则：

- ASR re-onboarding、model onboarding、Docker validation 或后续 evaluation 产出的
  WER/CER artifact 必须调用 SURE ASR metric route 中的 `CERMetric` / `WERMetric`，
  不得在 `validate.py`、一次性脚本或 wrapper 中重新实现第二套 edit-distance /
  normalization 逻辑。
- 允许 `validate.py` 在 `/sure_onboard` local smoke 阶段只保存
  `sample_output.json`、reference 和 prediction；metric 可以在推理后复算，但复算仍
  必须调用 `CERMetric` / `WERMetric`。
- 如果 metric 环境缺依赖，必须修复 evaluation 环境或使用项目已有可用环境；不得退回
  手写 CER/WER。
- `sample_output.json`、`verdict.json` 或 metric summary 中应记录 metric backend，
  例如 `sure_eval.evaluation.tasks.asr.metrics.CERMetric`。
- 如果只是修正 metric 口径，不允许重新跑模型推理；必须复用已有
  `sample_output.json` 中的 prediction/reference。
- 出现手写 edit distance、临时 CER/WER 或 metric-only 重新推理时，读取
  `references/memory/bad_cases/asr_metric_bypass.md`。

## 4. Backend 选择

优先级：

1. 纯 Python 推理栈，例如 FunASR、Transformers、Whisper：优先 `uv`，同时可补
   Docker。
2. 需要 CUDA 编译、ONNXRuntime CUDA provider、sherpa-onnx GPU、k2 或复杂 C++
   扩展：优先 Docker。
3. 本地 uv 如果只能 CPU fallback，不能伪装成 GPU pass。必须在
   `artifacts/*gpu*.json` 记录失败原因。

GPU 是首选。只有在用户明确接受 CPU smoke，或硬件客观不满足时，才能记录
`passed_with_limitation`。

## 5. 普通 ASR Wrapper

`model.py` 建议提供：

- `ModelWrapper.__init__(model_root=None, device=None)`
- `load()`
- `predict({"audio_path": "<wav>"}) -> {"text": "...", "language": optional, "raw": {...}}`
- `health()`

必须做到：

- 所有路径从 `MODEL_DIR` 或 `weights_manifest.json` 解析，不写死用户 home。
- import 阶段不加载大权重；`load()` 阶段再加载。
- `predict()` 不修改原始 fixture。
- 音频重采样应显式记录，优先复用 `librosa`、`soundfile`、`torchaudio` 或上游 API。

## 6. 流式 ASR 要点

流式 ASR 不能只按离线模型处理。必须额外验证：

- chunk 配置是否来自模型权重目录，例如 `chunk-960ms-model`。
- 每个 chunk 的 `accept_waveform()` / `decode_stream()` / `input_finished()` 调用顺序。
- 结尾是否需要 tail silence padding。部分模型的短音频如果没有约 1s tail
  padding，会丢尾词。
- provider 必须真实为 CUDA；日志中出现 `Fallback to cpu` 必须失败。

sherpa-onnx 运行经验：

- PyPI `sherpa-onnx==1.13.2` 在当前 host 上只有 `CPUExecutionProvider`。
- 本地 uv 请求 CUDA 会打印 `Please compile with -DSHERPA_ONNX_ENABLE_GPU=ON` 并
  fallback 到 CPU；脚本必须检测并退出非 0。
- Docker 中可从源码构建 `sherpa_onnx-...+cuda`。
- ONNXRuntime GPU 需要 cuDNN 动态库；`docker_validate.sh` 可通过
  `LD_LIBRARY_PATH` 注入：

```bash
/opt/conda/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib
```

- 不要把 Docker 中编译的 sherpa-onnx 扩展复制回 host `.venv`；host glibc 低于
  Docker 构建环境时，复制的扩展会加载失败。

## 7. local_uv_validate.sh

本地验证必须：

- 清晰设置 `SHERPA_ONNX_PROVIDER=cuda` 或任务对应的 GPU 参数。
- 写 `artifacts/local_uv_validate*.log`。
- 如果发现 CPU fallback，退出非 0。
- 验证后写结构化 JSON，例如 `artifacts/local_uv_validation.json` 或
  `artifacts/gpu_status.json`。

示例策略：

```bash
SHERPA_ONNX_PROVIDER="${SHERPA_ONNX_PROVIDER:-cuda}" \
.venv/bin/python validate.py 2>&1 | tee artifacts/local_uv_validate.stdout.log

if grep -q "Fallback to cpu" artifacts/local_uv_validate.stdout.log; then
  echo "Requested CUDA provider but runtime fell back to CPU." >&2
  exit 5
fi
```

## 8. Docker 验证

Docker 验证必须证明：

- 镜像内 Python/venv 被使用，不挂载 host `.venv`。
- 模型代码、fixture、权重、artifacts 通过绝对路径挂载。
- `validate.py` 在容器内跑完，并写 `validation.log`。
- GPU provider 真实可用。

镜像命名遵守总规范。ASR 示例：

```text
registry.example.com/sure/sure_asr_<name>:v1.0
registry.example.com/sure/sure_streaming_asr_zh_en:v1.0
```

推送后必须用 `docker pull` 验证 registry 可拉取，并记录 digest。

Docker/registry/GPU 查询默认清代理：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy <command>
```

## 9. Verdict 标准

ASR `verdict.json` 至少记录：

- `status`
- `task: "ASR"`
- backend
- validated subtasks，例如 `asr_en`, `asr_zh`
- local uv status
- docker status
- image tag 和 registry digest
- 是否有 GPU fallback、OOM 或 VRAM 限制

不能把“CPU 能跑”写成“GPU OK”。如果 GPU 失败但 Docker GPU 通过，状态应类似：

```json
{
  "status": "docker_gpu_passed_local_uv_gpu_blocked",
  "local_uv": {"status": "failed_gpu"},
  "docker": {"status": "passed_gpu"}
}
```

## 10. 新 ASR 模型接入检查表

- [ ] 已读 `AGENTS.md` 和本文。
- [ ] `model.spec.yaml` 的 `task_type` 与 io_contract 正确。
- [ ] 权重路径收敛到 model-local `.runtime/` 或 `checkpoints/`。
- [ ] `weights_manifest.json` 写清 provider、repo id、本地路径。
- [ ] fixture 有 `fixture/asr/.../gt.jsonl` 和音频。
- [ ] `validate.py` 跑 import/load/infer/contract。
- [ ] 本地 uv GPU 通过，或失败原因已结构化记录。
- [ ] Docker GPU 通过。
- [ ] Docker 镜像 push 和 pull digest 已记录。
- [ ] 没有把权重、fixture、大 cache bake 进镜像。
