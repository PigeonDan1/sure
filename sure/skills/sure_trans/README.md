# /sure_trans 技能介绍

`/sure_trans` 把一个已有交付环境的模型(Dockerfile + 模型权重 + 推理入口)转换成 SURE Eval 可直接消费的、digest 固定的容器化模型包,产出与 `/sure_onboard` 相同的 Eval-ready 契约。

## 核心概念

| 概念 | 说明 |
| --- | --- |
| Source image | 由交付物还原出的原版运行镜像:优先 `docker load` build context 内的镜像 tar,失败则回退 `docker build` 原 Dockerfile。 |
| Adapter image | 在 source image 之上叠加 `/opt/sure_trans/`(`model.py` + `server.py` + `config.yaml` + `model.spec.yaml` + `__init__.py` + `validate.py`)生成的新镜像,实现 `ModelWrapper` 与 MCP 协议,并携带模型本地验证入口 `validate.py`。 |
| Digest 固定 | 所有交接引用使用 `image@sha256:...`,禁止可变 tag;registry push 后必须按 digest 精确 pull 并复验。 |
| Container-only | Eval 运行时完全在容器内:`host_python_fallback=false`、`image_override_allowed=false`,模型 payload 以只读方式挂载。 |
| IO contract | `input_type=audio_path` 到 `output_type=json`,`primary_field=text`,`required_fields=["text"]`、`nonempty_fields=["text"]`、`json_serializable=true`,由 `validate.py --stage contract` 对 `sample_output.json` 校验。 |
| 模型 bundle | 最终交接目录 `sure/models/<model_name>/`:wrapper 五件套 + `Dockerfile.sure` + 模型 payload + `fixture/<task>/` + `artifacts/` terminal sidecar。`/sure_eval` 只挂载该目录,外部绝对路径不是可执行交接。 |

## 参数

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `dockerfile` | 是 | 既有 Dockerfile 绝对路径。 |
| `model` | 是 | 既有模型文件或目录绝对路径。 |
| `inference_entrypoint` | 是 | 既有推理入口绝对路径,别名 `inference_code`。 |
| `framework` | 是 | `pytorch_transformers`,接受 `pytorch`/`torch`/`transformers` 别名。 |
| `task_type` | 否 | 默认从证据推断,歧义时强制显式给出,例如 `asr`。 |
| `source_image_policy` | 否 | `auto`(默认)/`load`/`build`。`auto` 先找 build context 下的镜像 tar,失败回退 build。 |
| `build_context` | 否 | 默认取 Dockerfile 父目录。 |
| `image_tar` | 否 | 显式指定镜像 tar,必须位于 `build_context` 内。 |
| `model_name` | 否 | 默认取模型路径 basename。 |
| `fixture` | 否 | 冒烟输入绝对路径,否则自动选择 build context 下无歧义的 `examples/smoke.*`。 |
| `device` | 否 | `auto`(默认)/`cuda`/`cpu`。核心转换目前只用本地 Docker 验证。 |
| `model_mount_target` | 否 | 默认 `/models/<model_name>`。 |
| `model_stage_policy` | 否 | `auto`(默认)/`copy`/`hardlink`。 |
| `max_retries` | 否 | 默认 3。 |

启动示例:

```text
/sure_trans dockerfile=/path/to/Dockerfile model=/path/to/model inference_entrypoint=/path/to/infer.py framework=pytorch_transformers task_type=asr source_image_policy=auto
```

`examples/minimal-input.json` 是同一组参数的 JSON 形式。

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

`finalize_model_bundle` 通过后,`sure/models/<model_name>/` 与 `/sure_onboard` 的产物布局一致,`/sure_eval` 直接消费同一组 terminal sidecar:

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
- `runtime_inventory.json` 使用 `sure.onboard.runtime_inventory.v2`,`policy.eval_runtime=container_only`、`host_python_fallback=false`、`image_override_allowed=false`、`nfs_models_mutable_by_eval=false`。adapter 镜像不内置 Harness Runtime,`harness_runtime.required=false`,`/sure_eval` 从仓库挂载锁定版公共 Harness Runtime。
- `deployment_ready.json` 使用 `sure.onboard.deployment_ready.v1`,与 run 目录逐字节一致;`required_artifact_sha256` 覆盖 terminal sidecar 的 sha256,`bundle_identity_sha256` 为哈希表的摘要,四个 portable sidecar 不允许残留宿主机共享存储的绝对路径。
- `check_artifact.py --kind deployment_ready` 与 `/sure_onboard` 的 `check_finalized_bundle.py` 执行同一组校验:bundle 与 run 双写一致、哈希复验、bundle identity 重算、portable manifest、Dockerfile 哈希、执行策略与 digest 固定引用。

模型 payload(权重等文件)落在 bundle 根目录,与 `model.py`、`model.spec.yaml` 同级;`fixture/<task>/` 下是冒烟音频与 `gt.jsonl`,每行 `{audio, task_type, text}`。

### Gate 校验点

`check_artifact.py` 各 `--kind` 的语义校验与 `/sure_onboard` 的确定性脚本一一对应:

- `input`:`dockerfile`/`build_context`/`model_path`/`inference_entrypoint` 必须为存在的绝对路径;`model_dir` 必须精确等于 `<repo>/sure/models/<model_name>` 且不能是目录软链,对齐 `check_model_input.py`。
- `model_payload`:`destination` 必须等于 harness 拥有的 bundle 目录,外部路径复用被阻塞。
- `adapter`:`model.py`/`__init__.py`/`validate.py`/`server.py`/`config.yaml`/`model.spec.yaml`/`dockerfile` 七类文件必须全部存在,`model.py` 不允许残留 `NotImplementedError`/`TODO`。
- `registry`:`status=passed`、`pull_verified=true`,`target_image_ref` 与 digest 必须 digest 固定。
- `runtime_inventory`:`policy.eval_runtime=container_only`、`host_python_fallback=false`、`nfs_models_mutable_by_eval=false`,模型挂载只读;若 `harness_runtime.required=true`,必须是镜像内 runtime binding,不允许写入宿主机绝对路径。
- `verdict`:`status=success` 且 `readiness` 为对象,`bundle_ready=true`、`registry_ready=true`。
- `deployment_ready`:见上文,与 `check_finalized_bundle.py` 同套校验,遗留的宿主机绝对路径直接拒绝。

## 环境前置要求

| 依赖 | 说明 |
| --- | --- |
| `uv` | Harness Runtime 引导必需,可用 `SURE_UV_BIN` 指定。 |
| Python 3.11 | 引导复制 host CPython 3.11 的 stdlib 与共享库。conda 版 `INSTSONAME=libpython3.11.a` 但只带 `.so`,会报 "standard library or shared library is missing",需用 python-build-standalone 等正牌 CPython。 |
| Docker | source 与 adapter 镜像的 load、build、运行、push/pull 全部依赖本地 Docker daemon。部分站点的 `docker` 是包装脚本,容器内进程失败时仍可能返回 0,gate 不能只信退出码。 |
| GPU | 视模型规格而定,7B BF16 模型约需 14 GiB 空闲显存。 |
| PyPI 网络 | 首次运行从 PyPI 物化 Harness Runtime 依赖,可通过 `UV_DEFAULT_INDEX` 指定镜像源。 |

## 相关文档

- `SKILL.md`:agent 侧操作手册,含参数边界、失败规则、确定性脚本命令。
- `schemas/`:全部 artifact 的 JSON Schema 契约。
- `examples/minimal-input.json`:最小输入示例。
