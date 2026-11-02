# TTA Model Onboarding Playbook

本文是 `../AGENTS.md` 的 TTA(text-to-audio,文本生成音频/音效)任务补充。

## 1. 任务边界

TTA 在当前 SURE harness 中按"文本 caption -> 生成音频"处理。与 TTS 的区别:
TTA 合成的是通用声音(音效、环境声、音乐片段),不是带音色参考的人声语音,
因此最小输入只有文本,没有 prompt audio:

- `text` / `caption`: 描述目标声音的文本。

最小输出:

```json
{"audio_path": "artifacts/outputs/example.wav"}
```

`model.spec.yaml` 推荐:

```yaml
task_type: "tta"
io_contract:
  input_type: "text"
  output_type: "audio_path"
  primary_field: "audio_path"
  required_fields: ["audio_path"]
  nonempty_fields: ["audio_path"]
  json_serializable: true
```

## 2. Fixture

共享 fixture 库中的 TTA 代表样例位于:

```text
fixtures/tasks/tta/tta_smoke/
```

索引见 `fixtures/tasks/tta/README.md`。该 fixture 是 metadata-only:只含
caption,不含音频文件。接入新模型时复制 caption 行到模型目录即可;如果评测
需要参考音频(见下),由用户提供并记录在 model-local fixture 中,不要把参考
音频提交进共享 fixture。

TTA metric namespace:

```text
src/sure_eval/evaluation/tasks/tta/
```

`/sure_onboard` 的本地验证仍以 task-local `validate.py` 检查
import/load/infer/audio contract 为最小门槛;正式 TTA 指标必须通过 SURE
evaluation 工具链计算,不允许用手写相似度或文件大小代替。

## 3. 正式评测

TTA 引擎 manifest 声明 9 个默认指标,分三类输入契约:

- 分布质量:`fad`、`fd`、`kl`、`is`(audioldm_eval backend),需要
  `prediction_audio` + `reference_audio`,按 corpus 分布聚合。
- 文本-音频对齐:`clap_score`,需要 `prediction_audio` + 行内 `caption`。
- 美学质量:`audiobox_ce`、`audiobox_cu`、`audiobox_pc`、`audiobox_pq`,
  只需要 `prediction_audio`。

没有参考音频时,只能可靠计算 `clap_score` 与 AudioBox 美学四项;分布指标
需要用户提供与预测对齐的参考音频集合,缺失时必须记录为
`blocked` / `not_available`,不能静默跳过。

推荐入口:

```bash
sure-eval metric describe tta \
  --language any \
  --metrics clap_score,audiobox_ce,audiobox_cu,audiobox_pc,audiobox_pq \
  --output /tmp/tta_pipeline.json \
  --json

sure-eval metric run \
  --pipeline /tmp/tta_pipeline.json \
  --samples-jsonl /tmp/tta_samples.jsonl \
  --output-dir /tmp/sure_eval/tta_eval \
  --device cuda \
  --validate-env \
  --json
```

`samples_jsonl` 每行必须显式给出角色:

```json
{"sample_id":"tta_smoke_001","prediction_audio":"outputs/gen_001.wav","caption":"A dog barking loudly in a park","language":"any"}
```

带参考音频时追加 `"reference_audio":"ref_001.wav"`。

## 4. 验证标准

`validation.log` 至少要有:

- `VALIDATE_SPEC passed`
- `VALIDATE_IMPORT passed`
- `VALIDATE_LOAD passed`
- `VALIDATE_CONTRACT passed`

并记录输出音频路径、采样率、样本数、文件大小和对应 caption。若已运行正式
指标,记录 `report.json`、`pipeline_description.json`、metric backend 和
provider blocker。

## 5. 新 TTA 模型接入检查表

- [ ] 已读 `../AGENTS.md` 和本文。
- [ ] fixture 有 caption 文本;参考音频(如需要)由用户提供并记录来源。
- [ ] wrapper 输出为新音频文件,不覆盖 fixture。
- [ ] 权重与 runtime cache 都在 model-local `.runtime`。
- [ ] 本地/Docker 验证通过,输出音频元信息写入 artifacts。
- [ ] 正式指标通过 `sure-eval metric describe/run` 计算;缺参考音频时分布
      指标记录为 blocked,没有手写替代指标。
