# SCRIPT_ROUTING_UNIT — Agent 自检 Prompt

在产出 `script_routing.json` 之前，你必须先完成以下合规性自我检查。
这是强制步骤，不可跳过。

## 自检流程

<COMPLIANCE_CHECK_PROTOCOL>

### 步骤 1：脚本路径检查

对 `steps` 数组中的每一个 step，检查：
1. `script` 字段是否以 `scripts/` 开头
2. `script` 字段是否不包含以下禁止前缀：`demo/`、`examples/`、`tests/`、`src/`
3. `script` 指向的文件是否真实存在于磁盘上

### 步骤 2：步骤类型检查

对 `steps` 数组中的每一个 step，检查：
1. `name` 字段是否在 Allowed Step Types 白名单中

Allowed Step Types（白名单，只允许这些值）：
- prepare_dataset
- materialize_templates
- validate_execution_shell
- wait_for_predictions
- validate_predictions
- evaluate_predictions

### 步骤 3：综合判定

只有当步骤 1 和步骤 2 都完全通过（零违规）时，才能：
- 设置 `self_inspection_passed: true`
- 继续产出 `script_routing.json`

如果任何一项检查失败：
- 设置 `self_inspection_passed: false`
- 在 `blocking_issues` 中列出所有违规项
- **禁止产出 `script_routing.json`**
- 输出修正建议

</COMPLIANCE_CHECK_PROTOCOL>

## 输出格式

你必须严格按照以下 JSON Schema 输出自检结果：

```json
{
  "run_id": "<同 run_id>",
  "timestamp": "<ISO8601>",
  "compliance_check": {
    "script_constraints": {
      "all_scripts_in_allowed_dir": true|false,
      "no_demo_scripts": true|false,
      "all_scripts_exist": true|false,
      "checked_scripts": [
        {"step_name": "...", "script_path": "...", "in_allowed_dir": true|false, "is_demo": true|false, "exists": true|false}
      ],
      "violations": []
    },
    "step_type_constraints": {
      "all_names_in_whitelist": true|false,
      "allowed_step_types": ["prepare_dataset", "materialize_templates", "validate_execution_shell", "wait_for_predictions", "validate_predictions", "evaluate_predictions"],
      "checked_steps": [
        {"step_index": 0, "step_name": "...", "in_whitelist": true|false}
      ],
      "violations": []
    },
    "overall_passed": true|false
  },
  "self_inspection_passed": true|false,
  "blocking_issues": [],
  "next_action": "Proceed to output script_routing.json" | "Fix violations before proceeding"
}
```

## 关键约束（不可违反）

<SCRIPT_CONSTRAINTS>
- 只能使用 scripts/ 目录下的确定性脚本
- demo/、examples/、tests/、src/ 下的脚本绝对禁止出现在 script_routing.json 中
- Step name 必须在 Allowed Step Types 白名单中
</SCRIPT_CONSTRAINTS>

<STEP_TYPE_WHITELIST>
prepare_dataset, materialize_templates, validate_execution_shell, wait_for_predictions, validate_predictions, evaluate_predictions
</STEP_TYPE_WHITELIST>

<FORBIDDEN_PATHS>
demo/, examples/, tests/, src/
</FORBIDDEN_PATHS>
