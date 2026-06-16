# Phase-1 Target Policy

**Version**: 1.0  
**Scope**: All harness components, Builder Agent, Evaluator Agent  
**Purpose**: Define what constitutes success in Phase-1 onboarding

---

## Phase-1 定义

Phase-1 是**最小 repo-native 可调用路径验证**，不是完整能力评测。

### 目标边界

| 范围 | 包含 | 不包含 |
|------|------|--------|
| 验证内容 | import → load → infer → contract | 准确率、性能、多语言、长音频 |
| 输出要求 | 非空、格式正确、字段存在 | 语义正确、WER、实时率 |
| 路径选择 | 最小、最稳、最容易验证的路径 | 最优路径、生产推荐路径 |

---

## 核心原则

### 1. 最小成功标准

Phase-1 成功仅需证明：
- ✅ 环境可重建（BUILD_ENV）
- ✅ 模型可导入（VALIDATE_IMPORT）
- ✅ 模型可加载（VALIDATE_LOAD）
- ✅ 可跑通推理（VALIDATE_INFER）
- ✅ 输出满足契约（VALIDATE_CONTRACT）

**不要求**：
- ❌ 下游协同（如 diarization + ASR 联合工作）
- ❌ 高阶表现（如低 WER、高实时率）
- ❌ 完整功能覆盖（如所有语言、所有音频格式）

### 2. 路径选择原则

若模型支持多种调用路径，**优先选最小、最稳、最容易验证的路径**。

**示例**:
```python
# WhisperX 支持多种调用方式：
# 1. 纯 ASR (最小)
model.transcribe(audio)

# 2. ASR + Alignment (中等)
model.transcribe(audio)
align_model.align(result, audio)

# 3. ASR + Alignment + Diarization (完整)
model.transcribe(audio)
align_model.align(result, audio)
diarize_model.diarize(audio)
result = assign_word_speakers(diarize_result, result)

# Phase-1 只需验证方式 1
```

### 3. 失败判定标准

以下情况**不构成** Phase-1 失败：
- 下游任务未协同（如 ASR 成功但 diarization 失败）
- 性能未达生产标准（如实时率 < 1x）
- 特定语言/格式不支持（如仅测试英语，中文失败）
- 在同一条 repo-native path 上使用 CPU fallback 成功，而 GPU 仅作为性能/部署要求存在

以下情况**构成** Phase-1 失败：
- import/load/infer/contract 任一阶段报错
- 输出格式不符合 io_contract
- 必要字段缺失或为空
- 为了通过 phase-1 而切换到不同 runtime family 或不同产品形态

---

## 与下游任务的边界

```
Phase-1 (当前)
    ├── VALIDATE_IMPORT: import whisperx ✓
    ├── VALIDATE_LOAD: load_model('small', 'cpu') ✓
    ├── VALIDATE_INFER: model.transcribe(audio) ✓
    └── VALIDATE_CONTRACT: output has 'segments' ✓

Phase-2 (后续)
    ├── Multi-speaker ASR (ASR + Diarization)
    ├── Word-level timestamp accuracy
    ├── Benchmark evaluation (WER, RTF)
    └── Production readiness check
```

**Phase-1 不等待 Phase-2 成功**。

---

## Agent 行为准则

### Builder Agent

- 设计 wrapper 时，优先暴露最小可用接口
- 不要为实现完整功能而引入复杂依赖
- 若多条路径可选，推荐最简单的一条

### Evaluator Agent

- 诊断时区分 "Phase-1 失败" vs "Phase-2 未达标"
- import/load/infer/contract 通过即标记为成功
- 下游协同问题留在 Phase-2 解决

### Harness Controller

- VALIDATE_CONTRACT 仅检查字段存在性和格式
- 不检查语义正确性
- 不检查数值合理性
- 若使用 CPU fallback，必须确认仍是同一条 repo-native minimal callable path

---

## 示例场景

### 场景 1: ASR 成功但 Diarization 失败

**情况**: WhisperX 可以转录，但 speaker diarization 报错（缺少 HuggingFace token）

**判定**: ✅ Phase-1 成功
- import/load/infer/contract 全部通过
- diarization 是下游任务，不在 Phase-1 范围

### 场景 2: 推理成功但输出格式不符

**情况**: 模型返回字符串，但 io_contract 要求 json

**判定**: ❌ Phase-1 失败
- VALIDATE_CONTRACT 未通过
- 必须修复 wrapper 输出格式

### 场景 3: 性能很慢但输出正确

**情况**: 10秒音频推理需要 30 秒（RTF=3x），但输出正确

**判定**: ✅ Phase-1 成功
- 性能问题在 Phase-2 评估
- Phase-1 只验证可行性

### 场景 4: 多语言模型仅英语测试通过

**情况**: 模型支持 10 种语言，但 Phase-1 只用英语 fixture 测试

**判定**: ✅ Phase-1 成功
- 多语言覆盖是 Phase-2 任务
- Phase-1 验证最小可调用路径即可

### 场景 5: GPU 不可用，但 CPU fallback 跑通

**情况**: 模型输入标记 `requires_gpu: true`，但 host driver 不兼容；同一条 repo-native path 在 CPU 上完成 import/load/infer/contract

**判定**: ✅ Phase-1 成功
- CPU fallback 仍验证了最小 callable path
- verdict 必须显式记录 GPU limitation

---

## 文档索引

| 相关主题 | 文档 |
|---------|------|
| Evidence Priority | [evidence_priority.md](./evidence_priority.md) |
| Backend Selection | [backend_selection.md](./backend_selection.md) |
| Minimal Validation | [../contracts/minimal_validation.md](../contracts/minimal_validation.md) |
| Fixture Policy | [../contracts/fixture_policy.md](../contracts/fixture_policy.md) |
