# 开发指南

这份文档面向扩展 SURE Harness 或新增 skill 的维护者。

## Skill 包结构

```text
sure/skills/<skill-name>/
  sure.skill.json   # skill 清单
  SKILL.md          # agent 操作手册
  hooks/            # 状态机与门禁
  scripts/          # 确定性执行脚本
  schemas/          # artifact 合约
  references/       # 领域参考
  examples/         # 使用示例
```

## 定向检查

迭代时优先跑小范围检查：

```bash
npm run check:sure-hooks
python3 -m py_compile sure/skills/sure_eval/scripts/*.py
python3 -m unittest sure/skills/sure_onboard/scripts/test_runtime_inventory.py
python3 -m unittest sure/skills/sure_eval/scripts/test_protocol_provenance.py
```

当改动影响 setup、skill discovery 或 external engine 检测时，运行 doctor：

```bash
npm run sure:doctor
```

完整检查：

```bash
npm run check
```

`npm run check` 设计为不改写源码。需要 Biome 自动格式化时使用 `npm run
format`，推送前保持 `git diff --check` 通过。

无密钥测试启动脚本（`test.sh`、`pi-test.sh --no-env` 和 `pi-test.ps1 --no-env`）
统一从一个共享文件读取变量名：

```text
scripts/credential-env.txt
```

新增 credential 变量时只把变量名按字母序加入该文件，不要把 secret value 写进脚本或文档。
no-env 启动脚本在 `--no-env` 运行期间会临时移走 agent config 目录中的 `auth.json`，
退出时再恢复。

## SURE 定向测试

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-runbackend.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
```

## Runtime Provenance 生命周期

| 阶段 | 产物 | 规则 |
| --- | --- | --- |
| `/sure_onboard` | `runtime_inventory.json` | 汇总模型级 backend、Python、runtime probe、weights manifest 和小型证据链接。不要链接 checkpoint payload。 |
| `/sure_eval` | `prediction_generation_status.json` | 记录真实 MCP server command、working directory、安全 env snapshot、显式 tool args、protocol resolver 输出和 dataset 生成状态。 |
| `/sure_eval` | `protocol.yaml` | 读取优先级是 generation status、runtime inventory、model config、环境兜底。推理字段必须和评估结果分离。 |
| `/sure_reval` | `prediction_reuse_manifest.json` | 只复制/过滤 predictions，不复用旧 metric artifacts。 |
| `/sure_reval` | `source_inference_provenance.json` | 可用时链接源 protocol/status/runtime inventory；不可用时显式标记 unknown。 |

## 设计边界

| Harness 负责 | Skill 包负责 |
| --- | --- |
| Slash command 发现、run 生命周期、状态持久化。 | 领域 prompt、确定性脚本。 |
| Hook 执行、工具门禁、最终 manifest 校验。 | 状态机、schemas、checkpoints。 |
| 共享 runtime contract。 | 校验规则和修复说明。 |

不要把任务专属 metric、数据集假设或 SURE 业务逻辑塞进通用 harness，除非这个规则真的对所有
skill 都成立。

## 仓库卫生

生成文件不要进入 Git：

```text
.sure/
sure/models/
sure/handoffs/*/artifacts/
sure/skills/sure_eval/results/
```

不要提交 API key、provider token、auth 文件、模型权重、checkpoint、大数据集、prediction
dump、metric result dump、虚拟环境或 cache 目录。

`sure/external/sure-evaluation` 会作为 Git submodule 被跟踪。更新经过验证的 engine 版本时，
只提交 gitlink pointer。
