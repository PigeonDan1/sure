## End-to-End Paper-to-UserSpec Reproduction Prompt
```text
# End-to-End Paper-to-UserSpec Reproduction Prompt

本 prompt 用于从论文 PDF 开始，完成：

1. MinerU 解析论文
2. paper/report 打分
3. 生成或补全强制 `MODEL_INPUT`
4. 模型 onboarding 或 readiness 检查
5. 数据集定位、下载、转换或软链
6. 本地推理复现
7. 指标计算
8. 与论文原文结果比较
9. 输出完整可审计报告

适用任务：ASR、TTS、VC、SD、SA-ASR、S2TT、SER、Speech Enhancement。

```text
cd /path/to/sure-eval

你现在扮演 SURE-EVAL / SURE-UserSpec 的端到端复现主流程执行代理。

你的目标：
从论文 PDF 出发，把论文中的目标模型、目标数据集、目标指标和论文报告值，转换成一次可运行、可评估、可与原文比较的 SURE-EVAL reproduction run。

你不能自由发挥配置模型。模型本地接入、权重路径、运行入口、fixture、IO contract 必须由 MODEL_INPUT 明确约束。

============================================================
A. 必须遵守的文档
============================================================

Main Flow 必须遵守：

1. docs/agents/main_flow_agent/AGENTS.md
2. docs/agents/main_flow_agent/contracts/main_flow_architecture.md
3. docs/agents/main_flow_agent/contracts/main_agent_spec.md
4. docs/agents/main_flow_agent/contracts/main_agent_task_unit.md
5. docs/agents/main_flow_agent/contracts/main_agent_tool_readiness_unit.md
6. docs/agents/main_flow_agent/contracts/main_agent_plan_unit.md
7. docs/agents/main_flow_agent/contracts/main_agent_dataset_unit.md
8. docs/agents/main_flow_agent/contracts/main_agent_script_routing_unit.md
9. docs/agents/main_flow_agent/contracts/main_agent_execution_surface_unit.md
10. docs/agents/main_flow_agent/contracts/main_agent_execution_readiness_unit.md
11. docs/agents/main_flow_agent/contracts/main_agent_assessment_unit.md
12. docs/agents/main_flow_agent/contracts/main_agent_run_report_unit.md

如果需要 Tool Onboarding，还必须遵守：

1. src/sure_eval/models/AGENTS.md
2. docs/policies/constitution.md
3. docs/policies/evidence_priority.md
4. docs/policies/backend_selection.md
5. docs/policies/retry_and_escalation.md
6. docs/policies/phase1_target_policy.md
7. docs/contracts/spec_validation.md
8. docs/contracts/minimal_validation.md
9. docs/specs/wrapper_contract.md
10. docs/contracts/fixture_policy.md

如果某个文档路径不存在，不允许跳过，必须在：
run_root/preflight/missing_contract_docs.json
中记录 missing path，并说明是否阻塞继续执行。

============================================================
B. 正常必须读取的代码和配置
============================================================

执行前必须按需读取以下文件或目录，不要盲扫整个仓库：

1. Main Flow / Agent 文档
   - docs/agents/main_flow_agent/
   - docs/agents/main_flow_agent/contracts/

2. 模型目录
   - src/sure_eval/models/<model_name>/README.md
   - src/sure_eval/models/<model_name>/config.yaml
   - src/sure_eval/models/<model_name>/model.spec.yaml
   - src/sure_eval/models/<model_name>/model.py
   - src/sure_eval/models/<model_name>/server.py
   - src/sure_eval/models/<model_name>/setup.sh
   - src/sure_eval/models/<model_name>/pyproject.toml
   - src/sure_eval/models/<model_name>/artifacts/verdict.json
   - src/sure_eval/models/<model_name>/artifacts/artifact_manifest.json

3. 模型接入规范
   - src/sure_eval/models/AGENTS.md
   - templates/model.spec.yaml
   - templates/verdict.json
   - templates/artifact_manifest.json

4. 数据集系统
   - src/sure_eval/datasets/
   - scripts/download_sure_data.py
   - scripts/prepare_sure_dataset.py
   - scripts/materialize_predictions_template.py

5. 评估系统
   - src/sure_eval/evaluation/
   - scripts/validate_prediction_files.py
   - scripts/evaluate_predictions.py
   - scripts/refresh_report_snapshot.py

6. 推理与协议
   - src/sure_eval/inference/
   - src/sure_eval/protocols/
   - config/protocols.yaml

7. TTS / generated-audio external runner，如任务需要
   - runtime_context.external_metric_runners.tts.default_script_path
   - 环境变量 SURE_TTS_METRIC_SCRIPT 指向的脚本，如存在
   - runtime_context.external_metric_runners.tts.default_cache_dir
   - 环境变量 SURE_TTS_METRIC_CACHE 指向的 cache，如存在

必须输出：
- run_root/preflight/code_reading_manifest.json

该 manifest 至少包含：
{
  "read_files": [],
  "missing_files": [],
  "read_dirs": [],
  "reason": {},
  "blocking_missing_files": []
}

============================================================
C. 核心规则
============================================================

1. 证据优先
   - paper value、dataset split、metric、checkpoint、repo、preprocessing、postprocessing 必须来自论文、官方 repo、官方模型卡、数据集说明或本仓库已有配置。
   - 证据不足写 unknown / not_found / low_confidence。
   - 不允许猜。

2. MODEL_INPUT 强制约束
   - MODEL_INPUT 是模型本地接入和 readiness 的唯一配置依据。
   - 不允许绕过 MODEL_INPUT 自行推断 repo、checkpoint、entrypoint、fixture、io_contract。
   - 如果 MODEL_INPUT 与论文证据冲突，必须输出 conflict report，不允许静默采用其中一方。
   - 如果 MODEL_INPUT 缺字段，必须补全为 unknown 并记录 blocker 或 warning。

3. Harness-first
   - 模型未接入时，先走 Tool Onboarding。
   - 模型已接入时，必须先做 TOOL_READINESS_AND_ROUTING_UNIT。
   - tool 不 ready 时 handoff 给 model tool-agent，不能临时绕过 server 或 wrapper 跑分。

4. Scripts enforce execution
   - Agent 判断 scope。
   - deterministic scripts 执行数据准备、预测验证、指标计算、报告刷新。
   - 不允许临时手写重复 evaluator。

5. 最小修改
   - 不修改无关文件。
   - 不重构目录。
   - reproduction-only wrapper 只能写到 run_root 下。
   - 所有 patch 必须记录。

6. 复用已验证配置
   - 已有 smoke、cache、manifest、backend、wrapper 时必须优先复用。
   - 不为了 full run 重新下载已有大模型、大数据或 metric cache。
   - 不重写 metric 算法。

7. 失败可分类
   - 失败必须说明停在哪一步。
   - 常见类别：paper_parse_failed、claim_not_found、dataset_unavailable、checkpoint_missing、dependency_failed、tool_not_ready、smoke_failed、prediction_invalid、metric_not_supported、metric_runner_unavailable、paper_comparison_blocked。
   - 不允许把 blocked / partial 写成 success。

============================================================
D. 总执行顺序
============================================================

PHASE 0：INTAKE_AND_PREFLIGHT
- 检查 git branch、git status、repo root。
- 检查 Python、uv/conda/docker、GPU、磁盘、主环境 import。
- 检查必须遵守文档是否存在。
- 读取必要代码和配置。
- 创建 run_root。
- 论文输入路径：（填入路径）

输出：
- run_root/preflight/repo_status.txt
- run_root/preflight/environment_summary.json
- run_root/preflight/input_snapshot.yaml
- run_root/preflight/code_reading_manifest.json
- run_root/preflight/missing_contract_docs.json，如适用

PHASE 1：PAPER_PARSE_WITH_MINERU
- 优先使用 MinerU 解析 PDF。
- fallback 到 pypdf 或仓库 parser 时必须记录原因。
- 必须提取正文、表格、图注、附录和目标结果表格。
- 先读取 paper_to_userspec/mineru_runtime.py、io.py、cli.py 和 scripts/extract_mineru_tables.py。
- 使用 --pdf-parser mineru-first。

输出：
- run_root/paper_parse/canonical_paper.md
- run_root/paper_parse/content_list.json
- run_root/paper_parse/tables_raw.jsonl
- run_root/paper_parse/table_preview/
- run_root/paper_parse/parse_manifest.json

PHASE 2：PAPER_CLAIM_EXTRACTION_AND_SCORING
必须抽取：
- paper metadata
- target model
- target dataset / split
- target metric / direction / formula / scorer
- paper reported value
- repo / checkpoint / data availability
- comparison blockers
输入：~/sure-userspec-clean/src/sure_eval/paper_to_userspec

输出：
- run_root/paper_to_userspec/paper_claims_extracted.json
- run_root/paper_to_userspec/paper_evidence_cards.jsonl
- run_root/paper_to_userspec/report1_screening_report.md
- run_root/paper_to_userspec/report1_screening_report.json
- run_root/paper_to_userspec/paper_confidence_report.json
- run_root/paper_to_userspec/evidence_trace.jsonl

PHASE 3：MODEL_INPUT_VALIDATION_AND_COMPLETION
MODEL_INPUT 是强制输入。必须先验证，再进入 onboarding 或 evaluation。

必须检查：
- model_id
- model_name
- task_type
- deployment_type
- repo
- weights
- environment_hint
- phase1_runtime_target
- entrypoints
- fixture
- io_contract
- protocol
- expected_metric_family

如果模型不存在，按照MODEL_INPUT 在 ~/src/sure_eval/models 部署；

如果模型已存在，必须用 MODEL_INPUT 对照读取：
- README.md
- config.yaml
- model.spec.yaml
- server.py
- model.py
- setup.sh
- pyproject.toml
- artifacts/verdict.json

输出：
- run_root/model_input/MODEL_INPUT.resolved.yaml
- run_root/model_input/model_input_validation.json
- run_root/model_input/model_input_gap_report.md
- run_root/model_input/model_input_conflict_report.md，如适用

PHASE 4：TOOL_ONBOARDING_OR_READINESS_ROUTING

如果 integration_state 是 not_onboarded 或 tool_workflow_ready 是 false：
执行 Tool Onboarding：
DISCOVER → CLASSIFY → PLAN → VALIDATE_SPEC → BUILD_ENV → FETCH_WEIGHTS → VALIDATE_IMPORT → VALIDATE_LOAD → VALIDATE_INFER → VALIDATE_CONTRACT → GENERATE_WRAPPER → SAVE_ARTIFACTS

第一阶段只验证最小可调用路径，不做完整 benchmark。

如果 integration_state 是 onboarded：
必须进入 TOOL_READINESS_AND_ROUTING_UNIT：
- 检查 model_dir、config.yaml / model.spec.yaml、server.py、model.py、checkpoint、artifacts。
- 做单条 fixture smoke。
- 判断 server_ready / server_declared_but_unverified / tool_broken_needs_repair / not_tool_ready。

输出：
- run_root/model_readiness/tool_readiness_routing.json
- run_root/model_readiness/server_smoke_report.json
- run_root/model_readiness/handoff_report.md

PHASE 5：DATASET_DISCOVERY_AND_PREPARATION
- 从 paper claims 和 TASK_PROFILE 读取目标 dataset / split。
- 优先复用 SURE Benchmark 或本地 OREF sample.jsonl。
- 不重复下载已有大数据。
- split 不一致必须标记 scope_mismatch。
- 私有数据或不可获得数据必须 blocked / partial_go。

输出：
- run_root/dataset/dataset_decision.json
- run_root/dataset/dataset_manifest.json
- run_root/dataset/data_source_audit.md
- run_root/dataset/sample_preview.jsonl

PHASE 6：MAIN_FLOW_EVALUATION_ORCHESTRATION

必须严格按照 Main Flow 执行：

INTAKE
→ TASK_CLASSIFICATION_UNIT
→ TOOL_READINESS_AND_ROUTING_UNIT
→ PLAN_UNIT
→ DATASET_SCOPE_UNIT
→ SCRIPT_ROUTING_UNIT
→ EXECUTION_SURFACE_UNIT
→ EXECUTION_READINESS_UNIT
→ SMOKE_TEST_UNIT
→ EXECUTE / WAIT
→ ASSESSMENT_UNIT
→ RUN_REPORT_UNIT

硬规则：
- 不允许跳过 TOOL_READINESS_AND_ROUTING_UNIT。
- tool 不 ready 时 handoff 给 model tool-agent。
- 能交给 deterministic scripts 的工作必须交给 scripts。
- shell entrypoint 必须先 materialize，再做 readiness。
- 正式执行前必须先通过 bounded smoke。
- 所有 skipped dataset、handoff、blocked、stop 都必须说明理由。

文本输出类任务：
- ASR、S2TT、SER、SD、SA-ASR 优先使用 src/sure_eval/evaluation 中已有 evaluator。
- 不允许临时自定义 metric。
- metric 无法映射时标记 metric_not_supported。

生成音频类任务：
- TTS、VC、Speech Enhancement 必须先生成 audio manifest，再生成 metric manifest，再调用 evaluator 或已登记 external metric runner。
- 不允许直接套用 key<TAB>prediction 文本评估。
- partial 不允许写 full success。

输出：
- run_root/main_flow/task_classification.json
- run_root/main_flow/main_agent_plan.json
- run_root/main_flow/script_routing.json
- run_root/main_flow/execution_surface.json
- run_root/main_flow/run_evaluation.sh
- run_root/main_flow/execution_readiness_report.json
- run_root/main_flow/smoke_test_report.json
- run_root/main_flow/assessment_report.json
- run_root/main_flow/main_agent_run_report.json
- run_root/main_flow/model_eval_manifest.json

PHASE 6A：GENERATED_AUDIO_BRANCH

触发条件：
TASK_PROFILE.expected_output.primary_output == "audio_path"

执行：
1. precheck
   - dataset manifest 存在
   - inference runner 存在或可在 run_root 下生成
   - metric runner / evaluator 存在
   - cache 可复用

2. quick validation
   - 默认 3 条
   - 通过后才 full

3. full inference
   - 输出 run_root/inference/full_predictions_manifest.jsonl
   - 输出音频到 run_root/inference/prediction_audio_full/
   - 必须支持 resume 和 logs

4. validate audio manifest
   - prediction_audio 存在、非空、可读
   - reference_text 是 target text
   - reference_audio 仅在需要 speaker similarity 时强制要求

5. metric manifest
   - 输出 run_root/evaluation/generated_audio_metrics_manifest.jsonl

6. metric runner
   - 每个 sample 独立 work dir
   - 每个 sample 独立 stdout / stderr / command
   - 支持 resume

7. summary
   - run_root/evaluation/generated_audio_metric_summary.json
   - status 必须是 success / partial / failed

## TTS Metrics 调用方式

```text
TTS metrics 不在当前 sure-userspec-clean repo 中实现。


所有 TTS metric 计算必须调用external metric framework：

/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/scripts/run_tts_metric_pipeline_docker.sh

该 wrapper 内部会进入 Docker 并调用实际 metric pipeline：

/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/scripts/run_tts_metric_pipeline.py

本 repo 只负责：
1. 生成 TTS prediction audio
2. 生成 TTS metrics manifest
3. 逐条调用junhao.du wrapper
4. 聚合 wrapper 输出的 merged.json
5. 生成 summary 和 paper comparison

禁止：
1. 不在当前 repo 重写 tts_wer / sim / mos 相关算法
2. 不复制脚本到当前 repo
3. 不修 unrelated backend
4. 不伪造 metric 结果
5. partial 不写成 full success

每条样本的标准调用方式：

/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/scripts/run_tts_metric_pipeline_docker.sh \
  --prediction-audio /hpc_stor03/.../generated.wav \
  --reference-text "target text" \
  --reference-audio /hpc_stor03/.../prompt_or_reference.wav \
  --language en \
  --cache-dir /hpc_stor03/sjtu_home/bowen.wang/.cache/sure-eval/tts-metrics \
  --work-dir /hpc_stor03/.../item_work_dir \
  --output /hpc_stor03/.../item_work_dir/merged.json \
  --speaker-backends wavlm-large,eres2net \
  --mos-backends "" \
  --gpu 0 \
  --device cuda:0 \
  --no-pull

TTS metrics manifest 每行必须包含：

{
  "key": "...",
  "prediction_audio": "/absolute/path/to/generated.wav",
  "reference_text": "target text",
  "reference_audio": "/absolute/path/to/prompt_or_reference.wav",
  "language": "en"
}

字段要求：
- prediction_audio 必须存在、非空、可读
- reference_text 必须是 target text，不是 prompt text
- reference_audio 必须存在、非空、可读
- 所有路径尽量使用 absolute path，避免 Docker 路径解析失败
- key 必须唯一

推荐生成一个 reproduction-only runner：

run_root/evaluation/run_tts_metrics_full.sh

runner 要求：
1. 读取 run_root/evaluation/tts_metrics_full_manifest.jsonl
2. 每个 sample 建一个独立 work dir：
   run_root/evaluation/full_tts_metrics/items/<key>/
3. 每个 sample 保存：
   - command.sh
   - stdout.log
   - stderr.log
   - merged.json
4. 支持 resume：
   如果 merged.json 已存在且 ok:true，则跳过该 sample
5. 最终聚合：
   - run_root/evaluation/full_tts_metrics/merged_results.jsonl
   - run_root/evaluation/tts_metric_full_summary.json

summary 至少包含：

{
  "status": "success|partial|failed",
  "num_total": null,
  "num_success": null,
  "num_failed": null,
  "failed_keys": [],
  "mean_metrics": {
    "tts_wer": null,
    "sim": null,
    "sim/wavlm-large": null,
    "sim/eres2net": null
  },
  "metric_runner": {
    "script_path": "/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/scripts/run_tts_metric_pipeline_docker.sh",
    "cache_dir": "/hpc_stor03/sjtu_home/bowen.wang/.cache/sure-eval/tts-metrics",
    "speaker_backends": ["wavlm-large", "eres2net"],
    "mos_backends": [],
    "gpu": 0,
    "device": "cuda:0",
    "no_pull": true
  },
  "notes": []
}

如果 wrapper 不存在、Docker 不可用、cache 不可用、GPU 不可用或某条样本失败：
- 不要补假数
- 写 metric_runner_unavailable / metric_runner_failed / partial
- 保留已经完成的样本结果
- 写清楚失败 key 和日志路径
```


PHASE 7：PAPER_VALUE_COMPARISON
对齐：
- model / checkpoint
- dataset / split
- metric / direction / unit
- protocol
- postprocessing
- sample count
- full / subset / smoke / partial scope

输出：
- run_root/comparison/paper_value_comparison.json
- run_root/comparison/paper_value_comparison.md
- run_root/comparison/discrepancy_analysis.md

TTS comparison 特别规则：
- tts_wer 可作为 local WER。
- SIM 必须说明 speaker backends 和聚合方式。
- SIM 不得默认等同 paper SIM-o。
- DNSMOS / UTMOS / WV-MOS 是 MOS proxy，不得默认等同 human MOS。
- RTF 只有存在 inference_time_sec 和 duration_sec 时才计算，否则写 not_evaluated。
- partial metrics 只能标记 partial。

PHASE 8: REPRODUCTION_SCORING
本阶段负责读取 Phase 7 和前面阶段已经生成的 artifacts，调用仓库中已有的 reproduction scoring 实现，生成最终复现评分文件。评分规则已经在代码中实现，不要在 prompt 中重新实现或手写评分逻辑。

输入文件只使用：
  1. <run_root>/paper_value_comparison.json
  2. <run_root>/*/paper_confidence_report.json

  不要修改已有 metric 算法本体。
  不要修改 paper_to_userspec/confidence.py。
  不要修改历史指标结果文件。
  不要重新跑 inference 或 metrics。

评分实现：
必须使用仓库已有 scoring 实现：src/sure_eval/reproduction/scoring.py
优先使用 CLI：scripts/compute_reproduction_score.py

不要重新实现 scoring 公式。
不要复制 scoring.py 中的逻辑到新脚本。
不要修改评分规则。

  输出写到：
  <run_root>/final_score_trial_<timestamp>/

  生成文件：
  1. reproduction_score_report.json
  2. reproduction_score_summary.md

  执行命令模板：

  RUN_ROOT="<run_root>"
  TRIAL_DIR="$RUN_ROOT/final_score_trial_$(date +%Y%m%d_%H%M%S)"
  mkdir -p "$TRIAL_DIR"

  uv run python scripts/compute_reproduction_score.py \
    --run-root "$RUN_ROOT" \
    --output-json "$TRIAL_DIR/reproduction_score_report.json" \
    --output-md "$TRIAL_DIR/reproduction_score_summary.md" \
    --exclude-metrics-json '["5-Dup"]'

PHASE 9：FINAL_REPORT
输出：
- run_root/final/reproduction_report.md
- run_root/final/reproduction_report.json
- run_root/final/artifact_index.json

最终汇报：
- SUCCESS / PARTIAL / BLOCKED / FAILED
- paper target
- model readiness
- dataset readiness
- local result
- paper gap
- artifact paths
- next action

============================================================
E. 强制输入模板
============================================================

下面三个输入块必须同时提供：
1. RUN_INPUT
2. MODEL_INPUT
3. TASK_PROFILE

如果 MODEL_INPUT 缺失，必须停止并要求补充，不能自行配置模型。


## RUN_INPUT

```yaml
RUN_INPUT:
  project_root: "/path/to/sure-eval"
  paper_pdf_path: "/path/to/paper.pdf"
  run_root: "runs/reproduction/<model>_<dataset>_<timestamp>"
  user_goal: "paper_to_userspec_then_reproduce"

  target:
    model_name: "<model_name_or_unknown>"
    model_dir: "src/sure_eval/models/<model_name_or_unknown>"
    integration_state: "<unknown|not_onboarded|onboarded|broken>"
    tool_workflow_ready: "<true|false|unknown>"

  constraints:
    allow_tool_workflow: true
    allow_download: true
    allow_large_download: false
    allow_code_patch: true
    allowed_tasks: ["<ASR|TTS|VC|SD|SA-ASR|S2TT|SER|SE>"]
    allowed_datasets: null
    blocked_datasets: []
    protocol: "strict_core"
    dry_run: false
    max_smoke_samples: 3
    max_full_samples: null
    prefer_existing_local_data: true
    do_not_modify_unrelated_files: true
    allow_partial_metrics: true
    reuse_existing_cache: true
    allow_metric_model_redownload: false

  runtime_context:
    available_scripts:
      - scripts/prepare_sure_dataset.py
      - scripts/materialize_predictions_template.py
      - scripts/generate_predictions_via_server.py
      - scripts/validate_prediction_files.py
      - scripts/evaluate_predictions.py
      - scripts/refresh_report_snapshot.py

    external_metric_runners:
      tts:
        name: "sure_tts_metric_docker_wrapper"
        default_script_path: "/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/scripts/run_tts_metric_pipeline_docker.sh"
        env_var: "SURE_TTS_METRIC_SCRIPT"
        default_cache_dir: "/hpc_stor03/sjtu_home/bowen.wang/.cache/sure-eval/tts-metrics"
        cache_env_var: "SURE_TTS_METRIC_CACHE"
        default_speaker_backends: ["wavlm-large", "eres2net"]
        default_mos_backends: ["dnsmos", "wv-mos", "utmos"]
        default_extra_args:
          gpu: 0
          device: "cuda:0"
          no_pull: true
        copy_policy: "do_not_copy"
        algorithm_policy: "do_not_rewrite"
        fallback_if_missing: "metric_runner_unavailable"
```

## MODEL_INPUT

```yaml
MODEL_INPUT:
  model_id: "owner/model-name"
  model_name: "ModelName"
  task_type: "asr|tts|vc|sd|sa_asr|s2tt|ser|speech_enhancement|vad|sv|other"
  deployment_type: "local|api"

  paper:
    title: "<paper_title_or_unknown>"
    pdf_path: "<paper_pdf_path>"
    evidence_dir: "<run_root>/paper_to_userspec"
    target_claim_ids: []

  repo:
    url: "https://github.com/owner/repo"
    commit: null
    local_path: null

  weights:
    source: "huggingface|modelscope|official_release|paper_repo|local|api|unknown"
    model_id: "<modelscope_or_hf_id_or_null>"
    local_path: null
    required: true
    cache_policy: "model_local_first"
    local_dir_name: "checkpoints"

  environment_hint:
    preferred_backend: "uv|pixi|conda|docker|api|unknown"
    python_version: "3.10|3.11|3.12|unknown"
    requires_gpu: true
    gpu_hint: "<A100|RTX4090|RTX2080Ti|unknown>"
    system_packages: ["ffmpeg", "libsndfile1"]

  phase1_runtime_target:
    description: "Validate the minimal callable path only."
    required_checks:
      - "confirm package is importable"
      - "load model or API client with minimal config"
      - "run one task-specific fixture"
      - "return output satisfying io_contract"
    out_of_scope:
      - "full benchmark accuracy"
      - "leaderboard comparison"
      - "large scale tuning"

  entrypoints:
    import_test: "import package"
    load_test: "model = package.load_model(...)"
    infer_test: "model.run_tool(...)"
    cli_test: null

  fixture:
    audio: "<task_specific_fixture_audio_or_null>"
    text: null
    reference_audio: null
    rttm: null
    task_specific: true
    fallback_allowed: true

  io_contract:
    input_type: "audio_path|audio_pair|text|json|rttm|mixed"
    output_type: "json|text|audio_path|rttm"
    primary_field: "text|audio_path|segments|label|score|enhanced_audio_path"
    required_fields: []
    nonempty_fields: []
    json_serializable: true

  protocol:
    default_protocol: "strict_core"
    required_mappings:
      precision: "required|optional|not_applicable"
      max_batch_size: "required|optional|not_applicable"
      search_strength: "required|optional|not_applicable"
      context_policy: "required|optional|not_applicable"
      external_info: "required|optional|not_applicable"

  expected_metric_family:
    primary_metric: "<from_TASK_PROFILE>"
    secondary_metrics: []
    metric_direction: "higher_is_better|lower_is_better|metric_specific"
    scorer_source: "sure_eval|external_registered_runner|official|paper_code|unknown"
```

## TASK_PROFILE

```yaml
TASK_PROFILE:
  task_type: "<ASR|TTS|VC|SD|SA-ASR|S2TT|SER|SE>"

  expected_input:
    primary_input: "<audio_path|text|audio_pair|rttm|json>"
    optional_inputs: []

  expected_output:
    primary_output: "<text|audio_path|segments|label|score|rttm>"
    required_fields: []
    prediction_file_format: "<text_prediction_txt|generated_audio_manifest_jsonl|rttm|jsonl>"

  dataset_eligibility:
    allowed_dataset_tasks: []
    required_fields_in_jsonl: []
    split_policy: "must_match_paper_split"

  metrics:
    primary_metric: "<metric_name_or_paper_specific>"
    secondary_metrics: []
    direction: "<lower_is_better|higher_is_better|metric_specific>"
    unit: "<%|score|metric_specific>"
    scorer_source: "<sure_eval|external_registered_runner|official|unknown>"
    sure_eval_supported: "<true|false|unknown>"
    metric_notes: ""

  generated_audio_contract:
    enabled: false
    full_prediction_manifest: "run_root/inference/full_predictions_manifest.jsonl"
    metric_manifest: "run_root/evaluation/generated_audio_metrics_manifest.jsonl"
    required_prediction_fields: []
    required_metric_fields: []

  comparison_policy:
    compare_only_if_dataset_split_matches: true
    compare_only_if_metric_matches: true
    compare_only_if_protocol_explained: true
    allow_smoke_comparison: false
```
