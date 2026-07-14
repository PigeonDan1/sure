# Backend Selection Policy

**Version**: 1.0  
**Scope**: Builder Agent, Harness Controller  
**Purpose**: Define backend selection rules and constraints

---

## 核心原则

### 1. preferred_backend 仅为初始建议

`model.spec.yaml.environment.preferred_backend` 或 `environment_hint` **仅作为初始建议**，不是强制命令。

Backend 选择必须服从：
- Phase-1 runtime target（最小可调用路径验证）
- 基础设施约束（Docker daemon 可用性、网络策略等）
- Evidence Priority（运行时证据优先于配置建议）

`requires_gpu: true` 默认表示兼容性风险和后续能力边界，不自动等于 phase-1 必须在 GPU 上通过。

### 2. 最轻量可行路径优先

如果更轻量的 backend 能满足最小成功目标，**不得强行走更重路径**。

| Backend | 复杂度 | 适用场景 |
|---------|--------|----------|
| uv | 低 | 纯 Python，无系统依赖 |
| pixi/conda | 中 | 需要 conda 包或系统库 |
| docker | 高 | 复杂系统依赖、CUDA 版本隔离 |

### 3. Backend 不可执行时必须记录原因

当首选 backend 不可用时：
- 必须记录具体原因（如 "Docker daemon unreachable"）
- 必须尝试降级到可行 backend
- 必须在 `backend_choice.json` 中记录切换决策

---

## 决策流程

```
用户指定 preferred_backend
    ↓
检查基础设施约束
    ├── 不可用 → 尝试降级 → 记录原因
    ↓
检查 Phase-1 最小需求
    ├── 轻量 backend 可满足 → 使用轻量
    ↓
选择最优 backend
    ↓
记录决策到 backend_choice.json
```

---

## 记录要求

### backend_choice.json 必须包含

```json
{
  "chosen_backend": "uv",
  "reason": "Docker daemon unreachable; uv satisfies phase-1 requirements",
  "evidence": [
    "docker info failed: Cannot connect to daemon",
    "uv available at /usr/bin/uv",
    "phase-1 only requires import/load/infer, no complex system deps"
  ],
  "evidence_conflicts": [
    {
      "field": "preferred_backend",
      "sources": [
        {"value": "docker", "source": "user_input", "priority": 3},
        {"value": "uv", "source": "runtime_preflight", "priority": 1}
      ],
      "chosen": "uv",
      "reason": "Runtime evidence (docker unavailable) overrides user preference"
    }
  ]
}
```

---

## 与 Evidence Priority 的关系

Backend 选择遵循 [Evidence Priority Policy](./evidence_priority.md) 的层级：

1. **Runtime evidence** (priority 1): backend 实际可执行性
2. **Infrastructure constraints** (priority 2): 网络、权限、存储
3. **User preference** (priority 3): preferred_backend 建议
4. **Repo configuration** (priority 4): pyproject.toml 中的依赖暗示

---

## 常见场景

### 场景 1: Docker 不可用

**情况**: User 指定 `preferred_backend: docker`，但 daemon 不可达

**处理**:
1. 记录失败原因
2. 尝试 uv/pixi（根据 repo 配置）
3. 在 backend_choice.json 中标记冲突与解决

### 场景 2: 轻量 backend 足够

**情况**: Model 是简单 Python 包，但 user 指定 `preferred_backend: docker`

**处理**:
1. 评估 phase-1 需求：仅需 pip install + import/load/infer
2. 选择 uv（更轻量、更快）
3. 记录理由："uv satisfies phase-1 requirements, docker is overkill"

### 场景 3: Backend 降级后失败

**情况**: Docker 不可用，降级到 uv，但缺少系统依赖（如 ffmpeg）

**处理**:
1. 记录 uv 失败原因
2. 评估是否可安装系统包
3. 必要时升级到 docker（如果问题解决）或标记为失败

### 场景 4: 标注 requires_gpu，但 phase-1 可走 CPU fallback

**情况**: 输入要求 GPU，但 phase-1 目标仅是最小 callable path，host GPU 不可用或 driver 不兼容

**处理**:
1. 检查 CPU fallback 是否仍是同一条 repo-native path
2. 若是，则保留当前 backend，记录 limitation
3. 在 verdict 中明确：phase-1 passed with CPU fallback，不宣称 GPU readiness

---

## 禁止行为

1. **不得盲从 preferred_backend** - 必须验证可行性
2. **不得过度工程化** - 能用 uv 就不用 docker
3. **不得无记录切换** - 所有 backend 变更必须记录在案
4. **不得忽略基础设施约束** - 网络、存储、权限必须检查
