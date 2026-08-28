# 镜像打包参考（sure_trans）

本文件是 `/sure_trans` 镜像制作与发布的参考约定，口径与
`sure/skills/sure_onboard/references/AGENTS.md` 第 8 节一致。
硬约束以 `SKILL.md` 状态机、`schemas/` 与 gate 脚本为准；
本文档与硬约束冲突时，以 SKILL.md 和 gate 为准。

## 1. 适用范围

`/sure_trans` 固定产出两个镜像，registry 交付是成功条件：

- source 镜像：由用户交付环境（Dockerfile 或镜像 tar）物化的原始推理环境，用于原始推理基线验证。
- adapter 镜像：在 source 镜像上加 adapter 层（`/opt/sure_trans/` 下 wrapper 文件），实现 SURE ModelWrapper 与 MCP 协议，是 `/sure_eval` 的执行镜像。

`package_profile=docker-registry` 固定不变；推不上去 registry 时只能产出 partial/blocked，不能密封 Eval-ready bundle。

## 2. 镜像命名规则

部署 registry 取自站点策略 `network.container_registry`（`config/site.bundled.yaml` 或 `config/site.local.yaml`，用 `npm run sure:site-info` 查看），下文写作 `<container_registry>`。本站已把它配成 insecure registry，HTTP 可达，凭据在 `~/.docker/config.json`。

本地工作 tag（短名，仅本机调试用）：

```text
sure-trans/<model_name>:source-<dockerfile_sha256前16位>   # run_docker_build.py 自动生成
sure-trans/<model_name>:adapter
```

远端交付 tag（registry 服务端强制命名规范，不符合会被直接拒绝，exit 4）：

```text
<container_registry>/hpc/ai_asr-<name>:<version>
```

规则：

- 命名空间固定为 `hpc`，镜像名必须以 `ai_asr-` 开头，tag 是版本号。
  规范原文见内部 wiki（registry 拒绝信息中的链接）。实测：不带 `ai_asr-`
  前缀的名字 push 时服务端返回"没有权限, 镜像名称不符合规范"。
- source 镜像与 adapter 镜像各占一个仓库名：

  ```text
  <container_registry>/hpc/ai_asr-<model_name>-source:<version>
  <container_registry>/hpc/ai_asr-<model_name>:<version>
  ```

- 同一模型后续环境或脚本变更后重新交付，递增版本标签（`0.1.0` → `0.1.1`）。
  不得复用已经推送过且语义不同的 tag。
- 无论 tag 怎么变，最终交接引用一律使用 digest 固定形式 `<repo>@sha256:<digest>`；
  `docker_registry_result.json` 的 `pull_verified=true` 是硬要求。

## 3. 镜像边界

source 镜像只固化可复现运行环境：

- Python 版本、torch/CUDA（或 CPU）runtime、模型依赖包
- 交付 runtime（如 `longwavsplit`）与必要系统包（如 `ffmpeg`、`libsndfile1`）

adapter 镜像 = source 镜像 + adapter 层，不额外安装包。

不应固化进镜像、必须在运行时挂载的内容：

- 模型权重（只读挂载到 `model_mount_target`，如 `/models/<model_name>`）
- fixture / smoke 输入
- run 输出目录（`/sure-output` 类可写挂载）
- 宿主 `.venv` 与开发代码

trans 与 onboard 的一个有意差异：adapter 镜像**不打包 Harness Runtime**
（`runtime_inventory.harness_runtime.required=false`），由 `/sure_eval` 挂载仓库锁定的公共 Harness Runtime。

## 4. 必备文件

- source 镜像材料：用户 Dockerfile 或 `build_context` 内的镜像 tar（`source_image_policy=auto` 时先发现 tar、后回落 Dockerfile 构建）。
- adapter 目录（`scaffold_adapter.py` 生成，位于 `sure/models/<model_name>/adapter/`）：`Dockerfile.sure`、`model.py`、`server.py`、`config.yaml`、`model.spec.yaml`、`__init__.py`、`validate.py`。
- 密封 bundle 根：`Dockerfile.sure`（`finalize_trans_bundle.py` 密封时落地）。

与 onboard 8.4 的差异：trans 没有 `docker_build.sh` / `docker_validate.sh` 约定。source 镜像物化由 `scripts/run_docker_build.py` 执行，验证由 `scripts/run_trans_validate.py` 执行，均受 gate 校验。

## 5. 最小改动构建原则

- adapter 层尽量薄：新增 `COPY` / `RUN` 只追加在 `adapter/Dockerfile.sure` 尾部，不要重排或修改 source 镜像的既有层。
- 在镜像内临时 `pip install` 后，必须同步更新 `Dockerfile`，否则下次构建或集群运行不可复现。
- 需要加速下载时可用 BuildKit cache mount，例如 `RUN --mount=type=cache,target=/root/.cache/pip ...`。
- 构建完成后必须用 `docker image inspect <image>` 确认镜像存在。

## 6. 构建与推送步骤

1. 物化 source 镜像：

   ```bash
   "$HARNESS_PYTHON_BIN" scripts/run_docker_build.py --run-dir <run_dir> --produces <run_dir>/artifacts/source_image_result.json
   ```

2. source 镜像需要上集群做 GPU 验证时，先推远端（gate 脚本自动执行，名称强制
   `hpc/ai_asr-<model_name>-source:<version>`，证据写入
   `source_image_result.json` 的 `registry_ref`/`registry_push`）。手动等价命令：

   ```bash
   docker tag <source_image_id> <container_registry>/hpc/ai_asr-<model_name>-source:<version>
   docker push <container_registry>/hpc/ai_asr-<model_name>-source:<version>
   ```

3. 构建 adapter 镜像（`adapter/Dockerfile.sure` 以 digest 固定的 source 镜像为基底）：

   ```bash
   docker build -f adapter/Dockerfile.sure -t sure-trans/<model_name>:adapter <context>
   ```

4. 在 adapter 镜像内完成 import/load/infer/contract/mcp/equivalence 验证。GPU 模式下
   gate 脚本会先把 adapter 镜像推为 `hpc/ai_asr-<model_name>:<version>` 再上集群验证，
   证据写入 `adapter_image_result.json` 的 `registry_ref`/`registry_push`。

5. push adapter 镜像，解析 `sha256:...`，按 `repository@sha256:...` 精确 pull，并在 digest-pinned 镜像里复跑 MCP smoke。这是 `package_container` 单元的硬要求。

6. 把 digest 固定引用写入 `docker_registry_result.json`、`runtime_inventory.json`，最终 bundle 所有引用逐字一致。

## 7. push 失败恢复

如果 `docker push` 失败，不要立即放弃。典型可恢复信号与处理：

- 输出 `请求失败，状态码：502` 或其他 registry/proxy 5xx → 清除代理后重试：

  ```bash
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  docker push <container_registry>/hpc/ai_asr-<model_name>:<version>
  ```

- 退出码看似成功但没有正常 layer push / digest 输出 → 视为失败，记录原始输出。
- 清除代理后报 `operation not permitted` → 沙箱网络拦截直连 registry，请求非沙箱/完整网络权限后重试。
- registry 返回"镜像已存在，请更新 tag" → 当前 tag 不允许覆盖，递增版本 tag（如 `:adapter-v1.2`）后重新 build/tag/push。
- `docker pull` 返回 `manifest unknown` → 该 tag 当前不可从仓库拉取，不能作为集群任务镜像。
- push 成功或 tag 已存在后，都必须用 `docker pull <repo>@sha256:<digest>` 验证远端可拉取，只有 pull 返回 digest / `Image is up to date` 才可标记为可用。
- 以上恢复步骤仍失败时，在最终汇报中请用户在登录态完整的交互终端手动执行 push/pull。
