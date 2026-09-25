# SepFormer SE 交叉验证（2026-09-25）

第二个模型选用
[SpeechBrain SepFormer WHAM16k enhancement](https://huggingface.co/speechbrain/sepformer-wham16k-enhancement)，
固定 revision `90b3c5c3ffe3e04387b566715ab5fff36ec7b9d9`。
它使用 `SepformerSeparation.separate_batch`，输出一个增强语音源，与 MetricGAN+ 的
`SpectralMaskEnhancement.enhance_batch` 不同。采用相同 CPU 开发环境、样本和锁定评分引擎。

## Skill 修改范围

修改的是 skill 包的代码、测试和说明，不是逐个重写 `SKILL.md`：

| Skill | SE 适配改动 | 本轮验证 |
| --- | --- | --- |
| `sure_feed` | 自动 SE 分类、SpeechBrain 入口识别 | 真实在线发现、三个语义门控、handoff；本轮补识别 `separate_file` / `separate_batch` |
| `sure_onboard` | SE playbook、验证模板、模型示例 | SepFormer handoff 解析与真实 import/load/infer/contract |
| `sure_trans` | 状态机、schema、fixture/reference 哈希、adapter 和验证模板 | 逐条运行真实验证模板；MCP initialize/list/call/shutdown |
| `sure_infer` | SE MCP 参数、noisy/clean 数据投影 | 实际参数构造、MCP 推理和音频预测投影 |
| `sure_eval` | 使用说明、锁定 Evaluation Runtime 的 STOI/解码依赖 | 相同干净参考下比较增强音频与 noisy baseline |
| `sure_agent_eval` | 单阶段 SE 校验、音频输出路径和结构化预测 | 实际 MCP runner、数据投影和预测文件 |
| `sure_approve` | **未修改审批代码** | 审计开发目录并正确拒绝不完整包；未验证正向审批发布 |

直接修改的 `SKILL.md` 是 `sure_trans`、`sure_eval`、`sure_agent_eval` 三个。
Feed、Onboard、Infer 的适配位于脚本、模板或引用手册。

## 换模型发现的遗漏

修复前在线 Feed 返回 `blocked_needs_research`：任务正确识别为 SE，load 入口也存在，
但因没有识别模型卡的 `separate_file`，缺少 `entrypoints.infer_test` 和
`phase1_runtime_target`。补充两个 separation API 后，返回 `ready_for_onboard`，
没有 missing/weak fields，三个语义门控及 Onboard handoff 解析均通过。

新增独立 SepFormer wrapper，并让原开发 smoke 支持 `--example-dir`，从模型配置读取
身份信息，避免验证记录仍写着 MetricGAN+。干净参考只用于评分；模型输出为
16 kHz 单声道 PCM16 WAV。保留单源检查，不把多说话人分离输出直接当增强语音。

## 同样本质量比较

固定引擎 `db54d63350530d72d3acb83b560b8789ec896c2c`；两个指标均越高越好。
原始两条为 LibriSpeech speech-on-speech 混合；额外三条的种子、音频与变换完全沿用
[MetricGAN+ 验证报告](se-metricgan-validation.md)，不是重新抽样。

| 原始 2 条的均值 | SI-SDR（dB） | STOI |
| --- | ---: | ---: |
| Noisy baseline | 18.563686 | 0.916510 |
| MetricGAN+ | 4.269465 | 0.827089 |
| SepFormer | 19.322936 | 0.937676 |

| 额外 3 条的均值 | SI-SDR（dB） | STOI |
| --- | ---: | ---: |
| Noisy baseline | 4.683257 | 0.734851 |
| MetricGAN+ | -3.280982 | 0.653770 |
| SepFormer | 8.021871 | 0.742345 |

五条数据的 Onboard、逐条 Trans、Infer MCP 和 Agent runner 均通过；评分产物对两项指标
分别保留 2/3 条样本，没有丢样本。所有输出成功解码为非空 16 kHz 单声道 PCM16 WAV。
SepFormer 在两组数据上均有平均质量提升，不代表每一条语音或其他数据集都会改善。

本轮还通过 38 条 Feed 回归测试、12 项 `npm run check`，以及真实 Feed 的
match-task/model-input/rank-select 语义门控。共享 runner 的默认 MetricGAN+ 路径
复测通过，原两条分数不变。完整仓库测试沿用前一提交的记录，本轮没有重复运行无关套件。

## 复现与证据

命令见 [SepFormer 示例](../sure/skills/sure_onboard/examples/sepformer_wham16k/README.md)。
本地忽略目录 `.sure/se-verification/` 保留：

- `sepformer-feed-before/`、`sepformer-feed-fixed/`：修复前后真实在线发现。
- `sepformer-handoffs/`、`sepformer-onboard-input/`：handoff 与输入解析。
- `sepformer-original/`、`sepformer-extra/`：命令、各阶段日志、推理音频、参考集、评分及审批拒绝证据。
- `sepformer-mcp-smoke.json`：完整 MCP smoke，stdout 无非 JSON 杂讯。
- `sepformer-weights.sha256`：本地权重和配置哈希；权重没有提交到仓库。
- `metricgan-runner-regression/`：共享 runner 默认模型的回归验证。

这是两个模型的开发后端交叉验证。模型环境仍是开发 Python，未封存；没有完成
完整源环境转换、等价性、候选封装、正向审批发布、approved-model 解析和 slash-command
terminal gates。小样本质量结果不能替代 WHAM! / VoiceBank 标准评测。
