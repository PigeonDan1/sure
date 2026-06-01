# DiariZen 配置规则（血泪教训总结）

> 违反以下规则将导致数小时的调试时间。

---

## 规则一：永远不要动源代码

### 原则
**永远不要修改 `diarizen_src/` 目录下的任何源代码**，即使看起来有个明显的 bug。

### 理由
1. **子模块是修改过的版本**：DiariZen 的 pyannote-audio 子模块是经过修改的，与 PyPI 版本不同
2. **修改会破坏一致性**：一处修改可能导致其他依赖该代码的地方出问题
3. **升级困难**：修改后的代码在更新子模块时会产生冲突
4. **难以复现**：别人按照 README 安装后，无法复现你的修改

### 替代方案
如果遇到问题，优先考虑：
- 检查安装步骤是否正确
- 检查版本是否匹配
- 使用 workaround（如音频长度要求）
- 查阅官方 issues

### 反例
```python
# ❌ 错误的：直接修改 inference.py 修复短音频 bug
# 在 diarizen_src/pyannote-audio/pyannote/audio/inference.py 中

# 正确做法：使用满足条件的音频文件（>= 30s）
```

---

## 规则二：必须严格按照 README 逐步安装

### 原则
**安装步骤必须严格按照官方 README 的顺序执行**，不得跳过或调整顺序。

### 标准安装顺序
```bash
# Step 1: 创建环境
conda create -n diarizen python=3.10 -y
conda activate diarizen

# Step 2: 安装 PyTorch
pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cpu

# Step 3: 安装 DiariZen
cd diarizen_src
pip install -e .

# Step 4: 【关键】安装 pyannote 子模块
cd pyannote-audio
pip install -e .

# Step 5: 锁定 NumPy 版本
pip install numpy==1.26.4

# Step 6: 安装缺失依赖
pip install psutil accelerate
```

### 禁止行为
- ❌ 先装 DiariZen 再装 PyTorch
- ❌ 使用 `pip install pyannote.audio` 代替子模块安装
- ❌ 跳过 `pip install -e .` 直接用源码
- ❌ 不激活 conda 环境直接安装

---

## 规则三：配置前必须分析可能的冲突

### 原则
**在开始安装前，必须分析所有关键依赖的版本冲突可能性**。

### 检查清单

#### 1. Python 版本矩阵
| 组件 | 最低版本 | 推荐版本 |
|------|----------|----------|
| DiariZen | 3.10 | 3.10 |
| pyannote.audio | 3.10 | 3.10 |

#### 2. PyTorch 兼容性
- PyTorch 2.4 支持 NumPy 1.x 和 2.x ✅
- PyTorch 2.1 可能不支持某些新特性

#### 3. NumPy 版本锁定
```
DiariZen requirements.txt → numpy==1.26.4
                 ↓
        必须严格锁定此版本
                 ↓
PyTorch 2.4 支持 NumPy 1.26 ✅
```

#### 4. pyannote.audio 来源决策树
```
diariwen 是否包含 pyannote-audio 子模块？
    ├── 是 → 必须使用子模块版本
    │         └── pip install -e ./pyannote-audio
    └── 否 → 可以使用 PyPI 版本
              └── pip install pyannote.audio
```

### 分析方法
```bash
# 1. 查看官方依赖
cat diarizen_src/requirements.txt
cat diarizen_src/pyannote-audio/requirements.txt

# 2. 检查 PyTorch/NumPy 兼容性表
# https://pytorch.org/get-started/locally/

# 3. 搜索已知 issues
# https://github.com/BUTSpeechFIT/DiariZen/issues
```

---

## 规则四：冲突发生时，首先反思步骤跳跃

### 原则
**遇到错误时，首先怀疑安装步骤是否有跳跃或顺序错误**，而不是依赖本身有问题。

### 排查流程
```
发生错误
    ↓
是否严格按 README 顺序安装？
    ├── 否 → 重新安装，严格按照顺序
    │         └── 问题解决 ✅
    ↓
是否使用了子模块的 pyannote？
    ├── 否 → 卸载 PyPI 版本，安装子模块
    │         └── 问题解决 ✅
    ↓
NumPy 版本是否为 1.26.4？
    ├── 否 → 锁定版本
    │         └── 问题解决 ✅
    ↓
是否还有其他步骤遗漏？
    ├── 是 → 补全步骤
    ↓
才考虑依赖冲突或代码问题
```

### 常见跳跃错误
| 跳跃行为 | 错误症状 | 修复方法 |
|----------|----------|----------|
| 未装子模块 pyannote | `'DiariZenPipeline' object has no attribute '_segmentation_model'` | `cd pyannote-audio && pip install -e .` |
| NumPy 未锁定 | `numpy._core.multiarray` 错误 | `pip install numpy==1.26.4` |
| PyTorch 后装 | 依赖冲突警告 | 重新创建环境，先装 PyTorch |
| 未初始化子模块 | 空 pyannote-audio 目录 | `git submodule update --init --recursive` |

---

## 规则五：全局最优依赖决策

### 原则
**当冲突无法避免时，以最少修改实现全局最优解**。

### 决策优先级

```
                    冲突解决优先级
                    
         ┌─────────────────────────────┐
         │   1. 子模块完整性（最高）    │
         │      不可妥协               │
         └─────────────┬───────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   2. NumPy 版本锁定         │
         │      必须 1.26.4           │
         └─────────────┬───────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   3. PyTorch 版本           │
         │      选择最新兼容版         │
         └─────────────┬───────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   4. Python 版本            │
         │      满足最低要求           │
         └─────────────────────────────┘
```

### 决策案例：pyannote.audio 冲突

**场景**：PyPI 的 pyannote.audio 3.1.1 与子模块 API 不兼容

**可选方案：**

| 方案 | 修改内容 | 风险 | 决策 |
|------|----------|------|------|
| A | 修改 DiariZen 代码适配 PyPI 版 | 破坏原有逻辑，升级困难 | ❌ |
| B | 强制降级 PyPI 版到 3.0.x | 可能与其他依赖冲突 | ❌ |
| C | 使用子模块版本 | 无代码修改，官方推荐 | ✅ |

**全局最优解：方案 C**
- 无需修改任何代码
- 与官方环境完全一致
- 可复现、可维护

### 另一个案例：NumPy 版本

**场景**：DiariZen 需要 NumPy 1.26.4，但某些工具想要 NumPy 2.x

**决策：**
```
方案 A: 升级到 NumPy 2.x，修改 DiariZen 适配
    → 需要修改大量代码
    → 不符合规则一
    → ❌

方案 B: 锁定 NumPy 1.26.4，其他工具适配
    → PyTorch 2.4 支持 NumPy 1.x
    → 无需修改代码
    → ✅
```

---

## 总结

```
┌─────────────────────────────────────────────────────────┐
│                      黄金法则                            │
├─────────────────────────────────────────────────────────┤
│  1. 不动代码 → 2. 严格按序 → 3. 预判冲突 →             │
│  4. 反思跳跃 → 5. 全局最优                              │
└─────────────────────────────────────────────────────────┘
```

违反这些规则的代价：
- 规则一：数小时调试 + 维护噩梦
- 规则二：环境混乱 + 无法复现
- 规则三：盲目试错 + 时间浪费
- 规则四：错误方向 + 无用功
- 规则五：局部修复 + 全局问题

**遵循规则，一次成功。**
