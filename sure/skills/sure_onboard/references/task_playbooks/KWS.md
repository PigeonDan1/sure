# KWS Model Onboarding Playbook

本文是 `docs/agents/model_tool_agent/AGENTS.md` 的 KWS 任务补充，基于
`daydream_factory__keyword-spot-fsmn-ctc-wenwen` 和 WekWS 的接入经验。

## 1. 任务边界

KWS / keyword spotting 是“音频 -> 是否触发关键词”。最小输入：

```json
{
  "audio_path": "fixture/kws/positive.wav",
  "keywords": "你好问问,嗨小问"
}
```

输出建议：

```json
{
  "detected": true,
  "keyword": "嗨小问",
  "score": 0.93,
  "task": "KWS"
}
```

`model.spec.yaml` 可使用：

```yaml
task_type: "kws"
io_contract:
  input_type: "audio_path"
  output_type: "keyword_detection"
  primary_field: "detected"
  required_fields: ["detected", "keyword", "score"]
  json_serializable: true
```

第一阶段必须同时验证 positive 和 negative fixture：正例应触发目标关键词，负例应不触发。

## 2. 目录与权重

推荐结构：

```text
sure/models/{model}/
├── .runtime/
│   ├── modelscope_cache/<repo-id>/
│   └── source/wekws/
├── checkpoints/                 # 可为空
├── fixture/kws/
├── artifacts/
├── docker_artifacts/
├── model.py
├── validate.py
├── local_uv_setup.sh
├── local_uv_validate.sh
├── Dockerfile
├── docker_build.sh
└── docker_validate.sh
```

下载 agent 职责：

1. 下载 ModelScope checkpoint 到 `.runtime/modelscope_cache/...`。
2. 下载或 materialize WekWS 源码到 `.runtime/source/wekws`。
3. 不把大数据集全量下载进模型目录。

SURE model tool-agent 职责：

1. patch 上游兼容问题。
2. 配置 local uv 和 Docker。
3. 写 wrapper、fixture、validate、artifacts。

## 3. 数据与 Fixture

共享 fixture 库中的 KWS 代表样例位于：

```text
fixtures/tasks/kws/wenwen_smoke/
```

索引见 `fixtures/tasks/kws/README.md`。接入新模型时，优先从该目录选择正负样例复制到
模型目录；如果关键词不同，可以保留该格式并替换为 model-local fixture。

KWS metric namespace:

```text
src/sure_eval/evaluation/kws/
```

`/sure_onboard` 的本地验证仍以 task-local `validate.py` 同时验证正负样例为最小通过
条件；但 KWS 经验资产不能降级为只有 smoke。只要 wrapper 产出
`sample_output.json`，后续 metric enrichment 或 evaluation 应继续调用 canonical KWS
route，而不是只读 `validate.py` 的正负例断言。

推荐 runner：

```bash
PYTHONPATH=src python scripts/run_kws_metric_pipeline.py \
  --reference-jsonl <model_dir>/fixture/kws/gt.jsonl \
  --sample-output <model_dir>/artifacts/sample_output.json \
  --threshold <threshold> \
  --output <model_dir>/artifacts/kws_metric_report.json
```

该 runner 应直接调用 canonical task route：

```text
sure_eval.evaluation.tasks.kws.pipeline.evaluate_kws_files
```

report 必须保留兼容字段：

```text
ok, input_mode, metrics, rows, summary
```

同时记录新框架字段：

```text
pipeline_id, input_contract, input_files, pipeline_trace
```

正式评测必须记录 `accuracy`、`precision`、`recall`、`f1`、
`false_reject_rate`、`false_alarm_rate`、`false_alarm_per_hour` 和 `det_curve`。
DET 阈值扫描语义对齐 WekWS `compute_det.py` / `compute_det_ctc.py`：正例低于阈值
或关键词错误计为 false reject，负例高于阈值计为 false alarm，并按负例音频时长
换算 false alarm per hour。

KWS metric uv project 经验：

```text
src/sure_eval/evaluation/nodes/scoring/wekws_det/pyproject.toml
src/sure_eval/evaluation/nodes/scoring/wekws_det/.cache/uv
src/sure_eval/evaluation/nodes/scoring/wekws_det/.venv
```

不要把长期使用的 KWS metric cache 放到 `/tmp`。

KWS metric 当前支持三种输入模式：

| input_mode | 必需输入 | 说明 |
| --- | --- | --- |
| `sure_json` | `reference_jsonl + sample_output` | SURE wrapper 标准输出 |
| `wekws_score_ctc` | `wekws_label_file + wekws_score_file + keyword` | WekWS `score_ctc.py` 输出 |
| `wekws_frame_score` | `wekws_label_file + wekws_frame_score_file + keyword` | WekWS `score.py` frame-level 输出 |

KWS fixture 必须有正负样例：

```text
fixture/kws/
├── positive.wav
├── negative.wav
└── gt.jsonl
```

`gt.jsonl` 示例：

```json
{"key": "pos_1", "audio": "positive.wav", "keywords": "你好问问,嗨小问", "expected_detected": true, "expected_keyword": "嗨小问", "task": "KWS"}
{"key": "neg_1", "audio": "negative.wav", "keywords": "你好问问,嗨小问", "expected_detected": false, "expected_keyword": null, "task": "KWS"}
```

Mobvoi hotword 数据集经验：

- `daydream-factory/mobvoi_hotword_dataset` 有正负样例。
- 全量音频 archive 约 17.9GB，不应为 smoke 全量下载。
- 可以只下载小 metadata/resources tgz，并对 archive 做 range/stream 抽取选定 WAV。
- 如果 `data/datasets` 是只读 symlink，可把 OREF smoke 放到其它允许写入的位置，但结构仍要保持
  `audio/ + sample.jsonl`。

## 4. WekWS 兼容补丁

当前 WekWS 源码和 ModelScope 包可能不完全匹配。已知 patch：

- `stream_kws_ctc.py` 依赖 `tools.make_list` 中的 `query_token_set`、
  `read_lexicon`、`read_token`，但当前 GitHub 版本可能没有这些函数。
- 需要在 model-local WekWS copy 中恢复这些 helper，读取随 checkpoint 提供的
  `tokens.txt` / `lexicon.txt`。
- FSMN-CTC recipe 使用 `token_id - 1` 生成 dict。ModelScope 包的 `tokens.txt`
  是原始 id，所以 model-local `read_token` 必须应用同样 `-1` shift。
- 如果不做这个 shift，正例 keyword tokens 会错位，官方正例会被拒绝。

任何 patch 必须只改 `.runtime/source/wekws` 的 model-local copy，并写入 `BRIDGE.md`
或 `patch_report.json`。

## 5. Backend 选择

KWS 优先 GPU。经验：

- WekWS 可用 local uv 跑通 GPU。
- 如果要给外部用户或集群使用，应提供 Docker。
- local uv 可以作为快速调试路径，但 Docker 是更稳定的评测交付路径。

本地验证可用环境变量：

```bash
WEKWS_GPU=0
WEKWS_KEYWORDS="你好问问,嗨小问"
WEKWS_THRESHOLD=0.0
```

阈值用于 smoke 时可以较低，正式评测必须由任务配置或 benchmark 明确。

## 6. Wrapper 要求

`model.py` 应：

- 解析 `audio_path`、`keywords`、`threshold`。
- 加载 WekWS checkpoint、config、tokens、lexicon。
- 支持 positive/negative smoke。
- 返回 `detected`、`keyword`、`score`，并保留 `raw` 诊断字段。
- 不在 `predict()` 内下载数据或修改 checkpoint。

示例输出：

```json
{
  "detected": true,
  "keyword": "嗨小问",
  "score": 0.91,
  "task": "KWS",
  "raw": {"decoder": "offline_ctc_prefix_beam_search"}
}
```

## 7. local_uv_validate.sh

local uv 验证必须：

- 检查 `.venv` 存在。
- 设置 GPU，或从 `WEKWS_GPU` 读取。
- 跑完整 `validate.py`。
- 写 `artifacts/validation.log` 和 `artifacts/verdict.json`。
- `verdict.json` 中记录 `cuda_available`。

当前已验证状态示例：

```json
{
  "status": "passed",
  "task": "KWS",
  "backend": "uv",
  "gpu": 0,
  "cuda_available": true,
  "decoder": "offline_ctc_prefix_beam_search",
  "note": "Mobvoi positive fixture detects 嗨小问 and negative fixture is rejected."
}
```

## 8. Docker 验证

Docker 验证必须挂载：

- SURE 基础代码。
- model-local `model.py` / `validate.py` / `config.yaml`。
- `fixture/`。
- `.runtime/source/wekws`。
- `.runtime/modelscope_cache`。
- `artifacts` 或 `docker_artifacts` 输出目录。

容器内可建立 `.venv` 链接到镜像内环境：

```bash
ln -sfn /opt/kws_wenwen_venv /workspace/sure-eval/.venv
```

已验证镜像命名：

```text
registry.example.com/sure/sure_kws_wenwen:v1.0
```

注意：

- `WEKWS_GPU` 在容器内通常设为 `0`，宿主 GPU 由 `docker run --gpus device=<id>`
  决定。
- Docker/registry/GPU 查询清代理。

## 9. Verdict 标准

KWS 通过标准：

- import/load/infer/contract 通过。
- 正例检测到指定关键词。
- 负例不触发。
- `detected` 是 bool。
- `keyword` 为 string 或 null。
- `score` 可 JSON serializable。
- `cuda_available` 和 decoder 写入 artifact。

如果只有负例 smoke，没有正例，不能标为完整通过，只能标为
`passed_load_shape_only` 或等价状态，并要求补正例 fixture。

## 10. 新 KWS 模型接入检查表

- [ ] 已读 `AGENTS.md` 和本文。
- [ ] 明确关键词列表和阈值。
- [ ] checkpoint 和 toolkit source 都在 model-local `.runtime`。
- [ ] WekWS/token/lexicon 兼容 patch 已记录。
- [ ] fixture 同时有 positive 和 negative。
- [ ] `validate.py` 对正负例分别断言。
- [ ] local uv GPU 通过。
- [ ] Docker GPU 通过。
- [ ] 镜像 push/pull digest 已记录。
