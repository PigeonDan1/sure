# Speech Understanding Multitask Onboarding Playbook

本文是 `docs/agents/model_tool_agent/AGENTS.md` 的语音理解多任务补充，基于
`asr_kimi_audio` / Kimi-Audio-7B-Instruct 的接入经验。适用任务：

```text
ASR, S2TT, SER, SLU, GR, SD, SA-ASR
```

这类模型不是单一 ASR wrapper，而是统一音频理解模型。新 agent 接入同类模型时，
必须先读总规范和本文；如果只做普通 ASR，再读 `ASR.md`。

## 1. 任务边界

多任务语音理解的核心是“一套模型，多套 task-specific wrapper 方法和 fixture”。
不要把所有任务退化成 ASR 文本转写。

推荐任务定义：

| 任务 | 含义 | wrapper 方法 | 输出 |
|------|------|--------------|------|
| ASR | automatic speech recognition | `predict(audio_path)` | `text` |
| S2TT | speech-to-text translation | `translate(audio_path, source_language, target_language)` | translated `text` |
| SER | speaker emotion recognition | `recognize_emotion(audio_path)` | `text`, normalized `label` |
| SLU | spoken language understanding | `understand(audio_path, prompt=...)` | `text`, normalized choice `label` |
| GR | gender recognition | `recognize_gender(audio_path)` | `text`, normalized `label` |
| SD | speaker diarization | `diarize(audio_path)` | MeetEval-loadable annotation |
| SA-ASR | speaker-attributed ASR | `transcribe_with_speakers(audio_path)` | MeetEval-loadable annotation |

`model.spec.yaml` 至少要声明：

```yaml
task: "ASR"
supported_tasks: ["ASR", "S2TT", "SER", "SLU", "GR"]
io_contract:
  task_outputs:
    ASR:
      text: string
    S2TT:
      text: string
    SER:
      text: string
      label: "one of [neu, hap, ang, sad]"
    SLU:
      text: string
    GR:
      text: string
      label: "one of [male, female]"
    SD:
      segments: list
    SA-ASR:
      segments: list
```

## 2. 目录与权重

推荐结构：

```text
sure/models/{model}/
├── .runtime/
│   ├── modelscope_cache/models/<owner>/<repo>/
│   └── <upstream_source_if_needed>/
├── kimia_infer/                       # 或 model-local upstream runtime copy
├── fixture/
│   ├── asr/<dataset>/gt.jsonl
│   ├── s2tt/<dataset>/gt.jsonl
│   ├── ser/<dataset>/gt.jsonl
│   ├── slu/<dataset>/gt.jsonl
│   └── gr/<dataset>/gt.jsonl
├── artifacts/
├── docker_artifacts_multitask/
├── model.py
├── validate.py                        # 可保留单任务/ASR smoke
├── validate_multitask.py              # 多任务唯一入口
├── docker_validate.sh                 # 可保留旧 ASR smoke
└── docker_validate_multitask.sh       # 多任务 Docker 入口
```

Kimi-Audio 权重经验：

- ModelScope repo: `moonshotai/Kimi-Audio-7B-Instruct`
- 本地路径：
  `.runtime/modelscope_cache/models/moonshotai/Kimi-Audio-7B-Instruct`
- 权重约 40GB，必须 model-local，不能 bake 进 Docker。
- `weights_manifest.json` 必须检查大文件完整性，例如 35 个
  `model-*-of-35.safetensors`、`whisper-large-v3/model.safetensors`、
  `audio_detokenizer/model.pt`、`vocoder/model.pt`。

## 3. Fixture 规则

共享 fixture 库中的多任务语音理解样例已经拆成原子任务 fixture。组合模型按
实际支持任务选择：

```text
fixtures/tasks/asr/qwen3_asr_smoke/
fixtures/tasks/s2tt/kimi_audio_s2tt_smoke/
fixtures/tasks/ser/kimi_audio_ser_smoke/
fixtures/tasks/slu/kimi_audio_slu_smoke/
fixtures/tasks/gr/kimi_audio_gr_smoke/
fixtures/tasks/sd/README.md
fixtures/tasks/sa_asr/README.md
```

组合索引见 `fixtures/tasks/speech_understanding/README.md`。接入新模型时，按
子任务打开原子 fixture index，不要默认读取或复制所有子任务。

多任务 fixture 必须按任务分目录。不能用一个 ASR 样本假装覆盖全部任务。

```text
fixture/asr/aishell1-test/gt.jsonl
fixture/s2tt/covost2-en2zh/gt.jsonl
fixture/ser/iemocap/gt.jsonl
fixture/slu/mmsu/gt.jsonl
fixture/gr/librispeech-test-clean/gt.jsonl
fixture/sd/librispeech-two-speaker/gt.jsonl
fixture/sa_asr/librispeech-two-speaker/gt.jsonl
```

每个 `gt.jsonl` 每行至少包含：

```json
{"key": "sample_1", "audio": "sample.wav", "ground_truth": "...", "task": "ASR"}
```

任务特有要求：

- ASR: `ground_truth` 是转写文本，可算 CER/WER。
- S2TT: `ground_truth` 是目标语言翻译，必须包含 `target_language`，例如 `zh`。
- SER: 必须使用真实情感数据，例如 IEMOCAP；标签限定 `neu/hap/ang/sad`。
  不要用普通 ASR 音频替代 SER。
- SLU: 可以使用 MMSU 这类选择题音频。fixture audio 中应已经包含问题和选项。
- GR: 标签限定 `male/female`，可使用 LibriSpeech 等带说话人性别信息的数据。
- SD: 输出必须是 MeetEval 可读取的 diarization annotation；推荐 DER 使用 RTTM。
- SA-ASR: 输出必须是 MeetEval 可读取的 speaker-attributed ASR annotation；常用
  STM/CTM/SegLST，不能退化成普通 ASR `key<TAB>text`。
- 多任务 metric 脚本索引：
  - ASR task route: `src/sure_eval/evaluation/tasks/asr/`
  - S2TT task route: `src/sure_eval/evaluation/tasks/s2tt/`
  - SER / GR classification route: `src/sure_eval/evaluation/tasks/classification/`
  - SLU route: `src/sure_eval/evaluation/tasks/slu/`
  - SD route: `src/sure_eval/evaluation/tasks/sd/`
  - SA-ASR route: `src/sure_eval/evaluation/tasks/sa_asr/`
  - SA-ASR conversion profile: `src/sure_eval/evaluation/conversion/sa_asr__cpwer/`
  - SA-ASR G-STAR normalization node: `src/sure_eval/evaluation/nodes/normalization/gstar_norm/`
  - MeetEval scoring node: `src/sure_eval/evaluation/nodes/scoring/meeteval/`
  - prompt choice normalization node: `src/sure_eval/evaluation/nodes/normalization/prompt_norm/`
  - generic classification scoring node: `src/sure_eval/evaluation/nodes/scoring/classify/`
- 重依赖 metric 使用 node-local `pyproject.toml` 和 `.venv`；SER/GR/SLU 的
  `prompt_norm` / `classify` 为轻量确定性节点，不需要单独大模型环境。
- SA-ASR/SD 等 annotation 任务要额外检查 conversion profile。若模型输出不是 metric
  直接可读格式，需要在模型 artifact 或 metric `output_dir` 中保留转换脚本与说明；
  包级 `src/sure_eval/evaluation/conversion/{task_slug}__{metric_slug}/` 只放可复用代表
  profile。同时确认 `conversion_trace` 出现在报告中。

样本数建议每任务 2-3 条，最多 5 条。多任务 smoke 的目标是验证链路和契约，
不是追求 benchmark 统计显著性。

## 4. Wrapper 设计

`model.py` 必须明确拆分任务方法，不能让所有任务调用 `predict()`。

Kimi-Audio 经验方法：

```python
ModelWrapper.predict(audio_path)              # ASR
ModelWrapper.translate(audio_path, ...)       # S2TT
ModelWrapper.recognize_emotion(audio_path)    # SER
ModelWrapper.understand(audio_path, prompt)   # SLU
ModelWrapper.recognize_gender(audio_path)     # GR
```

生成参数建议：

- `text_temperature=0.0`
- `text_top_k=5`
- SER/GR/SLU 这类分类任务 `max_new_tokens=16`
- ASR/S2TT 可使用更大 `max_new_tokens`，例如 128。

### S2TT

Kimi-Audio 的 S2TT 实现是两阶段：

1. `predict(audio_path)` 得到 transcript。
2. 用 text-only prompt 翻译 transcript。

必须在 `raw` 中记录：

```json
{"stage": "asr_then_text_translate", "transcript": "...", "target_language": "zh"}
```

如果 S2TT 输出仍是源语言，优先检查 `translate()` 是否退回 ASR-only path。

S2TT metric 输入必须按 metric 明确准备，不要假设所有 metric 都只需要 `ref/hyp`：

| metric backend | 输入文件 | 聚合 | 用途 |
| --- | --- | --- | --- |
| `scoring/sacrebleu` | `hyp + ref`，每行 `key<TAB>text` | corpus metric | BLEU / chrF++ 传统可复现锚点 |
| `scoring/xcomet_xl` | `src + hyp + ref`，每行 `key<TAB>text` | segment mean | `Unbabel/XCOMET-XL` 主语义质量 |
| `scoring/bleurt_20` | `hyp + ref`，每行 `key<TAB>text` | segment mean | `BLEURT-20` 互补语义质量 |

S2TT metric 入口：

```text
sure_eval.evaluation.tasks.s2tt.pipeline.evaluate_s2tt_files
```

SacreBLEU 使用 scoring backend 自己的轻量 uv project，cache/env 也放在同一目录：

```text
src/sure_eval/evaluation/nodes/scoring/sacrebleu/pyproject.toml
src/sure_eval/evaluation/nodes/scoring/sacrebleu/.cache/uv
src/sure_eval/evaluation/nodes/scoring/sacrebleu/.venv
```

XCOMET-XL 与 BLEURT-20 使用各自 scoring node 的独立 uv project：

```text
src/sure_eval/evaluation/nodes/scoring/xcomet_xl/
src/sure_eval/evaluation/nodes/scoring/bleurt_20/
```

长期使用的 metric cache 或模型权重不要放到 `/tmp`。

### SER

SER prompt 应要求只输出固定标签：

```text
Recognize the speaker emotion in the following audio.
Answer with exactly one label from: neu, hap, ang, sad.
```

必须做 label normalization，例如：

```text
neutral/calm -> neu
happy/joy -> hap
angry/anger -> ang
sadness -> sad
```

如果输出是完整句子，不能直接当通过；应记录 `UNPARSED_LABEL` 或 mismatch。

### GR

GR prompt：

```text
Recognize the speaker gender in the following audio.
Answer with exactly one label from: male, female.
```

必须 normalize：

```text
man/m -> male
woman/f -> female
```

### SLU

SLU 必须走 direct-audio understanding：

```python
ModelWrapper.understand(audio_path, prompt=...)
```

不要把 SLU 改成 ASR 再 text-only reasoning。Kimi-Audio 的经验是：fixture audio 已经包含
spoken question 和 choices，wrapper 只需要加任务指令，要求输出 `A/B/C/D`。

建议指令：

```text
The audio may contain a question, context, and answer choices.
Reason silently from the audio and output exactly one uppercase letter: A, B, C, or D.
Do not explain.
```

必须实现 choice extraction，兼容：

- `A`
- `答案是 A`
- `The answer is A`
- 带中文/英文标点的单字母输出。

## 5. validate.py 与 validate_multitask.py

多任务模型必须提供 `validate_multitask.py`。`validate.py` 可以保留为 ASR-only
回归 smoke，但不能用于五任务结论。

Kimi-Audio 明确规则：

- `validate.py`: 旧 ASR-only regression path，输出 `artifacts/sample_output.json`。
- `validate_multitask.py`: 五任务唯一正确入口，输出
  `artifacts/multitask_sample_output.json`。
- `docker_validate.sh`: 旧单任务 Docker path。
- `docker_validate_multitask.sh`: 多任务 Docker path。

`validate_multitask.py` 应支持：

```bash
KIMI_AUDIO_VALIDATE_TASKS=ASR,S2TT,SER,SLU,GR
KIMI_AUDIO_VALIDATE_TASKS=SLU
```

输出文件：

```text
validation_multitask.log
multitask_sample_output.json
ref_asr.txt / hyp_asr.txt
ref_s2tt.txt / hyp_s2tt.txt
ref_ser.txt / hyp_ser.txt
ref_slu.txt / hyp_slu.txt / prompt_slu.jsonl
ref_gr.txt / hyp_gr.txt
```

状态语义：

- `status=COMPLETE`: 运行完成，metrics 可用。低分、错样本、`MISMATCH` 是模型结果，
  不是 harness 失败。
- `status=ERROR` 或非 0 exit: 运行时失败，例如缺权重、缺 fixture、CUDA OOM、代码异常。

不要把模型答错称作 validation failure。

Kimi-Audio 这类生成式语音理解模型可能在文本最前面稳定生成孤立的 `!` / `！`
decode artifact。不能先把它当普通文本 normalization 处理，必须先拿 token 级证据：

- raw generated text token ids；
- first token 的 per-token decode；
- special token ids，例如 `kimia_text_blank` / `kimia_text_eos`。

Kimi-Audio 已验证根因是 generated text stream 的首 token 为 id `0`，而该 tokenizer
中 `decode([0]) == "!"`；它不是 `<|im_kimia_text_blank|>`，也不能用
`skip_special_tokens` 解决。修复应在 Kimi text detokenize / generation protocol
边界过滤这个首位 stream-boundary token，再写入 `prediction`、`hyp_*.txt` 和 metric
report。不要只在 wrapper 字符串清洗或 metric 计算时临时去掉 `!`，否则会掩盖根因。

遇到该问题时读取：

```text
references/memory/bad_cases/kimi_audio_leading_bang_token.md
```

## 6. Evaluation

正式评估应优先复用 task routes；`SUREEvaluator` 和分类兼容入口可以保留，但新报告中
必须能看到 pipeline trace。

- ASR: `sure_eval.evaluation.tasks.asr.metrics.CERMetric` / `WERMetric`，至少 CER。
- S2TT: `sure_eval.evaluation.tasks.s2tt.pipeline.evaluate_s2tt_files`，至少 BLEU / chrF。
- SER / GR: `sure_eval.evaluation.tasks.classification.pipeline.evaluate_classification_files`。
- SLU: `sure_eval.evaluation.tasks.slu.pipeline.evaluate_slu_files`，先走
  `normalization/prompt_norm`，再走 `scoring/classify`。
- SD: `sure_eval.evaluation.tasks.sd` + MeetEval。
- SA-ASR: `sure_eval.evaluation.tasks.sa_asr` + conversion profile + MeetEval。

第一阶段 success 只要求链路完整；metrics 是模型表现，不是接入是否成功的唯一条件。

`validate_multitask.py` 可以在推理完成后调用正式 metric 类并落盘
`speech_understanding_metric_report.json`。如果选择先推理、后评测，也必须用同一批
`ref_*.txt` / `hyp_*.txt` 生成 report。正式多任务指标必须落成独立 report，不能只依赖
`validate_multitask.py` 内部的临时 metric 字段。

已建立的通用入口：

```text
scripts/run_speech_understanding_metric_pipeline.py
```

该 runner 从已有 ref/hyp 文件计算指标，不重新跑模型推理。必须确认 report 中：

- `ok: true` 或结构化 blocker；
- `errors` 明确；
- ASR backend 是 SURE ASR metric route；
- S2TT backend 是 SURE S2TT route；
- SER / GR backend 是 classification route；
- SLU pipeline trace 包含 `normalization/prompt_norm` 和 `scoring/classify`；
- SD/SA-ASR report 包含 MeetEval 或 conversion trace。

如果 `S2TT` 报 `No module named 'sacrebleu'`，这是评测环境依赖缺失，不是模型推理失败。
按 `references/playbooks/env_uv.md` 修复依赖后重跑同一个 metric runner。

## 7. GPU 与内存策略

Kimi-Audio 7B 多任务验证显存压力大。

经验：

- 本地 11GB 卡容易 OOM。
- 本地 Docker 使用全部可见 GPU 可能出现自动 placement 后某张卡爆显存。
- 8bit + device_map=auto 可以用于 debug，但不保证稳定通过完整五任务。
- 完整五任务更适合 VC/A10 或更大显存队列。

常用环境变量：

```bash
KIMI_AUDIO_LOAD_IN_8BIT=1
KIMI_AUDIO_DEVICE_MAP=auto
KIMI_AUDIO_MAX_MEMORY=0:4500MiB,1:4500MiB,cpu:80GiB
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

如果出现：

```text
CUDA error: no kernel image is available for execution on the device
```

说明当前 GPU 架构和镜像/torch/kernel 不匹配；不要当作 prompt 或 wrapper 问题。

如果出现：

```text
CUDA out of memory
```

先记录 GPU、可用显存、load_in_8bit/device_map/max_memory，再决定是否转 VC/A10。

## 8. Docker 多任务入口

Docker 多任务脚本必须：

- 使用已验证镜像，不为多任务验证强制重建。
- 挂载 SURE 基础代码、`kimia_infer/`、`fixture/`、ModelScope cache。
- 不挂载 host `.venv`。
- 在容器内链接镜像 venv：

```bash
ln -sfn /opt/asr_kimi_audio_venv /workspace/sure-eval/.venv
```

Kimi-Audio 已验证镜像：

```text
registry.example.com/sure/sure_asr_kimi_audio:v1.0
```

本地 Docker 调试：

```bash
KIMI_AUDIO_LOAD_IN_8BIT=1 \
KIMI_AUDIO_DEVICE_MAP=auto \
KIMI_AUDIO_MAX_MEMORY=0:4500MiB,1:4500MiB,cpu:80GiB \
sure/models/asr_kimi_audio/docker_validate_multitask.sh
```

绑定本地 GPU：

```bash
DOCKER_GPUS=device=0 sure/models/asr_kimi_audio/docker_validate_multitask.sh
```

调试单任务：

```bash
KIMI_AUDIO_VALIDATE_TASKS=SLU sure/models/asr_kimi_audio/docker_validate_multitask.sh
```

## 9. VC / 集群运行

完整五任务建议通过 VC 独占资源跑。Kimi-Audio handoff 中 A10 示例：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
vc submit \
  -p pdgpu-a10 \
  -i registry.example.com/sure/sure_asr_kimi_audio:v1.0 \
  -j kimi-audio-five-task \
  -n 1 -c 8 -m 32G -g 1 \
  -pj sjtu \
  -d <legacy-sure-eval-root>/sure/models/asr_kimi_audio \
  -e PYTHONPATH=<legacy-sure-eval-root>/src \
     KIMI_AUDIO_VALIDATE_TASKS=ASR,S2TT,SER,SLU,GR \
     KIMI_AUDIO_LOAD_IN_8BIT=0 \
     PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
     KIMI_AUDIO_MODEL_PATH=<legacy-sure-eval-root>/sure/models/asr_kimi_audio/.runtime/modelscope_cache/models/moonshotai/Kimi-Audio-7B-Instruct \
  --cmd '/opt/asr_kimi_audio_venv/bin/python validate_multitask.py'
```

VC 结果应检查：

```text
artifacts/validation_multitask.log
artifacts/multitask_sample_output.json
```

## 10. 已知 Kimi-Audio 五任务结果

历史完整 VC run 的期望结果：

```text
ASR:  COMPLETE, CER=0.0 on 3 samples
S2TT: COMPLETE, BLEU=36.7265 and chrF=26.5046 on 3 samples
SER:  COMPLETE, accuracy=1.0 on 3 IEMOCAP samples
SLU:  COMPLETE, accuracy=0.6667 on 3 MMSU smoke samples
GR:   COMPLETE, accuracy=1.0 on 3 LibriSpeech samples
```

已知 SLU mismatch：

```text
key=deixis_resolution_34bad028-6bad-4086-855a-bac86cd5f253 expected=B got=C
```

这个 mismatch 是模型表现，不是 harness 失败。

## 11. 新语音理解多任务模型接入检查表

- [ ] 已读 `AGENTS.md` 和本文。
- [ ] `model.spec.yaml` 声明 `supported_tasks`。
- [ ] 每个任务有独立 fixture，不用 ASR 样本替代 SER/SLU/GR。
- [ ] SD/SA-ASR 如被支持，输出是 MeetEval-loadable annotation，不是普通 ASR 文本行。
- [ ] wrapper 有 task-specific methods。
- [ ] `validate_multitask.py` 是多任务唯一结论入口。
- [ ] `validate.py` 如保留，明确只用于 ASR-only smoke。
- [ ] S2TT、SER、SLU、GR 有 label/text normalization。
- [ ] 多任务 metric report 记录 backend、pipeline trace 和 conversion trace。
- [ ] 输出 ref/hyp 文件和 `multitask_sample_output.json`。
- [ ] OOM、kernel image、8bit、device_map 等 GPU 状态已记录。
- [ ] Docker 多任务脚本不挂 host `.venv`，权重通过 `.runtime` 挂载。
- [ ] VC/集群运行命令和镜像 tag 已记录。
