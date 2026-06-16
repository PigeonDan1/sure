# Skill 规范说明

## 目录结构

```text
skill/
├── fixture/
│   ├── <task_1>/
│   │   ├── <sub-task_1>/
│   │   │   ├── gt.jsonl
│   │   │   ├── sample_001.wav
│   │   │   ├── sample_002.wav
│   │   │   └── ...
│   │   ├── <sub-task_2>/
│   │   │   ├── gt.jsonl
│   │   │   ├── sample_001.wav
│   │   │   ├── sample_002.wav
│   │   │   └── ...
│   │   └── ...
│   └── <task_N>/
│       └── ...
│
├── model.py
├── model.spec.yaml
├── pyproject.toml
├── server.py
└── validate.py
```

---

# 1. fixture/

用于存放评测数据集和样例数据。

## 目录规范

```text
fixture/<task>/<sub-task>/
```

每个子任务目录包含：

- `gt.jsonl`
  - Ground Truth 标注文件
  - 定义输入与标准输出

- `sample_*.wav`
  - 示例音频文件
  - 用于模型测试与验证

## 用途

- 本地验证模型能力
- 回归测试
- Benchmark 评测
- 自动化验收

---

# 2. model.py

模型实现入口。

## 主要职责

### 模型加载

负责：

- 加载权重
- 初始化推理环境
- 初始化资源

例如：

```python
class Model:
    def __init__(self):
        pass
```

### 推理逻辑

实现核心推理能力：

```python
def predict(input):
    ...
```

### 前后处理

包括：

- 数据预处理
- 特征提取
- 结果后处理

### Tool 接口

统一提供：

```python
run_tool(...)
```

或

```python
predict(...)
```

接口供 Server 调用。

---

# 3. model.spec.yaml

模型规范描述文件。

## 主要内容

### 基本信息

```yaml
name:
version:
author:
description:
```

### 输入定义

```yaml
input:
```

定义：

- 参数名称
- 参数类型
- 是否必填

### 输出定义

```yaml
output:
```

定义：

- 返回字段
- 数据结构
- 类型约束

### 参数配置

```yaml
parameters:
```

包括：

- 默认值
- 范围限制
- 枚举值

### 能力描述

说明：

- Skill 能做什么
- 适用场景
- 限制条件

### 版本与依赖约束

```yaml
requirements:
```

---

# 4. pyproject.toml

项目构建与依赖管理配置。

## 主要内容

### 项目信息

```toml
[project]
name = ""
version = ""
```

### 依赖管理

推荐使用：

```toml
uv
```

管理依赖。

例如：

```toml
dependencies = [
  "numpy",
  "torch",
]
```

### Python 版本约束

```toml
requires-python = ">=3.10"
```

### Extras

可选依赖：

```toml
[project.optional-dependencies]
```

### 构建配置

```toml
[build-system]
```

---

# 5. server.py

Skill 服务入口。

支持：

- MCP Server
- CLI 封装

---

## MCP Server（推荐）

对外暴露标准 Tool。

### 功能

#### Tool 注册

```python
@mcp.tool()
```

#### 请求解析

解析：

- 输入参数
- 请求上下文

#### 调用模型

```python
model.run_tool(...)
```

#### 响应处理

返回：

- 标准结构化结果
- 错误信息

#### 日志与异常处理

包括：

- 请求日志
- 推理日志
- 异常捕获

---

## CLI 支持

支持：

```bash
python server.py run_tool ...
```

方便：

- 本地调试
- 自动化测试
- 集成部署

---

# 6. validate.py

评测脚本。

## 核心流程

### 遍历 fixture

自动扫描：

```text
fixture/*
```

### 调用模型推理

通过：

```python
server.run_tool(...)
```

执行测试。

### 读取 GT

读取：

```text
gt.jsonl
```

作为标准答案。

### 结果对比

逐条比较：

- 文本结果
- 分类结果
- 结构化输出

### 指标计算

例如：

- Accuracy
- Precision
- Recall
- F1
- CER/WER（语音任务）

### 评测报告

输出：

```text
report.json
report.md
```

---

# 对外接口

## MCP Server（推荐）

标准能力暴露方式。

特点：

- Tool 注册
- 标准输入输出
- 结构化响应
- 易于接入 Agent

适用于：

- ChatGPT
- MCP Client
- Agent Framework

---

## CLI（可选）

本地执行方式。

示例：

```bash
python server.py run_tool \
  --audio sample.wav
```

特点：

- 开发调试方便
- CI/CD 集成简单
- 支持批量测试

---

# 开发流程

```text
实现 model.py
      ↓
定义 model.spec.yaml
      ↓
配置 pyproject.toml
      ↓
封装 server.py
      ↓
编写 fixture
      ↓
执行 validate.py
      ↓
生成评测报告
      ↓
发布 Skill
```

---

# 最佳实践

1. model.py 仅关注模型逻辑
2. server.py 负责协议适配
3. model.spec.yaml 保持与实现一致
4. fixture 覆盖核心场景与边界场景
5. validate.py 支持自动化评测
6. 所有输出遵循结构化 Schema
7. MCP Server 作为默认对外接口
8. CLI 作为开发调试补充