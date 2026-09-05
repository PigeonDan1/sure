# UV 环境策略 Playbook

## 何时使用 UV

| 条件 | 使用 UV |
|------|---------|
| 纯 Python 依赖 | ✅ 推荐 |
| 主要依赖为 PyPI 包 | ✅ 推荐 |
| 无复杂 C++ 扩展 | ✅ 推荐 |
| 无系统库依赖 | ✅ 推荐 |
| 无 CUDA 编译需求 | ✅ 推荐 |

## 环境创建方式

```bash
cd sure/models/{model_name}

# 每个模型使用 model-local cache，避免污染全局环境或被其它 run 影响
export UV_CACHE_DIR="$PWD/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.runtime/uv-python"
# 不要设 UV_LINK_MODE。uv 默认用硬链接装包；上面的 cache 和 .venv 在同一个目录树下，
# 同一个文件系统，硬链接不搬任何字节。设成 copy 会强制逐文件全量拷贝，在共享存储上
# 每个文件一次同步往返：torch 那一档实测 59 秒 vs 55 分钟。详见「常见失败」第 5 条。
export MPLCONFIGDIR="$PWD/.runtime/matplotlib"
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$MPLCONFIGDIR"

# 自检：确认 uv 真的用了这个 cache。环境变量设了不等于生效，没生效就会落回
# ~/.cache/uv，跟 .venv 未必同盘，硬链接就用不上了。
uv cache dir   # 应当输出上面的 $UV_CACHE_DIR

# 创建环境
uv venv --python=python3.10

# 激活环境
source .venv/bin/activate

# 安装依赖
uv pip install -r requirements.txt
# 或
uv pip install -e .
```

脚本中如果使用 `uv pip install ... | tee build.log`、`uv pip install ... | tail ...`
或任何管道，必须启用 `set -o pipefail`，否则 uv 的失败退出码可能被 `tee`/`tail`
掩盖成成功。安装完成后必须用 `.venv/bin/python` 做短 runtime probe，并把
`python_executable`、`runtime_checks.required_imports`、`runtime_probe` 写入
`build_env_result.json`；对于已经存在 `model.py` 的 repair flow，`import model`
必须通过后才能写 `env_ready=true`。

## Lock/Sync 约定

```bash
# 从项目依赖生成带哈希的可复现锁文件
uv pip compile \
  --python .venv/bin/python \
  --generate-hashes \
  requirements.txt \
  -o requirements.lock

# 严格按 lock 恢复，删除未声明包并校验每个分发包哈希
uv pip sync \
  --python .venv/bin/python \
  --require-hashes \
  --strict \
  requirements.lock
```

若项目以 `pyproject.toml` 声明依赖，将 `requirements.txt` 换成
`pyproject.toml`。`uv pip freeze` 只记录已安装版本，不记录分发包哈希，不能作为
`package=none` 的可提升锁文件。完成模型本地验证后，由 Harness Python 运行
`scripts/materialize_model_runtime.py`；不要把 `.venv` 本身当作 Eval runtime。

## 常见失败和修复

### 1. 系统 PyTorch 与虚拟环境冲突

**症状**: `ModuleNotFoundError: No module named 'torch._utils'`

**原因**: UV 环境隔离导致无法访问系统已安装的 PyTorch

**修复**:
```bash
# 在虚拟环境中重新安装 PyTorch
uv pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cpu
```

### 2. NumPy 版本冲突

**症状**: `AttributeError: np.sctypes was removed in the NumPy 2.0`

**原因**: NeMo 等库不支持 NumPy 2.0

**修复**:
```bash
uv pip install numpy==1.26.4
```

### 3. Torchvision 版本不匹配

**症状**: `RuntimeError: operator torchvision::nms does not exist`

**修复**:
```bash
# 安装与 PyTorch 匹配的 torchvision
uv pip install torchvision==0.19.0 --index-url https://download.pytorch.org/whl/cpu
```

### 4. S2TT metric 缺少 sacrebleu

**症状**: speech-understanding 或 S2TT metric runner 报：

```text
No module named 'sacrebleu'
```

**原因**: S2TT BLEU / chrF metric 依赖 `sacrebleu`。这是 evaluation 环境依赖缺失，
不是模型推理失败。

**修复**: 使用 uv 安装到当前 SURE/evaluation 环境，不要改模型 wrapper 或重跑模型推理：

```bash
env UV_CACHE_DIR=src/sure_eval/evaluation/nodes/scoring/sacrebleu/.cache/uv \
uv pip install \
  -p .venv.hostbak/bin/python \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  sacrebleu
```

安装后重跑同一个 metric runner，复用已有 `ref_*.txt` / `hyp_*.txt`。

### 5. CUDA torch wheel 下载超时

**症状**: 本地 uv 环境安装 CUDA 版 PyTorch 报：

```text
Failed to fetch https://download-r2.pytorch.org/...torch-*.whl
operation timed out
```

**原因**: `download.pytorch.org` / `download-r2.pytorch.org` 在当前网络下可能不稳定。

**修复原则**:

- 先根据 host driver/CUDA 选择匹配 wheel，例如 CUDA 12.8 host 优先尝试 cu128。
- 使用模型本地 `.venv`，不要切回 base Python。
- 外网下载可临时开代理；Docker、GPU、registry、ModelScope mirror 操作仍应清代理。
- CPU fallback 只能在记录 CUDA-first 失败和至少三次 CUDA 环境修复尝试后进入。

示例：

```bash
set -o pipefail
bash -lc '. <proxy-on-script> && \
  UV_CACHE_DIR="$PWD/.runtime/uv-cache" \
  uv pip install \
    --python .venv/bin/python \
    --index-url https://download.pytorch.org/whl/cu128 \
    torch==2.8.0+cu128 torchaudio==2.8.0+cu128; \
  status=$?; \
  . <proxy-off-script>; \
  exit $status' 2>&1 | tee artifacts/cuda_torch_install.log
```

如果 uv 报：

```text
Failed to hardlink files; falling back to full copy
```

不要设 `UV_LINK_MODE=copy` 把它压掉。这条提示说明 uv 退回了全量拷贝，安装会慢一个
数量级，先查是不是真的跨文件系统：

```bash
stat -c '%d' "$UV_CACHE_DIR" "$PWD/.venv"
```

两个值相同就是同盘，硬链接本来可用，不要动 `UV_LINK_MODE`。共享存储上强制 copy 的代价
是每个文件一次同步往返。torch 2.8.0 + torchaudio 2.8.0 这一档（26 个包、约 2.7 万个
文件、6.9G）在同一台调试机、同一个共享盘上的实测：

| `UV_LINK_MODE` | `Installed` 耗时 | 数据来源 |
|---|---|---|
| 默认（硬链接） | 59.42 秒 | 热缓存下重测 |
| `copy` | 55 分 26 秒 | Voxtral-Mini-4B onboarding 实跑 |

慢 56 倍。copy 那次的 `Prepared` 只用了 436ms，包早就在缓存里，55 分钟全花在往 `.venv`
逐个文件搬，跟下载无关。

两个值不同才是真跨盘。这时的正确做法是把 `UV_CACHE_DIR` 挪到和 `.venv` 同一个文件系统上，
让硬链接重新可用，而不是设 copy。

顺带：`Failed to hardlink` 确实不是安装失败，别把它当错误去 retry。

### 6. TTS 本地 uv 依赖 pinning

F5-TTS / IndexTTS-2 re-onboarding 经验：

- CUDA host 可见时，优先 pin `torch==2.8.0+cu128`、`torchaudio==2.8.0+cu128`
  或与当前 driver 匹配的 CUDA wheel。
- 如果出现 `torch.cuda.is_available() == False`，不要直接改 CPU；先检查 wheel CUDA
  tag、driver、`LD_LIBRARY_PATH` 和模型 `.venv`。
- 如果复用 host 已验证的 CUDA torch（例如 system-site-packages 中的
  `torch==2.3.1+cu121`），不能再安装要求更高 torch 的 `transformers>=5`
  或模型依赖。二选一：
  1. 保持该 torch，并 pin 与它兼容的 transformers/模型代码版本；
  2. 更推荐在模型 `.venv` 内安装与 host driver 匹配的 CUDA torch，例如
     `torch==2.8.0+cu128` + `torchaudio==2.8.0+cu128`，再安装
     `transformers>=5`。
  任何 `transformers` import 报 “PyTorch >= X is required” 都是 build_env 失败，
  不允许进入后续 validate 节点。
- 装 `transformers` 之前先读 checkpoint 自己声明的版本，不要直接装源上的最新版：

```bash
python3 -c "import json;print(json.load(open('checkpoints/config.json')).get('transformers_version'))"
```

  读到 `5.2.0.dev0` 就 pin `transformers==5.2.0`。装成更新的版本，模型代码往往要到
  `validate_load` / `validate_infer` 才报错，那时 docker image 已经建过一次，
  返工要重装依赖再重建镜像。Voxtral-Mini-4B 那次就是装了 5.15.0，checkpoint 要 5.2.0，
  多花约 25 分钟。
- `datasets` 与新版 pyarrow 可能出现 `AttributeError: module 'pyarrow' has no attribute
  'PyExtensionType'`，应 pin `pyarrow<21`。
- ModelScope 依赖可能要求不可解的旧包；已验证组合可以从 `modelscope==1.27.0` 加
  显式音频依赖开始，例如 `descript-audiotools==0.7.2`。
- uv 安装 PyPI 依赖优先使用清华源，避免默认 `https://pypi.org/simple` DNS/连接失败：

```bash
UV_CACHE_DIR="$PWD/.runtime/uv-cache" \
UV_PYTHON_INSTALL_DIR="$PWD/.runtime/uv-python" \
uv pip install --python .venv/bin/python \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements.txt
```

TTS/VC 等模型 import 阶段可能触发 matplotlib cache；每个模型本地验证脚本应设置：

```bash
export MPLCONFIGDIR="$PWD/.runtime/matplotlib"
mkdir -p "$MPLCONFIGDIR"
```

### 7. 前台安装被 timeout 杀掉，留下装了一半的包

**症状**: 安装看着「失败」了，之后 import 报

```text
AttributeError: module 'torch' has no attribute '__version__'
```

或某个包目录存在但内容不全。

**原因**: 在前台用 `timeout N` 跑 `uv pip install`，N 猜短了，进程在写文件的中途被杀。
torch/CUDA 这一档就算一切正常也可能十几分钟以上，猜不准。

**修复原则**: 预计超过 10 分钟的安装和下载一律放后台，不要在前台猜时长。

```bash
nohup bash -c 'set -o pipefail; \
  uv pip install --python .venv/bin/python \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt > artifacts/build_env.log 2>&1; \
  echo $? > artifacts/build_env.rc' > /dev/null 2>&1 &
```

轮询等 `build_env.rc` 出现，再按退出码判断成败。不能只看进程没了就当成功：

```bash
while [ ! -f artifacts/build_env.rc ]; do sleep 30; done
rc=$(cat artifacts/build_env.rc)
tail -5 artifacts/build_env.log
[ "$rc" = 0 ] || { echo "build_env failed rc=$rc"; exit 1; }
```

已经被杀出半个包的，重装要带 `--reinstall`。不带的话 uv 只看包元数据，认为已安装就跳过，
只打印一行 `Checked N packages in ...`，看着像成功，其实什么都没干。

`SKILL.md` 里 “no long `sleep` polling” 只约束 `discover` 阶段（不许用轮询等权重下载），
`build_env` / `fetch_weights` 的长任务不在此列，应当后台加轮询。

## 集群网络限制

### 常见环境约束

在部分集群环境中，以下网络限制可能影响模型 onboarding：

- **HuggingFace 被封锁**：无法访问 `https://huggingface.co`，必须使用 **ModelScope** 作为模型/数据下载源。
- **PyTorch CDN 被封锁**：`https://download.pytorch.org` 可能无法访问，安装 torch 时需使用清华镜像：
  ```bash
  uv pip install torch==2.4.0 --index-url https://pypi.tuna.tsinghua.edu.cn/simple
  ```
- **torchvision 版本兼容性**：安装依赖后必须验证 torchvision 与 torch 版本匹配：
  ```bash
  .venv/bin/python -c "import torch, torchvision; print(f'torch {torch.__version__} + torchvision {torchvision.__version__}')"
  ```
  若 torchvision 主版本号与 torch 不匹配（例如 torch 2.4.0 + torchvision 0.26.0），必须降级 torchvision 到匹配版本。参考对应关系：
  | torch | torchvision |
  |-------|-------------|
  | 2.4.0 | 0.19.0 |
  | 2.5.0 | 0.20.0 |
  | 2.6.0 | 0.21.0 |

### 验证清单（onboarding 后必做）

```bash
# 1. 验证 torchvision 兼容性
.venv/bin/python -c "import torch, torchvision; tv_major = int(torchvision.__version__.split('.')[1]); torch_major = int(torch.__version__.split('.')[1]); assert tv_major == torch_major - 5, f'torchvision {torchvision.__version__} incompatible with torch {torch.__version__}'"

# 2. 验证 ModelScope 可达
.venv/bin/python -c "from modelscope.hub.api import HubApi; api = HubApi(); print('ModelScope OK')"

# 3. 验证模型可加载（不触发下载）
.venv/bin/python -c "from model import ModelWrapper; m = ModelWrapper(); m.load(); print('Model load OK')"
```

## 验证命令

```bash
# 验证 Python 版本
.venv/bin/python --version

# 验证关键包
.venv/bin/python -c "import torch; print(torch.__version__)"
.venv/bin/python -c "import numpy; print(numpy.__version__)"
```
