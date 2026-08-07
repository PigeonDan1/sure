# 排错指南

优先运行 harness doctor：

```bash
npm run sure:doctor
```

它会检查仓库根目录、Node 依赖面、SURE skills、`sure-evaluation` 和 benchmark JSONL
发现路径。

## 出错了先看哪

- run 状态:`.sure/runs/<run_id>/state.json`——可恢复的进度和 `completed_units`。
- 评估产物:`sure/models/<model>/eval_runs/<run_id>/`——predictions、route plan、metric reports。
- 集群作业:提交步骤会打印查询命令——`vc info --job <id>`。

## `Cannot find module 'typebox'`

SURE slash commands 会一起注册。某个 skill 的依赖缺失，可能会在另一个 command 启动时暴露。

请在仓库根目录运行：

```bash
npm install --ignore-scripts
npm run sure:doctor
```

如果 sparse checkout 路径缺失：

```bash
git sparse-checkout add scripts fixtures packages/coding-agent/examples
npm install --ignore-scripts
npm run sure:doctor
```

然后通过本地入口启动 TUI：

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

## `rate_limit_exceeded: Concurrency limit exceeded`

模型提供方拒绝了 agent 请求，因为同一个账号已有太多活跃请求。它会中断 TUI 会话，但不代表
SURE run artifact 或模型 wrapper 无效。

关闭使用同一 provider 账号的其他 Pi/Codex/TUI 会话，等待网关释放进行中的请求，然后重新运行
同一条 slash command。

对于耗时较长的 `/sure_onboard`，可以查看：

```text
.sure/runs/<run_id>/state.json
```

## `/sure_init` 模型列表与 key 问题

供应商菜单在支持的地方实时拉模型列表。列表来源会以提示形式显示:

- `live`——供应商刚返回的,不用管。
- `cached`——在线查询失败,用了之前存下的网关列表;显示的模型可能过时。
- `builtin`——条目来自内置目录,没跟供应商确认过(不支持列表接口或查询失败时出现)。

`~/.pi/agent/models.json` 里的网关名和内置供应商重名时,菜单里会被跳过
——给网关条目改个名。

非交互 `/sure_init` 要传全参数:
`--option <供应商id> --model <模型id> --api-key <key>`;新建网关再加
`--name <名称> --base-url <地址>`。缺 key 会报
`No API key configured for <provider>`——传 `--api-key` 或改用交互模式。

## 找不到 Benchmark JSONL

如果 `/sure_eval` 或 `/sure_reval` 无法解析 dataset，确认下面路径存在：

```text
data/datasets/sure_benchmark/jsonl
```

或者设置：

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

并确保该 root 下包含 `sure_benchmark/jsonl`。

还没有数据的话，用仓库自带脚本从 ModelScope 下载并转换——完整命令序列
见用户指南「Benchmark 数据」一节（`download_sure_data.py --csv`、按档案
下音频、`convert_sure_to_jsonl.py`）。

## 数据集短别名触发几十 GB 下载

音频档案没备齐时传短别名（比如 `datasets=aishell1`），dataset manager
会判定数据缺失,直接启动一次 52.5 GB 的 ModelScope 全量下载——不提示、
不出进度、没有超时。run 在数据集解析之后突然没了动静,多半就是这个。
一律填完整 JSONL 文件名（比如 `datasets=aishell1-test_ASR`）就绕开了,
它们永远不会触发下载。

## VC 执行没有提交证据

当请求 `execution=vc` 时，成功运行必须包含真实 VC 提交证据。Harness 不应静默 fallback
到 local。

本地 smoke 和开发使用 `execution=local`。只有期望真实提交到 VC 集群时才使用
`execution=vc`。

## 把作业投到指定 VC 分区

在 `/sure_eval` 里配合 `execution=vc` 加 `vc_partition=<分区名>`。不传时由
harness 自动选分区。分区名不在你的可用范围内时,输入解析阶段会直接报错,
错误信息会列出你能用的分区(来自 `vc info -u`)。`vc info -u` 本身失败、
超时或返回为空时,这道前置检查会跳过——解析阶段没报错不等于分区名对了,
最终以 `vc submit` 的结果为准。

## 精确 `pipeline_id` 失败

精确 pipeline ID 由当前选择的 `sure-evaluation` checkout 决定。如果之前有效的 ID 失败：

```bash
cd sure/external/sure-evaluation
git status --short
git rev-parse HEAD
```

然后对照当前 engine catalog 或 describe 命令确认请求的 pipeline。修正 ID 后，把
`/sure_reval` 输出到新的 tmp 目录重新运行。
