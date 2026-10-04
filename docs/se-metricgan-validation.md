# MetricGAN+ SE 适配验证（2026-09-25）

选用 `speechbrain/metricgan-plus-voicebank`，固定 revision
`a196ce26b3bdace6fa1d819017584bdbcce462a8`，CPU 推理，16 kHz 单声道 PCM16 输出。
模型说明：[SpeechBrain 模型卡](https://huggingface.co/speechbrain/metricgan-plus-voicebank)。
首轮样本来自仓库 `fixtures/tasks/se/librispeech_noise_smoke`，共两条，均有独立 clean reference。
补测使用另外三条 LibriSpeech 语音，加确定性 Gaussian 噪声，覆盖 0/5/10 dB、
16 kHz 单声道、8 kHz 单声道、16 kHz 双声道；共五条不同语音。

改动覆盖 Trans 的 Python/schema/状态机、SE 数据投影、Infer 工具参数、
Feed 入口识别、Onboard/Trans 验证模板及 Evaluation Runtime。
验证模板还会清理旧输出、拒绝符号链接，并逐条复核 Onboard 输出，避免重跑误判成功。

## 每个 skill 的实际状态

| Skill | 已执行验证 | 结果与边界 |
| --- | --- | --- |
| `sure_feed` | 在线读取真实 HF metadata/model card；自动分类；生成 handoff；Onboard 解析 handoff | 修复后通过。修复前漏识别 `from_hparams`，把 `python train.py` 当推理命令，停在 `blocked_needs_research`；`audio-to-audio` 还会误分为 VC。 |
| `sure_onboard` | handoff 输入解析；真实 wrapper 的 import/load/infer/contract，五条样本 | 通过开发验证。补充 SE 噪声输入选择、独立输出路径、文件存在性检查。2026-10-04 补测封存 Runtime、四个真实验证门和完整 deployment bundle，均通过。 |
| `sure_trans` | 真实验证模板 import/load/infer/contract，逐条调用；SE fixture staging/hash 检查；MCP smoke；状态机回归 | 通过上述检查。补上 TypeScript 首关的 `se` 白名单；Python 层支持并不足以让 slash command 通过。未执行完整源环境转换、等价性与封装生命周期。 |
| `sure_approve` | 对开发目录执行 producer audit；对封存候选执行完整只读审计与运行时复验 | 开发目录被正确拒绝；封存候选生成了 findings 为空的 `awaiting_approval` review packet。尚无人类正向决定或发布。 |
| `sure_infer` | 用实际 Infer 参数构造与预测投影函数调用真实 MCP；五条音频解码；SE source projection 回归 | 开发调用通过。没有完整 approved-model/runtime 解析证据，不能记为 `/sure_infer` 正式成功。 |
| `sure_eval` | 锁定 Evaluation Runtime、固定引擎上的真实 SI-SDR/STOI；增强与 noisy baseline 各五条 | 后端评分通过。补齐 `pystoi`、SciPy、SoundFile 的 hash lock 和启动 import probe；没有经过正式 prediction-source 身份门控与批次追加完整流程。 |

`sure_init` / `sure_resume` 是内置命令，不属于上述六个 skill。本地 doctor 显示没有
Pi provider auth/models 配置；本次没有调用付费 LLM 或把脚本级成功标成 agent slash-command 成功。

## 质量结果

固定引擎：`db54d63350530d72d3acb83b560b8789ec896c2c`。
两个分数均为两条样本的平均值，越高越好。

| 输入 | SI-SDR（dB） | STOI |
| --- | ---: | ---: |
| 原始 noisy audio | 18.563686 | 0.916510 |
| MetricGAN+ enhanced audio | 4.269465 | 0.827089 |

这两条 smoke 上增强效果下降。它们是 LibriSpeech 派生的 speech-on-speech 混合，
不是模型的 VoiceBank/DEMAND 标准评测；不能用两条样本判断模型总体质量，
也不能用“接口能运行”代替质量结论。

三条新增语音分别为 `367-130732-0006`、`4198-12281-0009`、`3005-163389-0016`。
来源及 CC-BY-4.0 署名沿用 `fixtures/tasks/asr/qwen3_asr_smoke/asr_en/provenance.json`；
生成脚本记录噪声种子、重采样、声道变换、共用峰值缩放及输入/参考哈希。
下面是新增三条的平均值，不与首轮两条混算：

| 输入 | SI-SDR（dB） | STOI |
| --- | ---: | ---: |
| 新增 noisy audio | 4.683257 | 0.734851 |
| MetricGAN+ enhanced audio | -3.280982 | 0.653770 |

新增样本也没有体现平均质量提升。所有推理输出均成功解码为非空 16 kHz 单声道 PCM16 WAV；
8 kHz 和双声道输入的转换通过。本 PR 提供可测试的适配，不把该模型标为质量达标。

## 复现与证据

可复用脚本和命令见
[`metricgan_plus_voicebank/README.md`](../sure/skills/sure_onboard/examples/metricgan_plus_voicebank/README.md)。
`smoke.py` 用模型 Python 做推理，用独立锁定的 Evaluation Runtime 评分；不改 approved 根目录。

本地忽略目录下保留本轮证据：

- `.sure/se-verification/feed-ca/artifacts/feed_report.json`：修复前真实 Feed 失败原因。
- `.sure/se-verification/feed-fixed/artifacts/feed_report.json`：修复后自动分类与 handoff。
- `.sure/se-verification/handoffs/speechbrain__metricgan-plus-voicebank/model_input.yaml`：可继续使用的输入。
- `.sure/se-verification/onboard-input/artifacts/`：真实输入解析产物。
- `.sure/se-verification/mcp_smoke-final.json`：initialize/list/call/shutdown 通过，stdout 无非 JSON 杂讯。
- `.sure/se-verification/real-smoke-v6/summary.json`：首轮两条的最终代码复测结果与未验证范围。
- `.sure/se-verification/extra-fixtures/provenance.json`：新增三条的来源及变换记录。
- `.sure/se-verification/extra-smoke-v2/summary.json`：新增三条的完整开发 smoke 结果。
- 同目录 `commands.json`、`infer-contract.json`、`enhanced/`、`noisy_baseline/`、`approve/artifacts/`：命令、音频、分数及拒绝证据。

2026-10-04 复测使用 Python 3.10、PyTorch 2.0.1、Torchaudio 2.0.2 和 SpeechBrain
1.0.3。在线 Feed 生成真实 handoff；Onboard 用哈希锁封存 Model Runtime，模型通过
import/load/infer/contract、package、inventory、verdict 和 finalized bundle 门。Approve
随后通过 producer/integrity 审计以及隔离候选的真实运行时复验，生成无 findings 的
`awaiting_approval` review packet。SE 只有干净参考音频、没有文本标注的 fixture 也通过
Onboard 的准备与检查门。`sample_output.json`、权重清单和所有生成音频已转换为可移植路径并
纳入最终包哈希。

此复测尚未得到人类 `decision=approve`，因此没有发布到 approved root，也不能把
开发级评分当作正式的 `/sure_infer` → `/sure_eval` 完成记录。正向决定之后还必须在
发布的候选上验证 Infer/Eval 的完整身份门和批次追加流程。

## 回归检查

- Feed：37 个 Python 测试。
- Onboard：8 个 Python 测试。
- Trans：90 个 Python 测试，25 个 TypeScript 契约测试。
- Infer/数据投影：24 个 Python 测试；Evaluation Runtime：28 个 Python 测试。
- Approve：18 个 Python 测试。
- `npm run check`：使用锁定 Harness Python 和同版本 Biome musl binary 后通过全部 12 项。
- `./test.sh`：3,493 passed，694 skipped，0 failed。跳过项目包含无 API key/本地 LLM 的测试，不能算作真实 provider 调用验证。
- `sure:doctor`：0 failed；3 个已有环境提示为缺 provider auth、models 配置和默认数据目录。

环境排查：本机可信 CA bundle 位于 `/etc/pki/tls/certs/ca-bundle.crt`，通过
`SSL_CERT_FILE` 配置后 Feed 联网成功，未关闭 TLS 验证。系统 Python 3.6 不满足仓库
检查要求；默认 Biome glibc binary 也无法在本机运行，改用同版本 2.3.5 musl binary
仅用于本地检查，没有修改 npm 依赖锁。Evaluation Runtime 的一个测试使用固定
`/tmp/outside-python`，本机已有不可写文件；指定独立 `TMPDIR` 后通过。
Trans 的 standalone 检查需把临时目录放在仓库外；放到仓库内会沿祖先目录找到共享 site resolver。

全量测试最初触发既有 `auth-storage.test.ts` 并发重载用例失败。在未改动基线
`2ff0fc4` 的独立 worktree 上同样复现：`old` → `new` 的同长度写入可能共享文件时间戳，
没有触发该用例所需的重载。测试改为不同长度的 `new-value`，保留取消、合并读及锁释放
全部断言，未改鉴权实现；修正后在默认临时目录运行全量测试通过。
