# UV 环境策略 Playbook

## 何时使用 UV

沿用 backend_choice.json 的选择。适合可由 Python 包满足的依赖；需要系统库、
CUDA 编译或特殊工具链时按当前模型证据选择 backend，不把历史模型配置当通用默认。

## 环境创建方式

只补查尚不确定且影响当前选择的条件：Python/依赖/设备兼容性、所选来源可获取性、
锁文件在最终 runtime 中可物化性、安装与物化缓存衔接。复用已知证据；不全面测速、
扫描所有镜像或预下载大文件，不新增 gate。版本、代理、来源及目录由当前任务确定。

已有环境按原入口规则使用，不新增跨模型复用禁令或每次重建要求。
匹配的 sealed runtime 经原 verify_runtime 校验直接复用，未命中才物化；普通 .venv
不能替代 local package=none 的 sealed 绑定。复用不附带额外检查层，原有 gate 照常执行。

本次缓存是本次明确选择的目录，可以是已有共享缓存或其他 run 的缓存；helper 不自动搜索。
需要新选位置时，构建包缓存放在模型发布目录之外，记录实际绝对路径，不强制迁移旧缓存。
managed Python 是持久解释器安装，可能被 .venv 和 sealed runtime 引用，不能当缓存删除。
通过现有 site loader 获取站点 runtime root；不猜固定站点路径，不自动移动解释器或改链接。
权重/provider cache 与构建包缓存分开，仍遵守原权重记录和发布契约。

以下变量均取自本次 build_plan 的实际选择，在当前 shell 明确设置；不是预设值：
`MODEL_DIR`、`SELECTED_PYTHON`、`MODEL_PYTHON`、`BUILD_CACHE`、`DEPENDENCY_FILE`、
`LOCK_FILE`，以及需要显式配置时的 `UV_CONFIG`（现有 uv.toml）。

```bash
cd "$MODEL_DIR"
export UV_CACHE_DIR="$BUILD_CACHE"
uv cache dir                       # 核对实际生效路径
# 仅在需要创建验证环境时执行；已有环境沿用原规则
uv venv --python "$SELECTED_PYTHON" .venv
```

`MODEL_PYTHON` 指向实际选择的验证解释器；平台路径按现场确定。
若需要 uv 下载 Python，事先选定持久 `UV_PYTHON_INSTALL_DIR`，不要放进短期 run 清理目录。
需要 matplotlib 时设置本次 `MPLCONFIGDIR`，不向无关模型强加此配置。

## Lock/Sync 约定

在同一工作目录使用同一 Python、依赖输入、来源和必要解析配置。已有项目配置可直接复用；
配置发现可能因 cwd 改变时，通过 uv 原生 `--config-file` 和物化的 `--uv-config` 显式传递
同一 uv.toml，不新建配置格式。示例中无须显式配置时省略对应参数：

```bash
uv pip compile --python "$MODEL_PYTHON" --generate-hashes \
  --config-file "$UV_CONFIG" "$DEPENDENCY_FILE" -o "$LOCK_FILE"
uv pip sync --python "$MODEL_PYTHON" --require-hashes --strict \
  --config-file "$UV_CONFIG" --cache-dir "$BUILD_CACHE" "$LOCK_FILE"
```

`uv pip freeze` 不是 hash lock。选择与当前模型兼容的 Python/依赖/来源；不得默认切换
unsafe-best-match、镜像、CPU 或依赖版本。环境变量和命令行参数可以覆盖 uv 配置文件，
必须在各调用中保持所需有效设置一致；不依赖另一次 shell 的 export 自动延续。
代理、认证仍由当前调用的既有机制提供，不保存整份环境，不把凭据写进命令文本、锁文件
或可移植配置。便携配置中的来源应无凭据，路径应可重定位；站点路径只留在本次执行证据。
若诊断后修改配置，在现有 steps/evidence/repairs 中记录原因，必要时重新 compile/sync。

local package=none 由 Harness Python 运行 `scripts/materialize_model_runtime.py`，
显式传 `--uv-cache-dir "$BUILD_CACHE"`，需要时传 `--uv-config "$UV_CONFIG"`。
它从锁文件所在目录执行严格 sync：保持该目录与初装配置解析一致。
缓存缺包正常获取；实际物化需要的指定缓存不可访问时报错，不静默换空缓存。
未传缓存保留站点默认；已命中且验证通过的 runtime 不因未使用的缓存不可访问而失败。
缓存路径和控制字段不写进 build_env_result.json 或 portable runtime manifest。

uv 的默认链接策略与文件系统/平台有关；不固定 hardlink、copy、cp-al 或 symlink。
跨文件系统可使用 uv 的可用回退；仅在确有问题时检查文件系统与权限，按诊断调整并记录。
不要用单台机器的历史耗时保证收益，也不要因为链接回退就重装或清空缓存。

安装/探测失败必须保留原退出码，管道使用 `set -o pipefail`。完成后按原契约记录
python_executable、runtime_checks.required_imports、runtime_probe 和 repairs；
已有 model.py 的 repair flow 仍须通过原 import 检查，命令成功不等于 gate 通过。

## 常见失败和修复

- Python、torch/torchaudio/torchvision 与设备不兼容：根据当前上游约束、发行包和运行证据
  选择匹配组合；不按 host CUDA 数字直接猜 wheel，不照搬某个旧模型的 pin。
- NumPy、transformers、datasets/pyarrow 或 ModelScope 导入错误：读取实际版本要求和错误。
  例如旧代码的 PyExtensionType 错误可能需评估 pyarrow<21，不能无条件给所有任务降级。
- `sacrebleu` 缺失属于相应 evaluation 环境依赖：定位实际 metric runtime 修复，复用已有
  推理结果，不把曾用过的环境目录写成通用安装目标。
- 下载失败（例如 download-r2.pytorch.org 超时）：区分 DNS/连接/认证、HTTP 超时、缺少
  兼容 wheel 和来源解析策略；只检查影响当前选择的未知项，不自动切源或重装。
- 半安装状态：先确认原任务及受管理子进程结束，再诊断修复；需要重装时依据损坏证据
  使用安装器的 reinstall，不只凭包目录存在或 Checked 输出判断成功。

### 前台安装被 timeout 杀掉，留下装了一半的包

不要猜短的命令总时限，也不要按“预计十分钟”机械切换后台。需要交还控制权的 build_env
命令通过现有 shell 调用薄辅助；短命令可直接返回终态，长命令保留同一 attempt 回看：

```bash
# 从 skill package 以 Harness Python 调用；cwd 是被执行命令的目录
"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start \
  --run-dir "$RUN_DIR" --cwd "$MODEL_DIR" \
  --command 'uv pip sync --python .venv/bin/python --require-hashes --strict requirements.lock'
"$HARNESS_PYTHON_BIN" scripts/run_env_command.py status \
  --run-dir "$RUN_DIR" --attempt <start返回的attempt> --wait 30
"$HARNESS_PYTHON_BIN" scripts/run_env_command.py stop \
  --run-dir "$RUN_DIR" --attempt <同一attempt> --wait 30
```

示例中的 Python/lock 路径按本次选择替换，缓存/配置按上述规则在这次调用中提供。
传入内联命令，保留脚本名可见，不借包装执行其他 unit 的受限脚本；Harness 脚本仍用
Harness Python。辅助不替代 bash 工具，不改变业务 gate，不用来启动脱离进程组的守护程序。
安装类命令不得以 nohup/setsid 等方式脱离后台执行，hook 会拦截并要求改走 helper 或前台。

start/status/stop 的 `--wait` 默认最多30秒，只限制查询等待，不是杀进程时限。
返回 `running`、`succeeded`、`failed` 或 `unknown`；shell 调用返回0也可能只是 running，
必须读 status。日志及状态在 `<run_dir>/evidence/env-commands/<attempt>/`，不使用旧 rc、
全局 grep/pgrep 或成功产物判断本次任务。start 每次创建新 attempt，不能当作查询或自动重试。
启动长命令后按时回看同一 attempt（如 status --wait 循环），不要长时间静默等待。

先读日志和终态，再决定修复。stop 请求由本次持有身份的进程处理，确认受管理子进程均已
结束才返回 `processes_ended=true`；请求停止不等于已经停止。该标记仅确认本 attempt；共享目录须确认所有相关尝试结束。仍运行或状态不明时不得清理
工作目录、重启替代任务或凭保存的 PID 杀进程。失联不自动恢复，原有重试规则不变。
此辅助首版支持 POSIX 进程组（Linux/macOS）；不支持的平台明确报错，沿用已有前台 shell。
它只管理本次进程组，命令不得自行 daemonize/setsid 脱离管理；不建常驻服务或自动强杀策略。

物化日志显示锁等待、复用验证、venv、sync、freeze、发布等阶段和增量输出。
静默只触发检查，不自动判失败、杀进程、切源或换依赖。HTTP 请求超时、物化既有命令
总时限和查询等待是不同层次；本轮保留原有物化总时限，不叠加短杀进程时限。

## 集群网络限制

只依据当前站点证据判断可达性和认证要求，不预设封锁、代理或替代源。
首次获取慢、重复获取和终态发现延迟分别诊断；缓存与状态改进不保证消除全部耗时。
冷启动隔离仅用于明确指定的对照实验，生产运行不要求清空缓存或每次重建环境。
