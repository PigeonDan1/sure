# SURE 记忆系统设计稿(v2)

2026-08-18 初稿;同日按复核报告(`2026-08-18-memory-system-design-review.md`,高 6 条、中 13 条)改成本版。接手 /sure_check 工程后的重定向:重点从「提升前 check」改成「一个 clone 里的实例越用越好用」。check 的核验/判定这一半先停,记忆这一半拆出来做主线,挂进 onboard 和 eval 的状态机里。

上一版:`D:\sure\记忆系统\2026-08-11-sure-check-记忆系统-design.md`(师兄 8-12 认可的四层框架)。这一版继承它的候选格式、机械门、人工工具、supersede 不删、触发词路由,放弃它的 verifier / judge / check 报告,抽取的锚点从 check 结论换成 harness 自己每次 run 留下的门禁事实。

参考:`D:\sure\参考\2026-08-11-agent记忆系统同类项目调研.md`(50 项)、`2026-08-13-记忆调研-十大关键文献.md`;FlashRT(arXiv 2607.18171,2026-08);复核时新查的二十余篇见复核报告 §5。FlashRT 不是记忆论文,借的是它两条:结构化中间产物 + 先测量再采信的循环;它的「变体队列」是一次跑内的记忆,我们做的是跨次的延长。

## 0. 相对 v1 的改动(要向师兄说明的)

v1 的治理链是「规则验证 → 人工审批 → 版本化知识库」,人工审批在入库前。v2 改成「机械门 → 自动进暂定层并被下次 run 消费 → 有用命中攒够自动标 confirmed → 人 export 进 git 目录并 commit」,人工审批挪到入库之后,变成随时可否决。这是四层里治理层顺序的改动,不是细节调整。其余变化:

1. bad_case 正文上限 120 词改 200 词,因为沿用库里现有六段式(带证据路径和验证命令),120 词装不下;「策略级、不写只对单一模型成立的数值」这条要求保留,写进 EXTRACTION.md;
2. 「一条一 commit 可单独回退」不再由工具保证,commit 粒度在人手上;`cli export` / `confirm` / `reject` 每次只处理一条并打印建议的单条 git 命令,人照着做就还是一条一 commit;
3. 收编时不再自动跑 onboard 的 `check_experience_assets.py`(它查的是 playbook 引用完整性,与新条目无关);`cli reject` 遇到被 playbook 引用的老条目先警告;
4. references 的写入不再天然串行(自动 promote 出现),因此 promote 不直写 references(见 §8.2);
5. `similar` 从必填改成「同格冲突时必填」,其余情况可选。

## 1. 目标与边界

要做到的:

- onboard / eval 同一个坑不踩两次:上次在这类模型、这个分区、这一步的失败原因和解法,下次同一步被打回时 agent 一定看到;
- 环境事实越攒越全:分区名、CUDA、缓存布局、数据集怪癖这些不用每次重新摸;
- 库自己会长会退:候选自动落成暂定条目,用得上就自动标 confirmed,被打脸就标出来等人裁,过期就 supersede;
- 跨技能:一个技能跑出来的经验能给另一个技能用。

不做的:check 的核验/判定,多实例同步,按角色的记忆,向量库/嵌入,自动改 playbook / SKILL.md / COMMON.md,统计显著性门,记忆管理 agent(第二步),recipe 类成功做法条目(第二步),feed / reval 接入(第二步,照抄),归一化掩码与正则触发词(见 §7.2),带/不带记忆对照跑。

一条总原则(复核 H1):**抽取和注入都是主流程的副产品,任何情况下不得阻断技能自己的收尾。**

四层框架照旧,只是每层的载体变了:

| 层 | v1(check) | v2(本稿) |
|---|---|---|
| 证据层 | check 时算的哈希快照 | harness 每次 run 自己留下的 `events.jsonl` / `state.json` / `artifacts/`,压成 `run_digest.json` |
| 核验层 | verifier.py 11 项 | 各技能现有门禁的 pass / fail / repair 文本(不另写 verifier) |
| 学习层 | /sure_check 的 extract 单元 | onboard / eval 各自的 `extract_lessons` 单元 |
| 治理层 | adopt_memory.py 人批 | 分层落库 + 有用命中升降级 + cli 人工工具 |

## 2. 钉死的决定

问答式定下来的十五条,复核没有翻案,后面各节按这些展开:

1. 越用越好用先做「同坑不踩两次」,再做「环境事实」,多人多实例共享不做;
2. 抽取做成技能状态机里的一个 unit,不加新命令;
3. 成功、失败、半途的 run 都抽;抽取门连败两次放行,记 `extraction: failed`;
4. 第一版覆盖 onboard + eval;
5. 条目两种:bad_case、fact;`type` 字段留着给 recipe;
6. 过门候选自动落成 provisional 层;人可 confirm / reject / supersede;
7. 自动转正按「有用命中」算,不按被读次数;K = 2;
8. 多 agent 协同 = 跨技能路由(target_skill 可以不是产出技能,加共读 fact 库);记忆管理 agent 预留;
9. 消费两条路:prompt 级路由 + hook 注入(门禁打回时、pre_start 时);命中只从 hook 记录算;
10. provisional / candidates / usage / decisions 在 `sure/memory/`(gitignore);confirmed 才进 `references/memory/`(git 跟踪,人 commit);
11. 有用 / 打脸只在同一次 run 内、同一个 unit 上判;一次打脸就不自动转正;
12. digest 带修复窗口(打回 → 试了什么命令 → 过了没);触发词必须能在失败文本或证据文件里逐字找到;
13. bad_case 正文沿用库里现有六段式,≤200 词;fact 一句现状 + scope + checked_at,≤60 词;
14. 抽取 unit 放业务结论之后、打包收尾之前(onboard 在 verdict 后,eval 在 assessment 后);
15. 老分支 check 那半不进新分支;新分支从主线 1626e01 开,只挑记忆那半的代码过来改。

复核后在决定框架内定的三处(用户可翻):结算点取「unit 终局」(§8.1);自动转正不直写 references,走 outbox + `cli export`(§8.2);索引 `index.md` 用 bullet 列表,溯源头保留五行不换 YAML frontmatter(§6.4)。

## 3. 一句话架构

每次 onboard / eval 跑到业务结论出来,进 `extract_lessons`:hook 先把这次 run 的事实压成 `run_digest.json`,agent 读 digest 写 0..5 条候选,过机械门;收尾时候选自动落进 `sure/memory/provisional/`;以后任何一次 run 在同一步被门禁打回,hook 拿打回原文加日志尾匹配触发词,把最相关的两条塞进 repair 一起还给 agent,并记一笔;那个 unit 最终过了、且 agent 确实读过这条,记「有用」;终局仍失败且末次打回还命中它,记「打脸」;有用攒够两次(来自两次不同的 run)自动标 confirmed 进 outbox,人 `cli export` 搬进仓库里的 `references/memory/` 再 commit。

```
一次 run(onboard / eval)
  … → verdict / assessment → extract_lessons → finalize / run_report → finish
                                  │                                     │
             hook: build_run_digest.py                        pre_finish: 非 success 也要抽取声明(两次打回后放行)
             agent: 读 digest,写 candidates                    post_finish: publish → provisional
             gate:  check_memory_extraction.py                              usage 重放 → promote
                                                                             │
        sure/memory/                                                         ▼
        ├─ provisional/<target_skill>/<slug>/{entry.md, proposal.json}   ◀───┘
        ├─ outbox/<target_skill>/<slug>/          (自动或人工标 confirmed 的,等人 export)
        ├─ meta/<target_skill>/<slug>.json        (所有条目的状态与计数;python 单写者)
        ├─ usage/<run_id>.jsonl                   (每 run 一个文件,注入行 + 结算行)
        ├─ decisions.jsonl                        (publish / confirm / reject / supersede / promote / demote,append-only)
        ├─ digests/<run_id>.json                  (每 run 的 digest 拷贝,≤20KB)
        ├─ rejected/<target_skill>/<slug>/
        └─ index.json + index.md                  (confirmed + provisional 合并索引,有预算)

        sure/skills/<skill>/references/memory/bad_cases/<slug>.md   ◀── cli export(人 commit)
        sure/skills/_shared/memory/facts/<slug>.md                  ◀── cli export(人 commit)

下一次 run:
  pre_start        → 匹配 fact(按 scope)→ artifacts/memory_context.json(有预算)
  post_tool_result → 门禁打回 → 匹配条目(原始 repair + 日志尾)→ 拼进 repair(≤2 条)→ usage 注入行
  同 unit 之后的门禁结果 → useful(激活)/ pending → disputed / 中性 → usage 结算行
```

## 4. 抽取 unit

### 4.1 位置与定义

| 技能 | 插在哪 | 之后 |
|---|---|---|
| sure_onboard | `verdict` 之后 | `finalize_model_bundle`(仍是 LAST_UNIT) |
| sure_eval | `assessment` 之后 | `run_report`(仍是 LAST_UNIT) |

`LAST_UNIT` 不变,两个技能 pre_finish 里拿 `LAST_UNIT.produces` 当收尾证据的逻辑一字不动。成功路径必然经过 `extract_lessons`;失败路径见 4.5。

unit 定义:`id: "extract_lessons"`,`kind: "gate"`,`produces: "extraction_declaration.json"`,`schemaRef: "extraction_declaration.schema.json"`(每个技能 `schemas/` 下各放一份拷贝,源在共享库,vitest 断言字节相同),`gateScript: "check_memory_extraction.py"`,`gateInputs: ["candidates", "memory_evidence"]`(新字段,见下),`helperScripts: ["build_run_digest.py"]`(eval 的 Unit 类型要补 helperScripts 并让 preToolCall 认它,与 onboard 一致)。

`gateInputs`(复核 H4):Unit 加可选 `gateInputs?: string[]`,相对 `artifacts/` 的目录或文件。两个技能 hooks 里现有的 `unchangedFailedArtifact` / `failOrRetry` 只哈希 produces 那一个文件,内容没变就不重跑门、不消耗重试;抽取门拒的多半是 `candidates/**/proposal.json` 或 `.md`,agent 改了候选门也不重跑。改法:hooks 抽一个 `gateDigest(ctx, unit)`,把 produces 加 gateInputs 下所有文件按相对路径排序、路径加内容一起进 sha256,两处改用它;其他 unit gateInputs 为空,行为不变。

插 unit 后要一起改的写死处:两个技能 vitest 文件里的单元列表和 12 / 22 计数、两份 SKILL.md 单元表、handbook 与 company_model_onboarding 里的单元数。

### 4.2 进入 unit 时 hook 做的事

`post_tool_result` 里 `advance()` 把状态机推进到 `extract_lessons` 的那一刻,hook 调 `scripts/build_run_digest.py --run-dir <run_dir> --cutoff <events 行数> --mark-passed <刚过的 unit id>` 写 `artifacts/run_digest.json`,算 sha256,连同 cutoff 一起写进 checkpoint(见下)。`--mark-passed` 是因为 hook 数 cutoff 那一刻,「verdict / assessment 已通过」的 state 事件还没写进 events(extension 先追加 tool_result 再调钩子),不传的话 digest 会把刚过的 unit 记成 current。

checkpoint 扩字段(复核 H2):`CheckpointData` 现在是四键白名单(currentUnit / completedUnits / retries / failedArtifactDigests),readCheckpoint 重组、advance / bumpRetry 重新构造,任何新键写进去下一次就丢。两份 `checkpoints.ts`(onboard、eval)加可选 `memory?: { digestCutoff?: number; digestSha256?: string; digestPassed?: string; finishAttempts?: number; extractionStatus?: "failed"; injected?: Record<string, string[]> }`,readCheckpoint 按类型读回,advance / bumpRetry 用展开后再覆盖四键,两个 preFinish 成功收尾手拼四字段的地方同样带上。不用单独的 `memory_state.json`,状态只在 checkpoint 一处。

agent 看不见 state 消息(state_patch 只更新 TUI),所以 hook 不「告诉」agent 什么;EXTRACTION.md 让 agent 进 unit 后主动读 `artifacts/run_digest.json`,digest 只有 `{schema, error}` 时 agent 只能声明 `no_new_lessons: true` 并引用该 error,门放行这一种。

agent 不调 `build_run_digest.py`(复核 H3):门只认 hook 建的那份,重跑会让文件 sha 和 checkpoint 对不上。SKILL.md 明写「digest 只由 hook 建」;要看预览可以 `--out artifacts/run_digest.preview.json`,门不认它。

### 4.3 run_digest.json 装什么

```
{ schema: "sure.memory.run_digest.v1",
  run: { run_id, skill, args(已剥 output_dir), target: {kind, id}, status_so_far, cutoff,
         memory_usage: [ {entry_id, unit, attempt, outcome: "useful"|"disputed"|"open"} ] },
  units: [ { id, outcome: "passed"|"failed"|"skipped"|"current", attempts,
             repairs: [ {attempt, text} ]              (原始 repair,头 200 + 尾 400 字符,已剥掉 Memory 块),
             fix_window: [ {tool, command} ]           (≤10 条,每条 ≤300 字符;仅「先挂后过」的 unit;非 bash 工具只记 path),
             last_commands: [...]                      (≤10 条;仅终局失败的 unit),
             log_tail: {path, lines}                   (≤30 行,每行 ≤300 字符;仅 log_paths.json 登记了日志的失败 unit) } ],
  tool_errors: N,
  prior_runs: [ {run_id, status, failed_unit, finished_at, last_repair(≤300 字符), candidates: [slug...]} ]
             (同技能同目标,≤5 次,新的在前;倒序扫 .sure/runs/ 读 run.json 凑够即停),
  memory_index_snapshot: [ {id, type, status, target_skill, component, cause, trigger[], useful, disputed} ],
  units_registry: {skill: [unit ids]} }
```

- 目标(`target`)优先从产物读:onboard 的 `artifacts/model_input_resolved.json`,eval 的 `artifacts/eval_input_resolved.json`;读不到才从 args 兜底。不解析 YAML。
- `run.args` 按 hooks parseArgs 的切法剥掉 `output_dir=` / `--output_dir <v>`(照 `output-dir.ts` 逻辑用 python 重写并单测),output_dir 不进 digest、不进条目。
- units 只从 events ≤ cutoff 推,不读 state.json 的 retries;`--mark-passed` 的 unit 按 passed 处理并按先挂后过规则建 fix_window。
- repairs 用门禁脚本的原始文本:优先取 `tool_result_repair` 事件里 `state_patch.diagnostics[].repair`,取不到就按 config.json 里的常量前缀 `Memory (advisory` 把注入块剥掉。
- log_tail 从文件尾 seek ≤64KB,按 `\n` 和 `\r` 切行,取末 30 行;`log_paths.json` 允许 `{run_dir}` 与 `{product_dir}` 占位,给 onboard 的 build_env / validate_* 和 eval 的 smoke_test / execution 登记路径。
- 20KB 封顶,裁剪「先缩后删、核心段永不整删」,顺序写进 config.json:memory_index_snapshot 只留 id/status/cell → units_registry 只留当前技能 → prior_runs 缩到 2 条去 candidates → log_tail 30 行缩 10 行 → repairs 每条 600 缩 300 → fix_window 10 条缩 5 条;缩到底还超就接受超封顶。
- `memory_usage` 从 `sure/memory/usage/<run_id>.jsonl` 读本 run 的注入与结算行,让抽取 unit 知道本 run 哪条被注入、被打脸(复核 M10)。

「先挂后过」的 fix_window 是这套东西最值钱的材料:打回文本 → 试了什么 → 过了,和 FlashRT 的「假设 → 实现 → 测量」同构。events 里存的是工具调用的输入(命令原文),不存输出,所以 fix_window 只能从命令差异推,EXTRACTION.md 明说。

### 4.4 agent 产出

`artifacts/extraction_declaration.json`:

```
{ schema: "sure.memory.extraction.v2",
  no_new_lessons: bool, no_lessons_reason: string|null,
  covered_by: [entry ids], candidates: [candidate dir ids],
  infra_noise: bool, infra_evidence: [path or path:line] }
```

每个候选一个目录 `artifacts/candidates/<nn>-<slug>/{proposal.json, proposal.md}`,格式见 §5;fact 的证据文件放 `artifacts/memory_evidence/`。每次 ≤5 条候选。候选、证据、声明一律用 write 工具写:bash heredoc 里出现 `scripts/xxx.py` 字样会被脚本白名单当越权拒掉整条命令;bash 只用来跑观察命令并 tee。

抽取取材优先级(写进 EXTRACTION.md):本 run 被注入且被打脸的条目 → 提 modify / supersede 指向它,claims 引用它被打脸的那次 gate_repair;其次先挂后过的 fix_window;再次终局失败且日志能定位到具体位置的;再次同目标历次 run 的对比;成功侧的做法确认最后(recipe 第二步再开)。成功 run 里 `no_new_lessons: true` 是常态,不硬凑。

### 4.5 门与失败路径

`check_memory_extraction.py`(每技能一个薄封装,逻辑在 `sure/runtime/memory/proposals.py`)在 `post_tool_result` 跑;规则见 §5.3。

成功路径连败(复核 H1):extract_lessons 不走通用「耗尽即 FAILED」。门连败到上限(用决定 3 的两次,数值进 config.json;onboard 的 `max_retries=` 参数对它只准调大)时 hook 自动 `advance` 到下一 unit,checkpoint 记 `memory.extractionStatus = "failed"`,diagnostics 加 `extraction: failed (<原因>)`,post_finish 见 failed 就跳过 publish。两个技能耗尽提示语里「finish with status failed」这类话对 extract_lessons 去掉。EXTRACTION.md 补一句:候选过不了门可以改成 `no_new_lessons: true` 并写明原因,不算绕门。

非 success 收尾(failed / incomplete):`pre_finish` 加一项,在现有终态证据检查通过之后,要求 `artifacts/extraction_declaration.json` 存在且过门:

- 第一次不满足:hook 先建 digest(cutoff = 当前,同样记 sha 进 checkpoint),打回,repair 写清「先按 EXTRACTION.md 产出 extraction_declaration.json 再收尾;只需生成声明,不要结束回合」,checkpoint 记 `memory.finishAttempts = 1`;
- 第二次不满足:再打回,`= 2`;
- 第三次:放行,checkpoint 记 `extractionStatus = "failed"`,diagnostics 加一条,不再要求。

「失败也抽」的前提(复核 M4):run 得走到 `sure_finish`。extension 在 pre_finish 之前先校 `sure.skill.json` 里 `required: true` 的产物,onboard 至少要走到 package_gate 之后、eval 至少要走到 execution_readiness / smoke 之后才有这些文件;更早挂死的 run 多以 agent_end / session_shutdown 收场,只跑 on_error,不抽候选。补救两条:`on_error` 也调一次 build_run_digest.py 写 `artifacts/run_digest.json`(不发布不写候选);prior_runs 每条带 `last_repair`,下一次同目标的 run 至少看得见上次挂在哪、门禁说了什么。headless 下 pre_finish 的两次打回叠加 harness 的 3 次催办上限,e2e 用 print 模式实测一次。

## 5. 候选与条目

### 5.1 bad_case 正文(六段式)

```
# <标题>

## Trigger        触发词或症状
## Affected Step  哪个技能的哪个 unit
## Minimum Evidence  最少要看的证据(路径,或 路径:行)
## Known Mitigation  怎么修
## Verification   一条能跑的验证命令,或一个能查的文件
## Example Artifacts (可选)
```

必有 Trigger / Affected Step / Minimum Evidence / Known Mitigation / Verification,Example Artifacts 可选,门 1 按这份清单硬判;正文 ≤200 词,标题、代码块不计;策略级,不写只对单一模型成立的数值(EXTRACTION.md);语言英文。库里老 17 条的段名不统一(Fix Pattern / Required Fix / Known Mitigation(s) 混用、8 条超 200 词),老条目不回改、不过门。溯源头由落库脚本生成,agent 不写;正文里出现 `Trigger:` / `Cell:` / `Source:` / `Added:` / `Status:` / `Superseded-by:` 开头的行直接拒。

落库后的文件 = 头五行溯源 + 上面的正文:

```
Trigger: <trigger[0]>; <trigger[1]>; …          (触发词本身不许含 `;`)
Cell: <target_skill>/<component> x <cause>
Source: <run_id> → <target>
Added: <date>
Status: provisional | confirmed
```

### 5.2 fact 正文

```
# <一句现状>

Scope: cluster | model_family:<名> | dataset:<名>
Checked-at: <date>
Evidence: <路径 或 路径:行>

<≤60 词补充,可空>
```

fact 的证据必须是落了盘的文件。agent 想记一条在上下文里看到的事实(比如 `vc info` 的输出),要把观察命令重跑一遍把输出 tee 进 `artifacts/memory_evidence/<n>.txt` 再引用;凭记忆写的没有证据文件,门拒。

### 5.3 proposal.json 与门

```
{ schema: "sure.memory.proposal.v2",
  type: "bad_case" | "fact",
  op: "add" | "modify" | "supersede",
  target_skill: "sure_onboard" | "sure_eval" | "sure_feed" | "sure_reval" | "_shared",
  target_entry: null | <entry id>,
  applies_to: [skills],                 // bad_case 必须等于 [target_skill];fact 默认全部
  cell: { component: <unit id 或 "_">, cause: <config.json 里的 cause 枚举> },
  trigger: [string],                    // ≤5 条
  causal: bool,
  evidence: [path 或 path:line],
  claims: [ { kind: "unit_result" | "gate_repair", unit, attempt, status } ],
  source: { run_id, skill, target, digest_sha256 },
  similar: { entry, difference } | null, // 同格冲突时必填
  scope: <fact 用>, checked_at: <fact 用> }
```

`cell.component` 必须是 `target_skill` 在 `units.json` 里的 unit id(fact 用 `_`;sure_reval 无状态机,只能是 `_`);`cell.cause` 枚举写在 config.json:eval `failure_taxonomy.md` 八类 + `infra` + 少量 harness 级类(job_submission、resource_limit、data_layout、result_layout、metric_bypass),fact 用 `n.a.`,不开自由标签。

门(全部脚本硬判,手写校验,不依赖 jsonschema):

1. schema、枚举、必填字段齐;正文按 type 的段落清单和词数上限;正文无溯源头行;进头部或 README 路由行的字段(trigger、source)无 `|`、无 `;`、无不可打印字符;
2. `evidence` 每条真实存在,相对路径按顺序解析:(a) run 根 `.sure/runs/<run_id>/`(vc_logs、local_logs 也算),(b) 目标目录:onboard 是 `sure/models/<model>/`,eval 是 `eval_input_resolved.json` 里 `runtime.run_dir`(门自己读,agent 不用知道 output_dir);不许绝对路径、不许 `..`;`path:line` 的行号在范围内;run artifacts 用 resolve 后 startswith 判收容,集群模型目录按词法收容不 resolve(保留软链);
3. `claims` 每条能在 digest 里找到对应的 unit / attempt / status;
4. 触发词纪律(复核 H6):每条 trigger 去首尾空白后 ≥ `trigger_min_chars`(config.json,先 8);不等于停用词表里的词(error / failed / failure / exception / warning / missing / invalid / cuda / timeout / not found);剥掉模板短语表(从 validate.ts / index.ts 固定文案手列,vitest 断言每条短语仍在源码里)后剩余非空白字符仍 ≥ 下限;bad_case 至少一个 trigger 同时满足「不含本 run 的 run_id、target.id、`.sure/runs/`,去标点后不是纯数字或纯十六进制,不匹配 ISO 时间戳」;bad_case 至少一个 trigger 逐字(不分大小写)出自 digest 的 repairs 或 log_tail(只出自证据文件的触发词允许存在,但只服务 prompt 级路由,不参与 hook 命中);不许以 `re:` 开头;fact 的 trigger 允许为空,非空时须逐字出现在它引用的证据文件里。拒的 repair 写清「至少一条 trigger 要是下次同样失败还会原样出现的字串」;
5. `infra_noise: true` 时候选的 cause 只许 `infra`,且 `infra_evidence` 非空可解析;
6. `causal: true` 时 evidence 里至少一条 `path:line`;
7. 判重与格子(复核 M6):占位只算 status=confirmed 且未被 supersede 的条目;格子里只有 provisional / disputed 时允许 op=add,但 `similar.entry` 必须指向占位者且 `difference` 非空;`similar.entry` 非空时一律校验它在索引里;触发词集合与库里某条完全相同的 add 拒;候选 trigger 集合与库里同 target_skill 同 component 某条是子集或 Jaccard ≥ 0.5 时不拒但要求 similar 指向;`difflib.SequenceMatcher` ratio ≥ 0.9 的近似句同样要求 similar;同批候选之间两两同判;老条目 cell 为 null 不占位,但候选任一 trigger 与某老条目片段相同时必须在 similar.entry 或 covered_by 里点名;
8. modify / supersede 的 `target_entry` 必须存在;bad_case 的 `applies_to` 必须等于 `[target_skill]`(跨技能一律通过 target_skill 表达,复核 M7);
9. `source.run_id` 是本 run 的;`source.digest_sha256 == checkpoint.memory.digestSha256 == 磁盘 run_digest.json 现算 sha`,三方相等,门不重建 digest(复核 H3);
10. 声明一致:`no_new_lessons: true` ⇒ candidates 空且 reason 非空;`no_new_lessons: false` ⇒ candidates 非空;`candidates` 里的 id 都是 `artifacts/candidates/` 下的单段目录名,磁盘上没有未声明的候选目录;≤5 条。

## 6. 落库分层与目录

### 6.1 目录与权限

```
sure/memory/                                  # .gitignore;实例数据
├─ provisional/<target_skill>/<slug>/         # entry.md(正文 + 溯源头)+ proposal.json(原提案)
├─ outbox/<target_skill>/<slug>/              # 已标 confirmed、等人 export 的条目(entry.md 拷贝)
├─ meta/<target_skill>/<slug>.json            # 所有条目的状态与计数(含 confirmed / legacy),python 单写者
├─ usage/<run_id>.jsonl                       # 每 run 一个文件:注入行 + 结算行,单写者
├─ decisions.jsonl                            # publish / confirm / reject / supersede / promote / demote,只由 python 写,append 前 flock
├─ digests/<run_id>.json                      # 每 run 的 digest 拷贝
├─ rejected/<target_skill>/<slug>/            # 拒收留档
├─ .lock                                      # python 写 meta / index / decisions 时 flock
├─ index.json                                 # 合并索引(机器)
└─ index.md                                   # 合并索引(agent 读,有预算)

sure/skills/<skill>/references/memory/bad_cases/<slug>.md   # confirmed bad_case,git 跟踪
sure/skills/<skill>/references/memory/bad_cases/README.md   # 路由表,cli export 时幂等对账
sure/skills/_shared/memory/facts/<slug>.md                  # confirmed fact,git 跟踪
sure/skills/_shared/memory/facts/README.md                  # fact 索引
```

`entry_id` = `<target_skill>/<slug>`。`_shared/` 不带 `sure.skill.json`,发现逻辑会跳过它(已核对 manifest.ts)。memory 目录根从 `repoRootForPackage(ctx.packageDir)` 推,不用 cwd。

权限(复核 M1):生产检出是一个 clone 很多人用。`sure/memory/` 及其下所有目录、文件按检出的组协作权限建:把 bootstrap.py 的 `_apply_acl` / `_make_group_writable` 抄进 `sure/runtime/memory/paths.py`,第一次建根目录跑一次(setfacl 默认 ACL 优先,退路 setgid + g+rwx),之后每次新建子目录、临时文件、jsonl 都经同一写入口 chmod g+rw / g+rwx;TS 侧 usage 追加与建目录用 `mkdirSync` + `chmodSync(0o2775)`、`appendFileSync` + `chmodSync(0o664)`。写失败时 diagnostics 明写是权限问题、目录属主是谁、让维护人跑 `cli.py fix-perms`。手册方式 A 加一句「记忆目录是检出里所有人共用的」。`.sure/runs/` 和 bootstrap 锁有同一类问题,不属本稿,但集群 e2e 前得先让生产检出能多人跑。

并发(复核 M3):同节点 append 由内核串行化,跨节点 NFS 不保证,所以 usage 每 run 一个文件、单写者;decisions 单文件但只有 python 写且 append 前 flock;所有读 jsonl 的地方跳过解析失败的行并记 diagnostics;计数从 usage 重算,不原地增减;publish 建 slug 目录用 `os.mkdir` 抓 `FileExistsError` 再加 `-2`;临时文件 `tempfile.mkstemp(dir=目标目录)`,TS 用 pid + 时间戳后缀;usage 单行 < 4096 字节;写文件一律先写临时文件再 rename(主线 `run_reval.py` 已有带 fsync 的 `_atomic_write`,直接用)。

### 6.2 publish

`post_finish`(任何 status;extractionStatus=failed 时跳过)调 `scripts/publish_memory.py --run-dir --repo-root`:读 `extraction_declaration.json`,把每个候选写成 `provisional/<target_skill>/<slug>/`,slug 从 proposal.md 的 H1 生成(全中文 H1 退化成 `<run_id 后 8 位>-<nn>`),撞名加 `-2`;写 meta;把 `artifacts/run_digest.json` 拷成 `digests/<run_id>.json`;proposal.json 每条 evidence 补记文件 sha256(只记哈希不拷大文件);机器推 `meta.derived_from` = 本 run usage 注入行里 unit 等于候选 claims 里某 unit 的条目 id(agent 不填);从 digest 推 `meta.fix_exercised`(候选 cell.component 指向的 unit 在源 run 里 outcome=passed 且 attempts>1);`decisions.jsonl` 追加 `publish` 行;重建索引。幂等:同一 run 已 publish 过就跳过。publish 走单独的 spawnSync,60 秒超时,失败只记 diagnostics。

op=modify / supersede 的候选也落 provisional,照常进索引、注入、计数(给 cli confirm 当依据),但不改目标条目、不自动转正,只有人 confirm 才生效;目标条目被 reject 后指向它的候选标 orphan,cli list 可见。

### 6.3 meta

```
{ entry_id, type, status: "provisional"|"confirmed"|"disputed"|"superseded"|"rejected",
  target_skill, applies_to, component, cause, trigger, scope,
  injections: N, useful_activated: N, useful_unattributed: N, useful_runs: [run_id], disputed: N,
  last_hit: date|null,
  created: {run_id, date}, confirmed: {by: "auto"|"human", date}|null, exported: date|null,
  derived_from: [entry_id], fix_exercised: bool, evidence_sha256: {path: sha},
  superseded_by: entry_id|null, superseded_at: date|null, checked_at: date|null (fact) }
```

计数只在 meta 里,不写进 git 跟踪的条目文件;meta 由 python 从 usage 重放算出(promote.py 在 post_finish、cli 的 stats / rebuild-index),TS 不写 meta。confirmed 与 legacy 条目也有 meta(计数用)。

### 6.4 索引与老条目

`index.json`(带 `schema: "sure.memory.index.v1"`)和 `index.md` 由 publish / promote / cli 重建;onboard / eval 的 `pre_start` 在 resolveHarnessPython 成功之后调 `index.py --check`:索引里记着来源文件清单的内容哈希,和当前清单不一致就重建(不看 mtime);TS 不算哈希、不解析条目文件,只读 index.json,遇未知 schema 按索引损坏记 diagnostics 并跳过注入。fresh clone 上第一次 pre_start 就把索引建出来,手册说明第一次会多几秒。

索引器收录规则(复核 M10):references 下的 confirmed 与 legacy 无条件收;provisional 只收 meta 存在、meta 记的 entry.md sha256 与文件一致、decisions 有 publish 行的条目;不依赖 `.sure/runs/` 存在,手放的文件进不了索引;references 里已有同 entry_id 时以 references 为准,outbox / provisional 那份不再参与匹配。

`index.md`(复核 M11)是 bullet 列表不是表格(README 路由表仍是表格,所以门里对 `|` 的禁令保留),一行一条:`- [status] <entry_id> — <H1 标题> — triggers: a; b`;排序 confirmed 在前、provisional 新到旧、disputed 最后;superseded / rejected 不进;有预算(config.json,先 200 行 / 25KB),超限只保留最新若干 provisional 行并在末尾写「已省略 N 条,`cli list --status provisional` 查看」;rebuild-index / stats 打印行数字节数与上限;只提示不报错。fact 按 scope 有 `stale_after_days`,超龄行带 `[stale]`,只标不删。

老 17 条(复核 M8):不再从 README 路由表整句取触发词。实施时由我们一次性给 17 个文件补五行头(Trigger 从各文件 `## Trigger` 段反引号里挑能在真实报错里逐字出现的短串,如 `no kernel image is available`、`partition not found`、`Can't initialize NVML`、`tp_plan='auto'`,过滤掉 `.`、`!`、纯文件名;Cell 用 Affected Step 对应的 onboard unit id,对不上的用 `_`;没有报错串的如 asr_metric_bypass 留空只走 prompt 级;`Source: legacy`;`Status: confirmed`),git 跟踪文件人 commit。补头之前索引器把没有 `Trigger:` 头的老条目 trigger 记空、component `_`、只进 index.md 标 `[legacy]`,不参与 hook 匹配,stats 显示「无 trigger,不参与注入」。

## 7. 消费

### 7.1 prompt 级

- onboard:`references/memory/ROUTING.md` 的 bad-case 那行改成先看 `sure/memory/index.md`(合并索引,全量,不按 applies_to 过滤),再读命中的文件;SKILL.md 在 `context_selection` 处加一句读 `artifacts/memory_context.json`,记录放 `selected_references.memory`(schema 已允许),ROUTING 里的示例形状同步改;
- eval:新建 `references/memory/ROUTING.md` + `bad_cases/README.md`(空路由表),SKILL.md 在 `task_classification` 处加一句读 `artifacts/memory_context.json`;不往 `task_classification.json` 里加字段(schema `additionalProperties:false`)。

agent 自报读了什么只做展示,不参与升级。

### 7.2 hook 注入

`sure/runtime/memory/match.ts` 提供 `matchMemory({skill, unit, text, args})`,两个技能的 hooks 共用,只读 index.json。

匹配文本:门禁打回时 = 门禁脚本原始 repair(拼 Memory 块之前)+ 该 unit 在 `log_paths.json` 登记的日志尾 30 行(与 digest 同窗口,读不到就只用 repair);pre_start 时 = args(已剥 output_dir)+ target id。

匹配谓词只有一处规范:trigger 与文本各自 `lower()` 后原样子串比较,不折空白、不做其他归一化、不掩码、无正则(v1 删 `re:`)。python 门规则 4、match.ts、§8.1 打脸判定三处引用同一规范;`sure/runtime/memory/fixtures/match_vectors.json` 让 pytest 与 vitest 跑同一组向量;python 单测落 golden index.json 给 vitest 解析。

过滤(复核 M7):bad_case 要求 `target_skill == 当前技能 且 component == 当前 unit`;fact 按 scope 机械匹配(cluster 一律命中,model_family / dataset 名归一化后是 target id 或 datasets 参数的子串),trigger 只作补充,再按 `applies_to` 含当前技能或 `_shared` 过滤;superseded / rejected 不参与。

排序:状态三层 confirmed > provisional > disputed;同层按「命中触发词的最长长度」降序(不是命中条数),再 `useful_activated - disputed`,再新旧;disputed 继续参与但排最后一层并带 `[disputed]` 标签;modify / supersede 候选与其 target_entry 同时命中时合并成一行只占一个名额(目标行后挂「pending revision: <候选路径>」),usage 记两个 id;某条的 `similar.entry` 已在列表里就跳过它。

预算与去重(复核 H5、H6):每次 ≤2 条;单条注入行封顶 `inject_max_chars_per_entry`(先 300),两行拼完超 1500 就整条丢第二条,不截半句(抄 TencentDB `applyRecallBudget`,MIT,约 80 行);注入行的「一句话」定死取条目 H1 标题,不取正文任何一段(命令永远不进 repair);同一 run 同一 unit 已注入过的条目不再重复注入(查 checkpoint `memory.injected[unit]`),剔完没新条目就不加 Memory 段(可留一句「entries shown at attempt N still apply: <ids>」,不记 usage、不结算)。注入块固定首行 `Memory (advisory, agent-written, not human-reviewed; verify against evidence before relying):` 放 config.json 当常量,match.ts 拼、digest.py 剥共用;块会随 repair 进 lastRepair / events / result.json 的 error,接受(不动 extension.ts),手册「记忆」一节提 output_dir 下多出的产物。

usage 注入行:`{kind: "inject", run_id, skill, unit, attempt, events_cutoff, entries: [{entry_id, shared: bool}], at}`;pre_start 的记 `kind: "pre_start"`,不结算。`memory_context.json` 有预算:confirmed 全收、provisional ≤ N 条(先 10),按 confirmed、checked_at 排序。

## 8. 自进化

### 8.1 有用与打脸(复核 H5)

先定义「门禁结果」:只指该 unit 被 `advance`(过了),或该 unit 的 `retries` 又加一(`bumpRetry` 真消耗一次重试,含重试耗尽的 failure)。`unchangedFailedArtifact`(产物没变)、`event.isError`、produces 未写这三条 ok:true 路径既不注入也不结算;agent 只是 cat 一下日志不会触发任何结算。

结算只在同一次 run、同一个 unit 上,每条目每 unit 每 run 只有一笔注入、一次结算,取 unit 终局:

| 之后发生 | 记什么 |
|---|---|
| 该 unit 通过了(advance),且注入行到通过之间 events 里有一条 tool_call 的 input 含该条目文件路径(read 的 path 或 bash 命令原文,子串即可) | `useful_activated +1`(计入转正) |
| 该 unit 通过了,但没读过该条目 | `useful_unattributed +1`(只进 stats) |
| 又打回(真消耗重试)且新的原始 repair + 日志尾命中该条目触发词 | 先记 pending;同 run 同 unit 之后任一次通过则 pending 作废并按上一行结算;unit 终局仍失败(重试耗尽,或 run 以 failed 收尾时该 unit 仍卡着)才落 `disputed +1` |
| 又打回但不命中它的触发词 | unit 终局失败时落 `abandoned`(中性行,不进任何计数器) |
| 没有下一次(agent 收尾了) | 落 `abandoned`(pending 作废;这一行只为了别让条目停在「注入过、从没结算」) |

结算行由 TS 追加进 `usage/<run_id>.jsonl`(`{kind: "settle", entry_id, unit, outcome, at}`),TS 不碰 meta;meta 计数一律由 python 从 usage 重放算出。可选加强(第二步):proposal.json 加 `beacon: [string]`(≤3 条,门要求逐字出现在 Known Mitigation / Verification 段),命令出现在注入后的 fix_window 里也算激活。

误伤两面都有,接受:agent 没照做也过、陪跑条目也 +1(靠激活标记和 shared 标记区分);agent 没照做又撞墙算打脸(严一点顶多退回人批)。K=2 是自定值,汇报时不说有调研支持。

### 8.2 升降级(promote.py,post_finish 跑)

- provisional 且 op=add 且 `useful_activated ≥ 2` 且这两次来自 ≥2 个不同 run_id 且 `disputed = 0` → 自动标 confirmed:meta.status = confirmed(by auto)、decisions 追加 promote、索引标 [confirmed],entry.md 拷进 `sure/memory/outbox/<target_skill>/<slug>/`;**不碰 references/ 和 README**(复核 M2:生产检出直写跟踪目录会挡 `merge --ff-only`,别人也 commit 不了)。agent 消费走 index.md,功能不受影响;人用 `cli export` 搬进 references(§9)。fact 不自动转正;
- `disputed ≥ 1` → meta.status = disputed,索引带标记,永不自动转正,等人 confirm / reject / supersede;
- confirmed 且自上次 `useful_activated` 以来连续 `disputed ≥ 2` → meta.status 退回 provisional,索引带标记,文件不动;
- fact:不按命中升降,索引里显示 checked_at 与 stale;
- supersede:旧条目不删,头部加 `Superseded-by: <entry_id> (<date>)`,meta.superseded_by / superseded_at 填上,索引里旧条目不再参与匹配;
- 预防性生效的条目(pre_start 命中,或 agent 读索引后没触发打回)不会攒 useful,长期留在 provisional 是预期行为,靠人从冷条目列表里 confirm;
- 给记忆管理 agent 预留:meta 里的字段就是它以后整理的依据,本稿不实现它。

K = 2、每次 ≤5 候选、每次注入 ≤2 条、正文词数、触发词下限、索引预算这些数字先按本稿,跑出一批真实 run 之后按分布调,全部放 `sure/runtime/memory/config.json`,不散在代码里。

## 9. 人工工具

`python3 -s sure/runtime/memory/cli.py <cmd>`(集群共享 python 一律 `-s`;cli 及其导入链只用标准库,不 import yaml):

| 命令 | 干什么 |
|---|---|
| `list [--status …] [--skill …] [--cold [--days N]]` | 列条目:id、type、status、格子、useful(激活/未归因)/disputed、last_hit、入库日期;fact 显示 checked_at 与 stale;`--cold` 列 injections=0 的 |
| `show <entry_id>` | 正文 + meta + 原提案 + 注入历史 + 后代(derived_from 反查);evidence 文件不在或哈希变了标 missing / changed,不改 status |
| `compare <entry_id>` | 与 target_entry(modify/supersede)或 similar 指向的条目并排 diff |
| `confirm <entry_id> [--reason]` | 人工标 confirmed 进 outbox(bad_case 和 fact 都可以;modify/supersede 在这里生效) |
| `export <entry_id> [--repo-root <clone>]` | 把 outbox 条目搬进那个 clone 的 `references/memory/bad_cases/<slug>.md`(fact 进 `_shared/memory/facts/`),README 路由行幂等对账,打印建议的 git add / commit 单条命令,不跑 git |
| `reject <entry_id> --reason` | 移入 rejected/,已 export 的打印要删的路径,打印后代清单只显示不级联;被 playbook 引用的老条目先警告 |
| `supersede <old> --by <new>` | 旧条目加头,索引停用旧的 |
| `stats [--skill] [--since]` | 每条:注入 / 被读 / 有用(激活)/ 有用(未归因)/ 打脸 / 最近命中;每技能每 unit 每次 run 的重试数随时间的变化;「有注入且读了 vs 无注入」的下一次即过率;零结算条目占比(cold ratio);每格条目数与「格子被 provisional 占着又有 modify 候选」;各层条目数;index.md 用量 |
| `rebuild-index` | 重建索引并跑 README 对账 |
| `promote` | 立刻重放 usage 跑一遍 §8.2 的升降级再重建索引;post_finish 每次 run 自己跑一遍,这条是手工补跑,用来查「这条为什么还没自动转正」 |
| `fix-perms` | 把 `sure/memory/` 权限修成组协作 |

README 路由行的写法是幂等对账(所有 Status: confirmed 且已 export 的条目缺行补一行,指向已不存在文件的行删掉,现有行含 17 条老行一字不动,内容没变不写盘),写前 flock。工具永不跑 git。

## 10. 代码布局与改动点

共享库 `sure/runtime/memory/`(和 `sure/runtime/harness/` 并列;只用标准库,锁定解释器里虽有 pydantic / typer / rich / structlog / PyYAML,有意不用;没有 jsonschema):

```
sure/runtime/memory/
├─ digest.py          build_run_digest(§4.3)
├─ proposals.py       §5.3 的门(老分支 check_memory_proposals.py 的小函数与声明一致段搬来改)
├─ publish.py         §6.2
├─ usage.py           usage 读取 + 计数重放
├─ promote.py         §8.2
├─ index.py           合并索引 + 老格式解析 + README 对账 + --check
├─ paths.py           memory 目录、组可写、flock、原子写(抄 bootstrap.py 与 run_reval.py)
├─ cli.py             §9(老分支 adopt_memory.py 的写盘部分搬来改)
├─ match.ts           §7.2 匹配 + usage 注入行/结算行追加(TS,hooks 用)
├─ config.json        K、条数、字符数、词数、trigger_min_chars、停用词表、模板短语表、cause 枚举、裁剪顺序、注入块首行常量、index 预算、stale_after_days
├─ units.json         四个技能的 unit id 注册表(vitest 断言与状态机一致;sure_reval 为空表)
├─ log_paths.json     各 unit 的已知日志产物路径(含 {run_dir} / {product_dir} 占位)
├─ fixtures/match_vectors.json   pytest 与 vitest 共用的匹配向量
├─ EXTRACTION.md      extract_lessons 合同全文,两个 SKILL.md 指向它
├─ schemas/           run_digest / extraction_declaration / proposal / meta / index 的 JSON schema(只作文档与测试夹具;门手写校验)
└─ test_*.py
```

每个技能里只放薄封装(hook 只准跑技能自己 `scripts/` 下的脚本):`scripts/build_run_digest.py`、`scripts/check_memory_extraction.py`、`scripts/publish_memory.py`,各几行 `sys.path` + 转调;`schemas/extraction_declaration.schema.json` 各放一份拷贝(常量用 `enum` 不用 `const`,validate.ts 不认 const)。

hooks 改动点(onboard、eval 各一份,逻辑共用):

- `checkpoints.ts`:CheckpointData 加 `memory` 子对象并在 readCheckpoint / advance / bumpRetry 透传;
- `state-machine.ts`:Unit 加 `gateInputs`(eval 还要加 `helperScripts`),插入 `extract_lessons`;
- `index.ts`:`gateDigest`;推进到 `extract_lessons` 时建 digest 并写 cutoff / sha / digestPassed;抽取门耗尽自动 advance;`post_tool_result` 门禁打回时注入(原始 repair + 日志尾)+ 去重 + usage 注入行;门禁结果时结算行;`pre_start` 调 `index.py --check` + 匹配 fact 写 memory_context.json;`pre_finish` 非 success 的抽取要求与 finishAttempts;`post_finish` publish → promote(单独 spawnSync,60 秒超时);`on_error` 建 digest;
- SKILL.md:extract_lessons 合同(指向 EXTRACTION.md)、memory_context.json 的读法、单元表;
- eval:新建 `references/memory/`;onboard:ROUTING 指向合并索引并改示例;
- `.gitignore` 加 `sure/memory/`;`sure/skills/_shared/memory/facts/README.md`;
- 老 17 条补五行头(人 commit);
- 手册加一节「记忆」(记忆目录共用、export 流程、更新生产检出前 `git status --short sure/skills`、`.sure/runs/` 可删记忆库不靠它、output_dir 下多出的产物、第一次 pre_start 会多几秒),根 README / AGENTS.md 提一句,四件同版重建。

从老分支 `dev/sure-harness` 挑过来改的:`check_memory_proposals.py` 的字段清洗 / 证据 / 判重小函数与声明一致段、手写 schema 校验;`adopt_memory.py` 的路径收容、README 路由行、decisions 追加、add / modify / supersede 写盘、compare、`_print_git_suggestion`;`extraction_declaration.schema.json`;两套测试夹具;`finalize_check.py` 的目的地收容与回滚。check 那半(verifier / judge / finalize 主体 / resolve_check_target / hooks 里的 bash 解析门)不过来。外部只抄几段 MIT 小件:OpenHands `_keyword_matches`(可选)与 `_truncate_top`,TencentDB `applyRecallBudget`,hermes 原子写的 Windows 重试分支与 jsonl 容错读,Anthropic SDK `_validate_path` 写法,projectmem `superseded_ids` / staleness 写法;python 与 TS 侧都不加依赖。

## 11. 错误处理与边界情况

- digest 建不出:unit 照进,agent 只能声明无新经验(4.2);
- 抽取门在成功路径连败:到上限自动 advance,记 extractionStatus=failed,不阻断收尾(4.5);
- 没走到 sure_finish 的 run:不抽候选,on_error 只留 digest,下一次 prior_runs 可见(4.5);
- 索引损坏或缺失:pre_start 重建;匹配时读不到就跳过注入并记 diagnostics;
- usage / decisions 写失败:记 diagnostics(权限问题明写属主),不阻塞 run;
- 并发:每 run 单文件、flock、tmp+rename、计数重算,见 6.1;
- headless / output_dir 的 run:同一套 hook;digest 与 memory_context 里没有 output_dir 字样;eval 的目标目录从 `runtime.run_dir` 取,证据规则无差别;
- 非 bash 写工具没门:agent 能直接往 `sure/memory/provisional/` 放文件。接受(记忆只是 advisory,门禁防失手不防恶意);索引器只收有 meta、哈希一致、decisions 有 publish 行的条目,手放的文件进不了索引;
- 注入进 repair 的文字是指令通道:注入行只带标题不带正文,标 agent-written / not human-reviewed;Known Mitigation 命令来源硬门(verified_commands)留第二步。

## 12. 测试

- python 单测(`sure/runtime/memory/test_*.py`,在锁定解释器和系统 `python3 -s` 下各跑一遍):digest(假 run 目录:events / state / artifacts,含先挂后过、终局失败、cutoff 与 mark-passed、剥注入块、剥 output_dir、20KB 裁剪后 repairs / fix_window 仍在、log_tail seek)、门(§5.3 十条逐条正反例,含触发词四类拒、fact 证据、同批判重、三方 sha)、publish(幂等、撞名、中文 H1、modify 不动 confirmed、derived_from / fix_exercised 推断)、usage 重放(§8.1 全部分支:只 cat 不结算、注入两条只读其一只有读过的记激活、同 unit 连打三次只一行注入、pending 作废与落成)、promote(转正进 outbox 不写 references、不同 run 条件、disputed 冻结、退回、fact 不升、supersede 头)、索引(新旧格式、无头老条目不参与匹配、内容哈希触发重建、预算截断、不依赖 .sure/runs)、README 对账、cli(每个子命令对临时库;export 打印 git 命令不执行);
- vitest(`packages/coding-agent/test/suite/`):match.ts(过滤、排序、预算、去重,读共享向量与 golden index)、两个技能 hooks 的新行为(unit 插入位置与计数、进入时建 digest 并写 checkpoint、抽取门耗尽自动 advance、gateInputs 联合哈希让改候选重跑并消耗重试、打回注入与结算行、pre_finish 非 success 三次逻辑、post_finish 调 publish/promote、bumpRetry / advance / unchanged 三条路径后 memory 字段仍在)、units.json 与状态机一致、两份 schema 拷贝与共享库字节相同、每个 unit 的 schemaRef 文件存在、模板短语表每条仍在源码里;
- `npm run check:sure-hooks`、biome;
- 集群 e2e(必做):一次真实 onboard + 一次真实 eval,查 digest、候选、provisional 落库;再跑一次同目标制造同一处打回,查注入出现在 repair 里、usage 有注入行与结算行;两账号同时跑生产检出;fresh clone 首跑;抽取门连败仍 success 收尾;非 success 收尾三次逻辑与 print 模式轮数;output_dir 下 digest 与 memory_context 无 output_dir 字样;kill -9 后 prior_runs 可见;转正 → export → 个人 clone commit → 生产检出 fetch + merge --ff-only 演练;200MB 带 `\r` 的 build.log;`python3 -s cli.py` 与 `$HARNESS_PYTHON_BIN` 各跑一遍子命令;stats 能出表。

## 13. 依据速查

| 设计点 | 依据 |
|---|---|
| 抽取锚在确定性信号(门禁事实) | 调研结论 2:库的质量跟着入库信号可靠性走;TF-GRPO / HarnessBank / Memp 都是验证绑定抽取;Honest Lying(程序化失败信号 RRR 0.64 → 0.10)是「触发词逐字」最贴的依据 |
| digest 带「试了什么 → 过了没」 | FlashRT:先测量再采信;IBM Trajectory-Informed Memory 的 recovery tip、EvoMem 的先挂后好对比 |
| 抽取必经、失败也抽 | Reflexion 的材料在失败侧;TencentDB 的「强制归档」还在它的 roadmap,只当思路 |
| provisional 自动落库 + 人可否决 | Cursor Memories 1.0 无门 → 1.2 加人审 → 2.1 整功能下架(官方未给原因);Windsurf 新默认 agent 不再持久化;Devin 人批有效:折中成「自动进暂定层、注入时标记、人随时否决」 |
| 有用命中而不是被读次数;激活条件 | ACE helpful/harmful 计数、MemRL、ReMe(f≥5 且 u/f≤0.5 删)是同款;RoMeRL memory-reward trap、HarnessBank false elites、Counterfactual Trace Auditing 说明「过了 ≠ 用了」 |
| 一次打脸即冻结;K=2 | 无外部出处(有具体 K 的只有 HarnessBank K=3 加配对显著);冻结只是退回人批不是删,站得住;数值先自定 |
| 注入硬预算、注入块剥离 | TencentDB 召回预算与 sanitize 防反馈回路;SkillCorpus 每次 0..2 条;Demystifying Agent Skills(池子大了精度掉) |
| 由 harness 投递而不是 agent 主动读 | Delivery, Not Storage(agent 114 轮主动读 0 次);TRACE(存了 ≠ 遵守) |
| 格子一格一条、supersede 不删 | HarnessBank;Zep bi-temporal / MemOS 状态枚举(字段形状) |
| 血统与证据哈希 | When Self-Evolution Backfires(污染链,删源只追回 1.7/12.3)、SkillJack、EA-Graph |
| 不上向量库、不整体重写、不 LLM 裁判退役 | 调研 3.11 / ACE 塌缩;MOOSEDev(supersede 类查询向量 top-k 只找回 6-27%);Blind Curator / Ratchet |
| 索引预算、只标不删 | Claude Code MEMORY.md 200 行 / 25KB;hermes 30/90 天标记;projectmem 过期只标 |

## 14. 决策记录与复核对照

| # | 问题 | 定的 |
|---|---|---|
| 1 | 越用越好用先看哪种 | 同坑不踩两次 → 环境事实;多实例共享不做 |
| 2 | 抽取挂哪 | 技能末尾的 unit,不加命令 |
| 3 | 失败的跑抽不抽 | 抽;门连败两次放行记 failed |
| 4 | 第一版覆盖 | onboard + eval |
| 5 | 条目种类 | bad_case + fact,type 留 recipe |
| 6 | 进库要不要人批 | 自动进 provisional,人可否决 |
| 6b | 自动升级按什么 | 有用命中 K=2;TencentDB 没这机制,规则自定 |
| 7 | 多 agent 协同指哪种 | 跨技能路由;管理 agent 预留;按角色不做 |
| 8 | 消费怎么保证读到 | 路由 + hook 注入,命中只从 hook 算 |
| 9 | 放哪、进不进 git | provisional 等实例数据 gitignore;confirmed 进 references,人 commit |
| 10 | 老分支 check 那半 | 不进新分支 |
| 11 | 有用/打脸怎么判 | 同 run 同 unit;一次打脸即冻结;复核后结算点取 unit 终局 + 激活条件 |
| 12 | digest 装什么 | 带修复窗口、日志尾、历次 run、本次注入记录;触发词逐字落地并加纪律 |
| 13 | 正文格式 | 六段式 ≤200 词;fact 短模子;老条目不回改 |
| 14 | unit 放哪一位 | 业务结论之后、收尾之前;LAST_UNIT 不动;门耗尽自动放行 |
| 15 | 开发方式 | 新 clone `dev/sure-memory`,分支 `feat/memory-system`,与主线隔离;设计稿放仓库 docs/superpowers/specs |

复核报告条目落到本稿的位置:H1 → §1、§4.5;H2 → §4.2、§10;H3 → §4.2、§5.3 规则 9;H4 → §4.1;H5 → §7.2、§8.1、§8.2、§9、§12;H6 → §4.3、§5.3 规则 4、§7.2;M1 → §6.1、§9;M2 → §8.2、§9、§0;M3 → §6.1;M4 → §4.5;M5 → §4.1、§10;M6 → §5.3 规则 7;M7 → §5.3 规则 8、§7.2;M8 → §5.1、§6.4;M9 → §4.3、§5.3 规则 2;M10 → §4.3、§6.2、§6.3、§6.4;M11 → §6.4、§7.2、§8.2;M12 → §4.2、§7.1、§10、§12;M13 → §0、§13。复核 §6 列出的不采纳项照旧不做。
