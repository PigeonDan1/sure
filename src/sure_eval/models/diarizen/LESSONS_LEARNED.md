# DiariZen 配置经验教训总结

## 📋 配置过程概览

| 阶段 | 耗时 | 关键问题 |
|------|------|----------|
| 环境准备 | 30min | 未按官方顺序安装导致版本冲突 |
| 依赖冲突解决 | 2h+ | pyannote-audio 子模块与PyPI版本不兼容 |
| 模型测试 | 30min | 短音频文件触发 pyannote bug |
| 最终验证 | 15min | 成功运行，4 speakers 正确识别 |

**总计：约4小时**

---

## 🚨 踩过的坑（按严重程度排序）

### 1. 【致命】pyannote-audio 版本不兼容（最严重）

**问题描述：**
```
AttributeError: 'DiariZenPipeline' object has no attribute '_segmentation_model'
```

**根本原因：**
- DiariZen 嵌入了**修改过的** pyannote-audio 子模块
- PyPI 上的 pyannote.audio 3.1.1 与 DiariZen 不兼容
- DiariZen 的 pipeline.py 依赖子模块中的特定 API

**错误做法：**
```bash
pip install pyannote.audio  # 从PyPI安装 - 错误！
```

**正确做法：**
```bash
cd diarizen_src/pyannote-audio
pip install -e .  # 必须从子模块安装
```

**教训：** 当项目包含子模块时，必须优先检查子模块是否是修改过的版本。

---

### 2. 【严重】NumPy 版本冲突

**问题描述：**
- DiariZen 要求 NumPy 1.26.4
- 但安装后可能自动升级到 NumPy 2.x
- 导致 `numpy._core.multiarray` 相关错误

**根本原因：**
- PyTorch 2.4 支持 NumPy 1.x 和 2.x
- 但 pyannote.audio 子模块依赖 NumPy 1.x API

**解决方案：**
```bash
pip install numpy==1.26.4  # 严格锁定版本
```

**教训：** 不要假设 PyTorch 的兼容性等同于整个工具链的兼容性。

---

### 3. 【中等】PyTorch 版本选择

**问题描述：**
- 最初不确定 PyTorch 2.1 还是 2.4
- 担心 2.4 与 NumPy 1.26 不兼容

**验证结果：**
```bash
pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cpu
# 与 numpy==1.26.4 兼容 ✓
```

**教训：** PyTorch 2.4 的 release notes 声明支持 NumPy 2，但仍支持 NumPy 1.x。

---

### 4. 【中等】短音频文件 Bug

**问题描述：**
```
UnboundLocalError: cannot access local variable 'segmentations' where it is not associated with a value
```

**根本原因：**
- pyannote.audio 的 inference.py 在处理短音频时有 bug
- 当音频长度 < seg_duration (16s) 时，变量未初始化

**解决方案：**
- 使用 >= 30s 的音频文件
- 使用官方示例音频 `EN2002a_30s.wav`

**教训：** 测试时使用符合工具要求的样本数据，避免边缘情况。

---

### 5. 【轻微】依赖缺失

**问题描述：**
安装后缺少 `psutil` 和 `accelerate`

**解决方案：**
```bash
pip install psutil accelerate
```

---

### 6. 【轻微】SpeechBrain 版本警告

**问题描述：**
```
UserWarning: Module 'speechbrain.pretrained' was deprecated
```

**处理：** 这是 deprecation warning，不影响功能，可忽略。

---

## ✅ 正确的安装步骤（必须严格按顺序）

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

# Step 7: 验证
python -c "from model import DiariZenModel; m = DiariZenModel(); print('OK')"
```

---

## 🔍 冲突分析方法论

### 配置前应该检查的冲突点：

1. **Python 版本要求**
   - DiariZen: >= 3.10
   - pyannote.audio: >= 3.10

2. **PyTorch 版本**
   - DiariZen: 未明确限制
   - pyannote.audio: >= 2.0
   - 选择：2.4.0 (最新稳定版)

3. **NumPy 版本**
   - DiariZen: 1.26.4 (requirements.txt)
   - PyTorch 2.4: 支持 1.x 和 2.x
   - 决策：严格使用 1.26.4

4. **pyannote.audio 来源**
   - 子模块 vs PyPI
   - 这是最容易忽视的冲突！

---

## 🎯 全局最优决策

当冲突发生时，优先级如下：

| 优先级 | 依赖项 | 理由 |
|--------|--------|------|
| 1 | pyannote.audio (子模块) | DiariZen 的核心依赖，必须匹配 |
| 2 | NumPy 1.26.4 | pyannote 子模块要求 |
| 3 | PyTorch 2.4.0 | 与 NumPy 1.x/2.x 都兼容 |
| 4 | Python 3.10 | 最低版本要求 |

**决策过程：**
1. 首先确定 pyannote 必须来自子模块（不可妥协）
2. 子模块要求 NumPy 1.x，锁定 1.26.4
3. PyTorch 2.4 支持 NumPy 1.x，选择最新版
4. 最后验证整个链条

---

## 📚 关键文件参考

- `diarizen_src/requirements.txt` - 官方依赖列表
- `diarizen_src/pyannote-audio/requirements.txt` - 子模块依赖
- `diarizen_src/README.md` - 官方安装指南

---

## ⚠️ 避免的常见错误

1. ❌ `pip install pyannote.audio` （必须用子模块）
2. ❌ 不检查子模块是否为空 （`git submodule update --init`）
3. ❌ 安装顺序混乱 （先装DiariZen再装PyTorch）
4. ❌ 忽略 NumPy 版本锁定
5. ❌ 用短音频测试 (< 16s)

---

## 🏆 成功验证

```
Diarizing: EN2002a_30s.wav
✓ Diarization completed
  Detected 4 speakers
  13 segments
```

**模型配置成功！**
