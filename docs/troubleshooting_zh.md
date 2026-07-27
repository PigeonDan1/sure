# 排错指南

优先运行 harness doctor：

```bash
npm run sure:doctor
```

它会检查仓库根目录、Node 依赖面、SURE skills、`sure-evaluation` 和 benchmark JSONL
发现路径。

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

## VC 执行没有提交证据

当请求 `execution=vc` 时，成功运行必须包含真实 VC 提交证据。Harness 不应静默 fallback
到 local。

本地 smoke 和开发使用 `execution=local`。只有期望真实提交到 VC 集群时才使用
`execution=vc`。

## 精确 `pipeline_id` 失败

精确 pipeline ID 由当前选择的 `sure-evaluation` checkout 决定。如果之前有效的 ID 失败：

```bash
cd sure/external/sure-evaluation
git status --short
git rev-parse HEAD
```

然后对照当前 engine catalog 或 describe 命令确认请求的 pipeline。修正 ID 后，把
`/sure_reval` 输出到新的 tmp 目录重新运行。
