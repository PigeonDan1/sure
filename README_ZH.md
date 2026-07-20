# SURE Harness

[English README](./README.md)

SURE Harness 把音频模型评估变成 agent 可读、artifact 可审计、执行过程可约束的工作流。它面向 Pi/Codex 这类 TUI agent：人类用 slash command 表达意图，agent 负责规划和执行，harness 负责边界、证据和复现性。

产品目标很直接：模型从发现、接入、烟测、正式评估到报告落盘，都要能被人类检查，也要能被 agent 稳定执行。

## 产品工作流

| Command | 产品任务 | 主要产物 |
| --- | --- | --- |
| `/sure_feed` | 从 ModelScope、HuggingFace、GitHub 或显式输入中发现模型，判断任务类型，生成接入输入。 | `model_input.yaml`, `feed_report.json` |
| `/sure_onboard` | 把一个模型仓库变成可运行的本地推理单元，包含 wrapper、环境计划、fixture、package gate 和 verdict。 | `verdict.json`, wrapper 文件, model spec |
| `/sure_eval` | 对已经 onboard 的音频模型执行 SURE-EVAL 评估，生成 route plan、执行面、VC/local 执行证据和指标报告。 | `main_agent_run_report.json`, route plan, metric reports |

`/sure_init` 用于项目初始化：选择 agent/provider、配置 auth 位置、发现 skill、检查本地 Python/backend。

## 原子能力

SURE 的核心不是一段大脚本，而是一组可组合、可审计的原子能力：

- **任务路由**：根据模型和数据集元信息识别 ASR、TTS、VC、KWS、S2TT、说话人相关任务、语音理解等任务族。
- **MODEL_INPUT 生成**：把发现阶段的证据转成 `/sure_onboard` 可消费的 YAML。
- **Fixture 准备**：为不同任务准备小规模 smoke fixture，但不把 smoke 数据冒充为 benchmark 证据。
- **Runtime 规划**：选择 local、Docker 或 VC 执行面，并把决策写入 artifact。
- **执行门禁**：执行 bounded smoke，阻止不合规 fallback，要求终态 artifact 齐全后才能 finish。
- **评估路由**：主动读取外部 `sure-evaluation` engine，发现支持的 metric、选择 route nodes，并检查 node-local 环境。
- **VC 提交**：生成可提交 entrypoint，执行真实 `vc submit`，记录资源、镜像、日志和队列修复。
- **Artifact manifest**：持久化 `run.json`、`events.jsonl`、最终 manifest、metric payload、sample report 和失败诊断。

这些能力保持原子化：脚本负责确定性执行，hooks 负责门禁和状态机，agent 在约束内做范围和路线决策。

## 快速开始

在 repo 根目录：

```bash
npm install --ignore-scripts
./pi-test.sh
```

在 TUI 里：

```text
/sure_init
/sure_feed source=modelscope query="english asr" max_models=20
/sure_onboard model_input=sure/handoffs/<model>/model_input.yaml
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=vc
```

本地开发可以显式使用 local：

```text
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=local
```

如果用户请求 `execution=vc`，harness 必须产生真实 VC 提交证据，不允许静默 fallback 到 local。

## 评估引擎

SURE Harness 从独立的 `sure-evaluation` engine 读取 metric 能力和 route nodes。这个 checkout 应保持本地化，不进入本仓库 Git 历史：

```bash
mkdir -p sure/external
git clone https://github.com/PigeonDan1/sure-evaluation.git sure/external/sure-evaluation
```

也可以通过环境变量指定：

```bash
export SURE_EVALUATION_HOME=/path/to/sure-evaluation
```

当前重要评估链路包括：

- ASR 中文 CER：`normalization/wetext_norm -> scoring/wenet_cer`
- TTS/VC 中文 CER：`frontend/funasr_loader_16k_mono -> transcription/paraformer_zh -> normalization/punctuation_strip_norm -> scoring/wenet_cer`
- TTS 英文 WER：`transcription/whisper_large_v3 -> normalization/whisper_norm -> scoring/wenet_wer`

## 仓库卫生

仓库应该包含 harness 代码、skill 包、schemas、prompts、小型 fixtures 和测试。不要提交：

- API key、provider token、auth 文件。
- 模型权重、checkpoint、大数据集、生成的 prediction、metric result dump。
- `.sure/` run 目录。
- 本地 external engine checkout。
- 模型本地虚拟环境或 cache。

运行产物应该落到 ignore 路径，例如 `.sure/`、`sure/models/`、`sure/handoffs/*/artifacts/`、`sure/skills/sure_eval/results/`。

## Skill 包结构

仓库内置 skills 位于：

```text
sure/skills/<skill-name>/
```

典型结构：

```text
sure/skills/<skill-name>/
  sure.skill.json
  SKILL.md
  hooks/
  scripts/
  schemas/
  references/
  examples/
```

`SKILL.md` 是 agent 操作手册。Hooks 负责状态机和门禁。Scripts 负责确定性执行。Schemas 定义 artifact 合同。

## 开发检查

迭代时优先跑小范围检查：

```bash
npm run check:sure-hooks
python3 -m py_compile sure/skills/sure_eval/scripts/*.py
```

更完整检查：

```bash
npm run check
```

常用 SURE 定向测试：

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
```

## 设计边界

Harness 负责 slash command 发现、run 生命周期、状态持久化、hook 执行、工具门禁和最终 manifest 校验。

Skill 包负责领域 prompt、确定性脚本、状态机、schemas、checkpoints、校验规则和修复说明。

不要把任务专属 metric、数据集假设或 SURE 业务逻辑塞进通用 harness，除非这个规则真的对所有 skill 都成立。
