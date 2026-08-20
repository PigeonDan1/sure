# 评估引擎

这份文档给要动评估引擎的人:submodule 关系、引擎位置解析、route 选择、跑完从哪些产物看真实链路。

安装和发行形态见[根 README](../README.md),站点路径契约见[站点配置指南](site-configuration.md),这里不重复。

## Harness 与引擎的分工

Metric 能力表、route nodes、精确 `pipeline_id` 选择、node-local 环境来自 `sure-evaluation` submodule。另外 `sure/skills/sure_eval/scripts/sure_eval/` 下还有一份随 skill 打包的确定性评测后端(`sure-eval-backend`):CER 怎么算、normalizer 怎么写都在这份里,`evaluate_predictions.py` 直接 import 它。

所以别再往父仓库**新增**引擎源文件,也别就地改它们。这份 bundled backend 已经在库里了,要动它,submodule 基线得跟着一起同步。

不管引擎是从 submodule 还是从别的 checkout 来的,每次 evaluation run 都要把这次用的 engine path 和 commit 写进 `evaluation_route_plan.json`。这是回头查「当时到底跑的哪版引擎」的唯一凭据。

## submodule 关系

Submodule 路径固定:

```text
sure/external/sure-evaluation
```

它在 `.gitmodules` 里登记的是**相对地址** `../sure-evaluation.git`(branch `main`),指向哪取决于父仓库的 remote:从公司 GitLab clone 得到的是 GitLab 侧的 `sure/sure-evaluation`,从 GitHub clone 才是公开版。父仓库只记录经过验证的引擎 commit(gitlink)。

已有 clone 补拉 submodule:

```bash
git submodule update --init --recursive
```

要指到另一个 checkout(高级用法,跟 `evaluation_engine_root=` 参数干的是同一件事):

```bash
export SURE_EVALUATION_HOME=/path/to/sure-evaluation
```

指过去的 checkout 必须停在 `sure/runtime/evaluation/runtime.json` 钉的那个 commit 上,否则启动时就报 `evaluation engine commit differs from the locked runtime`。

引擎自带的 `sure-eval` CLI 由引擎的 Python 包提供,`npm install` 不装它。只有绕开 harness 手跑引擎脚本时才需要装:

```bash
pip install -e sure/external/sure-evaluation
```

正常跑 `/sure_eval`、`/sure_reval` 不用装:评测用的那套 Python 环境,harness 会按 `sure/runtime/evaluation/` 的契约自己装好,同时把引擎 commit 钉死。可选依赖组和 node-local 环境见引擎自己的文档。

### bump 引擎基线(维护者操作)

在改 `/sure_eval`、`/sure_reval`、route 选择、normalization 假设或 pipeline 兼容性文档前,先同步并验证 submodule:

```bash
git submodule sync --recursive
git submodule update --remote --merge sure/external/sure-evaluation
npm run sure:doctor
```

**接着必须把 runtime lock 一起改**,这一步漏了下次评测直接起不来:

```bash
git -C sure/external/sure-evaluation rev-parse HEAD          # 填进 runtime.json 的 engine_commit
sha256sum sure/external/sure-evaluation/pyproject.toml       # 填进 engine_pyproject_sha256
```

两个值写进 `sure/runtime/evaluation/runtime.json`。gitlink 和 runtime.json 里记的值对不上时,`/sure_eval`、`/sure_reval` 一解析 route plan 就当场报 `evaluation engine commit differs from the locked runtime`,而 `npm run sure:doctor` 在 submodule 正常的情况下查不出这一项。

三样一起提交:

```bash
git add .gitmodules sure/external/sure-evaluation sure/runtime/evaluation/runtime.json
git commit -m "chore(sure): bump sure-evaluation submodule"
```

在共享部署检出中不要直接开发或提交；从隔离开发检出走评审和 CI。

## 数据集与音频路径解析

`sure_benchmark/jsonl` 是引擎查参考文本的位置,默认:

```text
data/datasets/sure_benchmark/jsonl
```

它**不是** `datasets=` 参数该填的东西。`datasets=` 传活动站点策略允许的数据源路径，详见 [`/sure_eval` skill 契约](../sure/skills/sure_eval/SKILL.md)。

`SURE_EVAL_DATASETS_ROOT` 可以指向另一个包含 `sure_benchmark/jsonl` 的数据根,但它**只挪 JSONL 的查找位置**:

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

音频路径先看数据集 JSONL 里写的值:绝对路径直接用;相对路径依次试这五处,取第一个存在的:

1. JSONL 所在目录
2. 仓库根
3. 仓库根的 `data/datasets/sure_benchmark/SURE_Test_Suites/`
4. skill 目录 `sure/skills/sure_eval/`
5. skill 目录下同一串 `data/datasets/sure_benchmark/SURE_Test_Suites/`

音频放在别处的话,在上面这几个位置里挑一个,留条软链指过去。

## Route 选择

`metrics` 只有 `/sure_eval` 认:传一个 reported metric,就按它当前的默认链路跑。`/sure_reval` 反过来只认 `pipeline_id`,选的是精确链路;`metrics` 在 reval 是禁用参数,传了在 preStart 第一关就被拒。完整参数契约见对应的 [`/sure_eval`](../sure/skills/sure_eval/SKILL.md) 和 [`/sure_reval`](../sure/skills/sure_reval/SKILL.md) 文档。

同一 dataset 和 metric 想比多条链路,就用逗号一次传多条:

```text
pipeline_id=<id1>,<id2>
```

**别重复写 `pipeline_id=`**:斜杠命令的参数解析是同名键后者覆盖前者,重复写不会报错,只会静悄悄少跑一条。多条链路会各自写到独立的 metric 产物目录里,不互相覆盖。

## 典型链路

| 任务 | 链路形态 |
| --- | --- |
| ASR 中文 CER | `normalization/wetext_norm -> scoring/wenet_cer` |
| TTS/VC 中文 CER | `frontend/funasr_loader_16k_mono -> transcription/paraformer_zh -> normalization/punctuation_strip_norm -> scoring/wenet_cer` |
| TTS 英文 WER | `transcription/whisper_large_v3 -> normalization/whisper_norm -> scoring/wenet_wer` |

这张表只是大致形态。实际走哪条链路,以本次运行用的那个本地 `sure-evaluation` 引擎为准。

## 输出证据

一次 eval 或 reval 跑完,实际走了哪条链路,看下面这几个产物。注意 eval 和 reval 的 route plan 是两个 schema、两个落点:

| 产物 | 关键字段 |
| --- | --- |
| `/sure_eval`:`.sure/runs/<run_id>/artifacts/evaluation_route_plan.json`(schema `sure.harness.evaluation_route_plan.v1`) | `engine.engine_root` / `engine.commit`;`datasets[]` 的 `requested_metrics` / `selected_metrics` / `route_choices` / `selected_routes` |
| `/sure_reval`:先写在 `.sure/runs/<run_id>/scratch/evaluation_route_plan.json`,整棵 scratch 拷进批次包后,持久副本在 `sure/results/<镜像路径>/evaluation_runs/<批id>/evaluation_route_plan.json`(schema `sure.reval.route_plan.v1`) | `engine`;`selected_routes[]` 的 dataset / metric / pipeline_id / route_id / nodes |
| `evaluation_payload.json` | `results[].pipeline_id`、`results[].nodes`、metric 产物目录 |
| `metrics/<dataset>/<metric_slug>[__<pipeline_id>]/pipeline_description.json` | 精确 route 元数据 |
| 同目录 `report.json` | score 和 pipeline trace |

`/sure_reval` 还要检查 `reval_run_report.json`:

```text
evaluation_only=true
old_evaluation_reused=false
```
