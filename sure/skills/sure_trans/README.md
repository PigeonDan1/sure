# /sure_trans 技能介绍

`/sure_trans` 把已有模型交付转换成 SURE Eval 可消费的模型包。输入环境可以是 Dockerfile，也可以是本地 Python 可执行文件与锁文件；当前 Docker 流程保持不变，Python 已接通依赖扫描、环境探测、原始推理以及 adapter 的 import/load/infer/contract/MCP/等价性验证，`package=none` 最终交付在后续改造组接通。

## 核心概念

| 概念 | 说明 |
| --- | --- |
| Source image | 由交付物还原出的原版运行镜像:优先 `docker load` build context 内的镜像 tar,失败则回退 `docker build` 原 Dockerfile。 |
| Adapter image | 在 source image 之上叠加 `/opt/sure_trans/`(`model.py` + `server.py` + `config.yaml` + `model.spec.yaml` + `__init__.py` + `validate.py`)生成的新镜像,实现 `ModelWrapper` 与 MCP 协议,并携带模型本地验证入口 `validate.py`。 |
| Digest 固定 | 所有交接引用使用 `image@sha256:...`,禁止可变 tag;registry push 后必须按 digest 精确 pull 并复验。 |
| 站点解析交付 | source/adapter 仓库由活动站点策略统一解析,agent 不拼接 namespace;解析结果和策略身份写入 `trans_input_resolved.json`。 |
| Container-only | Eval 运行时完全在容器内:`host_python_fallback=false`、`image_override_allowed=false`,模型 payload 以只读方式挂载。 |
| IO contract | `input_type=audio_path` 到 `output_type=json`,`primary_field=text`,`required_fields=["text"]`、`nonempty_fields=["text"]`、`json_serializable=true`,由 `validate.py --stage contract` 对 `sample_output.json` 校验。 |
| 模型 bundle | 最终交接目录 `sure/models/<model_name>/`:wrapper 五件套 + `Dockerfile.sure` + 模型 payload + `fixture/<task>/` + `artifacts/` terminal sidecar。`/sure_infer` 只挂载该目录,外部绝对路径不是可执行交接。 |

## 参数

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `dockerfile` | 条件必填 | Docker 输入的既有 Dockerfile 绝对路径；与 `python_executable` 二选一。 |
| `python_executable` | 条件必填 | Python 输入的可执行文件绝对路径；与 `dockerfile` 二选一。 |
| `lockfile` | Python 必填 | Python 输入的可复现依赖锁文件绝对路径。 |
| `package` / `package_profile` | 否 | `docker-registry`（默认）或 `none`；`none` 仅接受 Python 输入。 |
| `model` | 是 | 既有模型文件或目录绝对路径。 |
| `inference_entrypoint` | 是 | 既有推理入口绝对路径,别名 `inference_code`。 |
| `framework` | 是 | 计算框架，必须为 `pytorch`；接受 `torch` 别名。 |
| `model_framework` | 是 | 模型实现框架，推荐 `transformers`，也可填写 `wenet`、`funasr`、`custom` 等安全标识符。非 Transformers 不会单独阻断。 |
| `task_type` | 否 | 默认从证据推断,歧义时强制显式给出,例如 `asr`。 |
| `source_image_policy` | 否 | `auto`(默认)/`load`/`build`。`auto` 先找 build context 下的镜像 tar,失败回退 build。 |
| `build_context` | 否 | 默认取 Dockerfile 父目录。 |
| `image_tar` | 否 | 显式指定镜像 tar,必须位于 `build_context` 内。 |
| `model_name` | 是 | 必须使用 `<组织>__<模型名称>` 格式；后续 bundle、镜像和 registry 命名均使用此值。 |
| `fixture` | 否 | 冒烟输入绝对路径,否则自动选择 build context 下无歧义的 `examples/smoke.*`。 |
| `device` | 否 | `auto`(默认)/`cuda`/`cpu`。`cpu` 只用本地 Docker;`cuda` 和 `auto` 先通过 VC 做 GPU 验证,符合条件的 `auto` 才可回退本地 CPU。 |
| `vc_partition` | 否 | GPU 验证分区;默认取活动站点策略的 `execution.vc_default_partition`。 |
| `vc_memory_gb` / `vc_gpus` | 否 | GPU 验证资源覆盖;默认 32 GiB、1 GPU。 |
| `model_mount_target` | 否 | 默认 `/models/<model_name>`。 |
| `model_stage_policy` | 否 | `auto`(默认)/`copy`/`hardlink`。 |
| `image_version` | 否 | 显式 registry tag 覆盖项。省略时查询站点解析出的 source/adapter 仓库，选择已有三段数字版本的下一个 patch；空仓库从 `0.1.0` 开始。 |
| `max_retries` | 否 | 默认 3。 |

启动示例:

```text
/sure_trans dockerfile=/path/to/Dockerfile model=/path/to/model inference_entrypoint=/path/to/infer.py framework=pytorch model_framework=transformers model_name=organization__model task_type=asr source_image_policy=auto
```

Python 输入示例:

```text
/sure_trans python_executable=C:\path\.venv\Scripts\python.exe lockfile=C:\path\requirements.lock.txt model=C:\path\model inference_entrypoint=C:\path\infer.py framework=pytorch model_framework=transformers model_name=organization__model task_type=asr package=none
```

Python 输入的原始推理命令必须使用 `python_executable` 指向的解释器。环境探测和原始推理直接在本机运行，不调用 Docker 或 VC。

Python adapter 复用同一个已验证解释器与 lockfile。`materialize_adapter_runtime.py` 固定解释器、lockfile 和 adapter 文件哈希；本组不复制平台专用虚拟环境，最终 uv runtime 的物化和便携交付由 `package=none` 终态流程完成。

`examples/minimal-input.json` 是同一组参数的 JSON 形式。

框架检测只把 PyTorch 作为硬门槛。`model_framework=transformers` 是推荐路径；若申报其他模型框架，或静态分析发现 PyTorch 实现未使用 Transformers，流程继续运行，并在 `framework_detection.json` 和最终 `verdict.json.framework` 中记录申报值、检测分类、架构线索与澄清。后续原始推理、adapter 推理和等价性 gate 仍必须全部通过。

source/adapter 仓库分别由 `network.container_registry` 和 `container_delivery.repository_template` 解析;source 仓库在目标仓库名后追加 `-source`。自动版本解析结果记录在 `trans_input_resolved.json.image_version_resolution`。查询复用 Docker 登录凭据但不把凭据写入 artifact；registry 查询失败会阻断，不会猜测可能重复的版本。并发运行仍可能同时选中同一版本，最终由 registry 的不可覆盖策略阻止冲突，失败的一方重新解析版本后再提交。

source 镜像构建会自动追加一层：若基础镜像没有 `git`，按镜像内可用的 apt/apk/dnf/yum/microdnf 安装 `git` 和 `ca-certificates`；原始 Dockerfile 不会被改写，最终 `USER` 会恢复。这样 adapter 镜像继承该工具，避免 `/sure_infer` 运行时缺少 `git`。

adapter 镜像同时复制当前锁定的 Harness Runtime。默认从 `SURE_HARNESS_RUNTIME_ROOT` 目录复制；配置 digest 固定的 runtime image 后，设置 `SURE_HARNESS_RUNTIME_IMAGE=<repository>@sha256:<digest>`，并传入 `--build-context sure_harness_runtime=docker-image://<repository>@sha256:<digest>`。最终 `/sure_infer` 使用镜像内的 Model Python 和 Harness Python 两个独立运行时，不再把仓库 Harness Runtime 挂载进模型容器。

## 工作流(20 个单元)

状态机逐个单元推进,当前单元产出其声明 artifact 后才进入下一单元。gate 单元有两类确定性脚本:`check_artifact.py` 做语义校验(路径归属、digest 固定、哈希复验、readiness 布尔),`run_trans_validate.py` 真实执行 artifact 里声明的 `run_command` 并记录退出码与日志;手工写的 `status=passed` 不被认可。

| # | 单元 | 产出 | 阶段 |
| --- | --- | --- | --- |
| 1 | `load_trans_input` | `trans_input_resolved.json` | 输入解析 |
| 2 | `inspect_dependencies` | `inference_dependency_report.json` | 静态分析 |
| 3 | `detect_framework` | `framework_detection.json` | 静态分析 |
| 4 | `prepare_fixture` | `fixture_manifest.json` | 静态分析 |
| 5 | `build_source_image` | `source_image_result.json` | 原版验证 |
| 6 | `validate_env_compat` | `execution_compat.json` | 原版验证 |
| 7 | `validate_original_inference` | `original_inference_result.json` | 原版验证 |
| 8 | `stage_model_payload` | `model_payload_manifest.json` | 打包 |
| 9 | `generate_adapter` | `adapter_manifest.json` | 打包 |
| 10 | `build_adapter_image` | `adapter_image_result.json` | 打包 |
| 11 | `validate_import` | `import_result.json` | adapter 验证 |
| 12 | `validate_load` | `load_result.json` | adapter 验证 |
| 13 | `validate_infer` | `infer_result.json` | adapter 验证 |
| 14 | `validate_contract` | `contract_result.json` | adapter 验证 |
| 15 | `validate_mcp` | `mcp_result.json` | adapter 验证 |
| 16 | `validate_equivalence` | `equivalence_result.json` | 等价性验证 |
| 17 | `package_container` | `docker_registry_result.json` | 发布 |
| 18 | `write_runtime_inventory` | `runtime_inventory.json` | 发布 |
| 19 | `verdict` | `verdict.json` | 发布 |
| 20 | `finalize_model_bundle` | `deployment_ready.json` | 交接 |

## 日志与产物位置

每次运行产生独立 run 目录:

- `.sure/runs/<run_id>/events.jsonl`:全量事件流(tool 调用、gate 判定),排查卡点首选。
- `.sure/runs/<run_id>/state.json`:状态机位置(`currentUnit`、`completedUnits`、`retries`),支持断点续跑。
- `.sure/runs/<run_id>/artifacts/`:每个单元的产物 JSON,以及 gate 脚本自写的执行日志,例如 `source_image_load.log`、`original_inference_execution.log`。
- `.sure/runs/<run_id>/artifacts/` 中的交接文件:`runtime_binding.json`(三运行时职责声明)、`package_gate.json`、`artifact_manifest.json`、`validation.log`、`sample_output.json`、`deployment_ready.json`(与模型 bundle 逐字节一致)。
- `.sure/runs/<run_id>/fixture/`、`original_output/`、`adapter/`:中间数据与生成的 adapter 源码。
- `sure/.runtime/harness/logs/bootstrap-*.log`:Harness Runtime 首次物化的构建日志。

## 最终 bundle 布局(与 /sure_onboard 对齐)

`finalize_model_bundle` 通过后,`sure/models/<model_name>/` 与 `/sure_onboard` 的产物布局一致,`/sure_infer` 直接消费同一组 terminal sidecar:

```text
sure/models/<model_name>/
├── model.spec.yaml
├── model.py / server.py / __init__.py / validate.py   # wrapper
├── config.yaml                                          # server launch config
├── Dockerfile.sure                                      # adapter Dockerfile(sha256 记录在 package_gate)
├── artifacts/
│   ├── validation.log / sample_output.json
│   ├── docker_registry_result.json
│   ├── package_gate.json / verdict.json
│   ├── artifact_manifest.json
│   ├── runtime_inventory.json                     # container-only Eval binding
│   └── deployment_ready.json                      # terminal immutable readiness marker
└── fixture/<task>/                                 # 冒烟音频 + gt.jsonl
```

对齐要点:

- `package_gate.json` 使用 `sure.onboard.package_gate.v2`,`model_dir="."`、`artifact_manifest_path="artifacts/artifact_manifest.json"`,`readiness.{local_ready,docker_ready,registry_ready,bundle_ready}=true`,`docker.dockerfile_sha256` 对应 bundle 根目录的 `Dockerfile.sure`。
- `artifact_manifest.json` 使用 `sure.onboard.artifact_manifest.v1`,`phase=deployment_ready`、`status=finalized`,required 含全部 terminal sidecar。
- `runtime_inventory.json` 使用 `sure.onboard.runtime_inventory.v2`,`policy.eval_runtime=container_only`、`host_python_fallback=false`、`image_override_allowed=false`、`nfs_models_mutable_by_eval=false`。adapter 镜像内置锁定版 Harness Runtime,`harness_runtime.required=true`,`/sure_infer` 使用镜像内的 Harness Python。
- `deployment_ready.json` 使用 `sure.onboard.deployment_ready.v1`,与 run 目录逐字节一致;ready bundle 必须声明 `integrity_profile=manifest-complete-v1`,`required_artifact_sha256` 覆盖 wrapper、Dockerfile、fixture、sample output、全部模型 payload 与 required sidecar,`bundle_identity_sha256` 为哈希表的摘要,四个 portable sidecar 不允许残留宿主机共享存储的绝对路径。
- `check_artifact.py --kind deployment_ready` 与 `/sure_onboard` 的 `check_finalized_bundle.py` 执行同一组校验:bundle 与 run 双写一致、哈希复验、bundle identity 重算、portable manifest、Dockerfile 哈希、执行策略与 digest 固定引用。

模型 payload(权重等文件)落在 bundle 根目录,与 `model.py`、`model.spec.yaml` 同级;`fixture/<task>/` 下是冒烟音频与 `gt.jsonl`,每行 `{audio, task_type, text}`。

### Gate 校验点

`check_artifact.py` 各 `--kind` 的语义校验与 `/sure_onboard` 的确定性脚本一一对应:

- `input`:`dockerfile`/`build_context`/`model_path`/`inference_entrypoint` 必须为存在的绝对路径；`framework=pytorch` 和非空 `model_framework` 必须同时存在；`model_dir` 必须精确等于 `<repo>/sure/models/<model_name>` 且不能是目录软链,对齐 `check_model_input.py`。
- `framework`:静态分析必须检测到 PyTorch；Transformers 是推荐项而非硬门槛，其他模型框架必须写入架构澄清。
- `model_payload`:`destination` 必须等于 harness 拥有的 bundle 目录,外部路径复用被阻塞。
- `adapter`:`model.py`/`__init__.py`/`validate.py`/`server.py`/`config.yaml`/`model.spec.yaml`/`dockerfile` 七类文件必须全部存在,`model.py` 不允许残留 `NotImplementedError`/`TODO`。
- `registry`:`status=passed`、`pull_verified=true`,`target_image_ref` 与 digest 必须 digest 固定。
- `runtime_inventory`:`policy.eval_runtime=container_only`、`host_python_fallback=false`、`nfs_models_mutable_by_eval=false`,模型挂载只读;`harness_runtime.required=true` 且必须是镜像内 runtime binding,不允许写入宿主机绝对路径。
- `verdict`:`status=success` 且 `readiness` 为对象,`bundle_ready=true`、`registry_ready=true`。
- `deployment_ready`:见上文,与 `check_finalized_bundle.py` 同套校验,遗留的宿主机绝对路径直接拒绝。

## 环境前置要求

| 依赖 | 说明 |
| --- | --- |
| `uv` | Harness Runtime 引导必需,可用 `SURE_UV_BIN` 指定。 |
| Python 3.11 | 引导复制 host CPython 3.11 的 stdlib 与共享库。conda 版 `INSTSONAME=libpython3.11.a` 但只带 `.so`,会报 "standard library or shared library is missing",需用 python-build-standalone 等正牌 CPython。 |
| Docker | source 与 adapter 镜像的 load、build、运行、push/pull 全部依赖本地 Docker daemon。部分站点的 `docker` 是包装脚本,容器内进程失败时仍可能返回 0,gate 不能只信退出码。 |
| VC | `device=cuda` 和 `auto` 必须能调用 `vc`;分区和默认资源来自站点策略与命令参数。 |
| GPU | 视模型规格而定,7B BF16 模型约需 14 GiB 空闲显存;GPU smoke 在 VC 作业内执行,不占用登录节点本地 GPU。 |
| PyPI 网络 | 首次运行从 PyPI 物化 Harness Runtime 依赖,可通过 `UV_DEFAULT_INDEX` 指定镜像源。 |

确认是 `CUDA out of memory` 时,验证 gate 会在当前 VC partition 自动重新提交最多 8 次,让调度器重新分配 GPU;每次尝试保留独立日志。VC 接口不能指定物理卡,因此不保证 8 次对应 8 张不同 GPU。8 次都失败后才报告显存修复建议。

## 相关文档

- `SKILL.md`:agent 侧操作手册,含参数边界、失败规则、确定性脚本命令。
- `schemas/`:全部 artifact 的 JSON Schema 契约。
- `examples/minimal-input.json`:最小输入示例。
