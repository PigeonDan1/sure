# 记忆系统 v2 设计稿评审报告

日期:2026-08-18。评审对象:`D:/sure/dev/sure-memory/docs/superpowers/specs/2026-08-18-memory-system-design.md`(下称设计稿,行号指该文件)。

材料:七份调研报告(prior-research、old-code、ext-memory-libs、ext-experience-research、ext-products、ext-scoring、ext-recent)、四份挑刺报告(critic-mechanism、critic-integration、critic-operations、critic-alignment),以及核实过的发现清单。全程只读。我自己重开的本地文件只有:设计稿全文;`sure/skills/sure_onboard/hooks/checkpoints.ts` 26-31 与 61 行、`sure/skills/sure_eval/hooks/checkpoints.ts` 61 行(CheckpointData 四个键、重试上限 3 / 2);`sure/skills/sure_onboard/hooks/index.ts` 887、906、920、936、971 行(unchangedFailedArtifact / failOrRetry / preFinish 位置与「remains blocked」文案);`sure/skills/sure_eval/hooks/index.ts` 247-254 行(脚本固定放行名单);`sure/skills/sure_onboard/hooks/validate.ts` 29 行(schema 只从 packageDir/schemas 读);`packages/coding-agent/src/core/sure/extension.ts` 98、920、956 行;`sure/runtime/harness/requirements.in`;根 `package.json` 45-56 行;`sure/skills/sure_onboard/references/memory/bad_cases/README.md` 8-30 行。其余代码行号、外部仓库、许可证、论文都以各报告为准,报告的抓取日期都是 2026-08-18;我没有重开任何外部 URL。没有打开 `D:/sure/models.json` 和 `D:/sure/参考/apifusion-网关接入.md`。

十五条钉死决定(设计稿 §2)不提翻案。下面凡是「必须改」的,都是决定带出来而设计稿没接住的后果,或者是设计稿正文与决定本身打架的地方。

---

## 1. 结论

设计稿的骨架站得住:抽取锚在门禁事实、候选自动进暂定层、触发词路由加 hook 注入、有用命中转正、supersede 不删。十一份报告没有一份推翻这条主线,新出的文献(2026-06 到 08)反而分别把它的主决定各验证了一遍。问题全在细节上,而且是「不补就跑不起来或跑歪」那种。

必须改的分两档。高档 6 条,每条都会让 run 卡死或让自动转正学错:(1)抽取门在成功路径连败会把业务成功的 run 卡成收不了尾,与决定 3 相反;(2)checkpoint 是四字段白名单,设计稿要记的 cutoff、attempts、sha 一写就丢;(3)门重建 digest 比哈希会被并发 run 和后台日志误拦;(4)门只按声明文件哈希决定重跑,agent 改候选不重跑;(5)有用 / 打脸结算规则有搭便车、结算太早、重复注入、把 ok:true 当过门四个漏洞叠在一起,K=2 一次 run 就能凑够;(6)触发词从取到用没有一条完整的纪律,来源比匹配面宽、无长度门槛、实例专属串能过门、注入块会污染下一次匹配和 digest。中档 13 条,集中在多人共用的生产检出(权限、并发、转正写跟踪目录挡 ff-only)、格子与判重、老 17 条、digest 取材与预算、索引预算、若干接入面小坑。

可以直接拿来用的大件都是自己的:老分支 `check_memory_proposals.py` / `adopt_memory.py` 的小函数与两份测试夹具,主线的 `checkpoints.ts`(readCheckpoint / advance / bumpRetry / runBackend)、`validate.ts`、`bootstrap.py` 的组可写与 flock、`run_reval.py` 的原子写与规范 JSON。外部只有几段几十行的 MIT 小件值得贴(OpenHands 整词匹配与顶部截断、TencentDB 注入预算、hermes 原子写与 jsonl 容错读、Anthropic SDK 路径校验、projectmem 过期判定),没有一个值得装成依赖;十一份报告在这一点上完全一致。

---

## 2. 设计要改的地方

每条给「问题 / 证据 / 改法 / 涉及节」。同一根因合成一条。「证据」里的报告名指 scratchpad/research/ 下对应文件;代码位置除我上面列出的自查项外,均引自报告。

### 2.1 高:不改就跑不起来或学错

**H1. 抽取门在成功路径连败会把成功的 run 卡死在收尾之前**

- 问题:设计稿 §11 第 379 行说成功路径抽取门连败「沿用 unit 重试上限,超了 unit FAILED,agent 以 failed 收尾再走 4.5」。可 extract_lessons 插在 LAST_UNIT 之前(§4.1),unit FAILED 后 currentUnit 停住,finalize_model_bundle / run_report 到不了。onboard 以 success 收尾要求状态机到终点,以 failed 收尾要求 `deployment_ready.status ∈ {blocked, local_only}` 且 `bundle_ready=false`,一个已打包成功的模型两条路都不通,agent 只能造假或被催办 3 次后以 failed 死掉且不走 post_finish;eval 上限只有 2 次,更容易触发,只能写一份与事实相反的 failed 报告。这和决定 3「连败两次放行」正好相反。
- 证据:critic-integration §2.13、critic-operations §3.8、old-code §5.3、critic-alignment C-13;核实清单 F2(integration)、OPS-01、F3(old-code)判定 confirmed。代码位置:onboard `hooks/index.ts` 1033-1043、1081-1109;eval `hooks/index.ts` 263-271、537-547;eval `checkpoints.ts:61` 上限 2(我自查)。
- 改法:extract_lessons 不走通用「耗尽即 FAILED」。门连败到上限(按决定 3 用 2 次,数值进 `sure/runtime/memory/config.json`,onboard 的 `max_retries=` 参数只准调大)时 hook 自动 advance 到下一 unit,checkpoint 记 `memory.extractionStatus = "failed"`,diagnostics 加 `extraction: failed (<原因>)`,post_finish 见 failed 就跳过 publish。§11 第 379 行改成这句;§4.5 的 pre_finish 三次逻辑保留给「根本没进过 unit 的失败 run」,两处次数统一;两个技能耗尽提示语里「finish with status failed」这类话对 extract_lessons 去掉;EXTRACTION.md 补一句「候选过不了门可改成 no_new_lessons: true 并写明原因,不算绕门」;§11 加一句「抽取门是 advisory 副产品,不得阻断主流程收尾」。
- 涉及:§4.5、§11、§10、决定 3 与 14 的后果。

**H2. checkpoint 装不下设计稿要记的字段**

- 问题:§4.2 要记 `extractionDigestCutoff`,§4.5 要记 `extractionFinishAttempts` 和 state 补丁 `extraction_status: failed`。但 `CheckpointData` 只有 currentUnit / completedUnits / retries / failedArtifactDigests 四键(我自查 checkpoints.ts 26-31),`readCheckpoint` 白名单重组、`advance` / `bumpRetry` 重新构造 data、state 合并对 checkpoint.data 整体替换、`normalizeSureDisplayStatePatch` 丢顶层多余键、diagnostics 整体替换。新字段经过下一次任何 checkpoint 写入就没了,门只能退回信 digest 文件自己写的值。
- 证据:critic-integration §2.2、critic-operations §3.9、old-code §5.1;核实清单 F1(integration)、OPS-08、F1(old-code)判定 confirmed。代码位置:checkpoints.ts 77-123、128-170;state.ts 236-311(报告引)。
- 改法:两份 checkpoints.ts(onboard、eval)的 CheckpointData 加可选 `memory?: { digestCutoff?: number; digestSha256?: string; digestPassed?: string; finishAttempts?: number; extractionStatus?: "failed"; injected?: Record<string, string[]> }`;readCheckpoint 按类型读回;advance / bumpRetry 用 `...completed` / `...current` 展开后再覆盖四键;preFinish 成功收尾手拼四字段处(onboard 1050-1058、eval 553-561)同样带上。§4.5 第 143 行「state 补丁记 extraction_status」改成 checkpoint 字段,diagnostics 只作展示;§10 改动点加 checkpoints.ts 一行;§12 vitest 加「bumpRetry、advance、unchanged 三条路径后 memory 字段仍在」。备选(不推荐):单独落 `artifacts/memory_state.json`,和现有「checkpoint 一处存状态」不一致。
- 涉及:§4.2、§4.5、§10、§12。

**H3. 门重建 digest 比对哈希会误拦;digest 时序与「agent 也可以重跑」连带要收口**

- 问题:§5.3 规则 9 让门重建 digest 比 sha。digest 含 `prior_runs`(读 `.sure/runs/`)、`memory_index_snapshot`(读 `sure/memory/index.json`)、`status_so_far`、`log_tail`,另一个 run 收尾 publish、任何 run 重建索引、vc/docker 后台还在追加日志,都会让重建结果不同,诚实候选被当「手改 digest」拦下,而且反复拦,eval 两次就到上限触发 H1。另外 hook 数 cutoff 那一刻,「verdict / assessment 已通过」的 state 事件还没写进 events(extension 先追加 tool_result 再调钩子),digest 会把刚过的 unit 记成 current;§4.1 第 92 行「agent 也可以重跑 build_run_digest.py」在 eval 白名单下会被拒(eval Unit 无 helperScripts,固定放行名单我自查 index.ts 247-254 只有六个),重跑后文件 sha 也和存下的对不上。
- 证据:critic-mechanism M-11、critic-integration §2.11 与 F10、critic-operations §3.4/§3.5、old-code §5.4/§5.5/§5.7;核实清单 M-11、F4(integration)、OPS-04、F4(old-code)、F10、F7、F5(old-code)。
- 改法:hook 建完 `artifacts/run_digest.json` 立刻算 sha256 连同 cutoff 记进 checkpoint(H2 的 `memory.digestSha256`),两处建 digest(进 unit、pre_finish 失败路径)都记;门规则 9 改成三方相等:`proposal.source.digest_sha256 == checkpoint.digestSha256 == 磁盘文件现算 sha`,不再重建;规则 3、4 直接读文件。防手改目标不降,因为 `sure_update_state` 改不了 checkpoint。§4.1 第 92 行删掉「agent 也可以重跑」,改成「只由 hook 建;agent 不调 build_run_digest.py」,preToolCall 白名单不用改;若坚持保留手动重跑,只许 `--out artifacts/run_digest.preview.json`,门只认 hook 那份。hook 推进到 extract_lessons 时同一 checkpoint 补丁里写 `digestPassed: <刚过的 unit id>`,调脚本时传 `--mark-passed <unit>`,脚本把该 unit 记 passed 并按先挂后过规则建 fix_window。§4.3 写明 units 只从 events ≤ cutoff 推,不读 state.json 的 retries。规则 9 的 repair 文案改成「让 hook 重建」而不是让 agent 手改。§12「cutoff 一致性」测试改成「checkpoint sha 三方一致、手改 digest 被拦、改 .sure/runs 或 index.json 后门仍放行」。
- 涉及:§4.1、§4.2、§4.3、§5.3 规则 9、§12。

**H4. 门只按声明文件哈希决定是否重跑,agent 改候选门永不重跑**

- 问题:`unchangedFailedArtifact` 与 `failOrRetry` 开头只哈希 produces 那一个文件,内容没变就不重跑门、不消耗重试,只回「remains blocked on unchanged artifact content」(我自查 index.ts 906、936 行文案)。extract_lessons 的 produces 是 `extraction_declaration.json`,门拒的多半是 `candidates/**/proposal.json` 或 `.md`;老声明 schema `additionalProperties:false`,agent 连一个能「碰一下」的合法字段都没有。老 ledger 103 行记过这条 minor 未修。
- 证据:critic-integration §2.12、old-code §5.2;核实清单 F6(integration)、F2(old-code)。
- 改法(推荐 A):两个技能 `state-machine.ts` 的 Unit 加可选 `gateInputs?: string[]`(相对 artifacts/ 的目录或文件),extract_lessons 声明 `["candidates", "memory_evidence"]`;`hooks/index.ts` 抽一个 `gateDigest(ctx, unit)`,把 produces 加 gateInputs 下所有文件按相对路径排序、路径加内容一起进 sha256,`unchangedFailedArtifact` 与 `failOrRetry` 改用它;其他 unit gateInputs 为空时行为不变。vitest 加「只改 proposal.json 门重跑并消耗重试」。备选 B(弱):合同要求「候选先写、声明最后写、打回后必须改声明」并给 schema 加可选 `attempt` 字段;这条靠 agent 记得,不推荐。§4.5 无论选哪种都要把「同内容不重跑」这条现有规则写进去。
- 涉及:§4.1、§4.5、§10、§12。

**H5. 有用 / 打脸结算规则:搭便车、结算太早、重复注入、把 ok:true 当过门**

- 问题(四个漏洞同一根因,都在 §7.2 与 §8.1 的结算文字):
  1. 搭便车:注入 ≤2 条、下一次门禁过了每条 useful +1,不区分「agent 照记忆做了」和「自己修好的」,也不区分独占与共注入;K=2 时两次搭便车就自动转正,一条触发词很宽的无关条目当次 run 就能进 git 跟踪目录。RoMeRL 叫它 memory-reward trap,HarnessBank 的 false elites 是同一件事。
  2. 结算太早 / 打脸过敏感:onboard 常要两三轮修,第二轮仍打回且触发词还在就 disputed +1,一次 disputed 永不自动转正;同一 run 同一条会同时拿 useful 和 disputed 然后冻结。「需要两轮修好的坑」正是常态。
  3. 重复注入:同 unit 连打回三次同两条塞三次,injections 灌水、repair 越拼越长,直接触发第 2 条。
  4. ok:true 不等于过:产物未变(remains blocked)、产物缺失、工具报错三条路径都返回 ok:true 且 diagnostics 带同样 repair;真正过了只有 advance 那条路,又打回只有 bumpRetry 真消耗那次。若结算按「post_tool_result 里出现带触发词的 repair」判,agent 只 cat 一下日志就白得 useful 或白挨 disputed。
  另有并发面:TS 结算「同步更新 meta」与 python promote / cli 是双写者,tmp+rename 只保证原子不保证隔离,集群并行 run 会丢计数。
- 证据:critic-mechanism M-02、M-03、M-10;critic-alignment C-1、C-2、C-5;critic-operations §3.10;ext-scoring §6.1-6.2(RoMeRL、MemRL、HarnessBank、Counterfactual Trace Auditing、Honest Lying);ext-experience-research §3.4;prior-research §3.4(2)(3);ext-products §4.2;ext-recent F2、F3;核实清单 M-02、M-03、M-10、C-1、C-2、C-5、F1/F2(ext-scoring)、F1/F2(exp)、F2(products)、F2/F3(recent)、OPS-09、F16。代码位置:onboard index.ts 887-918、798-812、824-831(报告引);extension.ts 868-872 每次 tool_call 事件带完整 input(读文件可查)。
- 改法(不动决定 7、9、11):
  1. §8.1 表格上方先定义「门禁结果」:只指该 unit 被 advance(过了)或该 unit 的 retries 又加一(bumpRetry 真消耗一次重试,含重试耗尽的 failure)。unchangedFailedArtifact、event.isError、produces 未写三条 ok:true 路径既不注入也不结算。usage 注入行记当时 attempt,结算只在 retries[unit] > 注入 attempt 或 unit 进 completedUnits 时做。
  2. §7.2:同一 run 同一 unit 已注入过的条目不再重复注入(查 checkpoint `memory.injected[unit]`,H2 已加字段);剔完没新条目就不加 Memory 段(可留一句「entries shown at attempt N still apply: <ids>」,不记 usage、不结算)。每条每 unit 每 run 只有一笔注入、一次结算。
  3. §8.1 useful 加激活条件:注入行到该 unit 门禁结果之间,events.jsonl 里有一条 tool_call 的 input 含该条目文件路径(read 的 path 或 bash 命令原文,子串匹配即可)才记 `useful_activated +1`;过了但没读记 `useful_unattributed +1`,只进 stats 不进转正。可选加强:proposal.json 加 `beacon: [string]`(≤3 条,门要求逐字出现在 Known Mitigation / Verification 段),命令出现在注入后 fix_window 里也算激活;没 beacon 的条目退回读文件判定。usage 注入行加 `events_cutoff`(注入那一刻 events 行数,做法同 digestCutoff)和 `shared: true`。
  4. §8.2 转正条件改成 `useful_activated ≥ 2 且来自 ≥2 个不同 run_id 且 disputed = 0`;meta 把 useful 拆成 activated / unattributed(和为老 useful,cli 列不变);退回条件里的「自上次 useful」改成「自上次 useful_activated」。
  5. 结算点二选一,给用户定:
     - 现状:按「注入后该 unit 下一次门禁结果」结算(§14 第 11 行的写法)。
     - 选 A(维持):配合第 1、2 条后,同一条同 unit 只结算一次,不再双计数,但 agent 看了记忆仍需两轮修好的场景下,正确条目第一次用就被 disputed 冻住,靠人 confirm 解。
     - 选 B(改终局):「又打回且命中触发词」先记 pending;同 run 同 unit 之后任一次门禁通过则 pending 作废并按首注入记 useful;unit 终局仍失败(重试耗尽或 run 以 failed 收尾时该 unit 仍卡着)且末次 repair 命中触发词,pending 落成 disputed +1。仍是「同 run 同 unit 判、一次打脸即冻结」,§2 决定 11 原文不动,只改 §14 第 11 行「下一次门禁」为「unit 终局」;promote.py 在 post_finish 跑,那时终局已知,不需新调度点。代价:「看了没照做、别的路子过了」也算 useful,和 §13「宁严勿松」方向相反,但第 3 条的激活条件把这一面兜住了。
     - 选错怎样:选 A 且不补激活条件,K=2 会放进搭便车条目并冻错正确条目,人审队列比 v1 更长;选 B 不补激活条件,useful 更虚。多数报告(ext-scoring 建议 4、ext-experience §3.4、prior F-2、critic-mechanism M-03)推荐 B 加激活条件。我也推荐 B。
  6. 单写者:§8.1 第 309 行改成 TS 只往 usage.jsonl 追加结果行(每条目带 useful / disputed / 中性判定),不碰 meta;meta 的 useful / disputed / injections / last_hit 一律由 python 从 usage 重放算出(promote.py 在 post_finish、cli 的 stats / rebuild-index),写 meta 时 `fcntl.flock` 一把 `sure/memory/.lock`(照 bootstrap.py 439 行写法,Windows 单测 try-import 兜底);§10 usage.py 职责改成「usage 读取 + 计数重放」,match.ts 只「匹配 + 追加 usage 行」;§12 vitest 只测 hooks 写了正确的 usage 行,python 单测测计数与升降级,结算规则只留一份。
  7. §8.1 补两句:useful 侧同样有误伤(agent 没照做也过、陪跑条目也 +1),接受,靠激活标记区分;两条一起注入会一起 +1,K=2 是自定值,汇报时不说有调研支持。§9 stats 每条加「注入 / 被读 / 有用(激活) / 有用(未归因) / 打脸」列,加「有注入且读了 vs 无注入」的下一次即过率,加零结算条目占比。§12 测试加:注入后只 cat 不结算;注入两条只读其一随后过门只有读过的 +1;同 unit 连打三次 usage 只一行注入;pending 转 disputed 与作废各一例。
- 涉及:§7.2、§8.1、§8.2、§9、§10、§11、§12、§14 第 11 行。

**H6. 触发词从取到用没有一条完整纪律**

- 问题(六个口子同一根因):
  1. 来源比匹配面宽:门 4 允许触发词出自 repairs、log_tail 或证据文件;§7.2 只拿 repair 文本 + unit id 匹配。来自日志尾 / 证据文件的触发词(`libcudart.so`、`no kernel image is available`、`Not allowed submit to minijob queue` 这类)以后任何打回的 repair 里都不会出现,条目永远注不进去、攒不到 useful、永远 provisional。
  2. 无门槛:`error`、`failed`、`cuda` 逐字都能找到;hooks 模板文案(`is missing required field`、`must be a JSON object`)也能过门,子串匹配下打中一切,白占名额直到攒出 disputed。OpenHands 就是因为 `git` 打中 `github` 从裸子串改成整词。
  3. 实例专属:run_id、绝对路径、时间戳、临时目录、纯数字都能逐字找到并过门,下次永不匹配;条目占着 provisional 和格子。digest 的 repairs 只留头 600 字符,run_validate.py 的 stderr 是「固定前缀 + exit_code + log_path 绝对路径 + 原始 stderr」,真正有区分度的 traceback 末行在尾部被截掉,agent 只能选前缀或路径。
  4. 注入块污染:Memory 块拼在 repair 末尾,extension 把整个 gate 结果存进 `tool_result_repair` 事件和 lastRepair(我自查 extension.ts 920);下一次打回的 repair 天然含触发词(误判 disputed),下一次 run 的 digest repairs 里带记忆文本,门 4 被记忆自己满足,条目互相「繁殖」触发词;result.json 的 error 里也带着 Memory 块。
  5. 双实现分叉:归一化、子串、`re:` 正则、判重在 python 门和 TS match.ts 各写一份,无共享规范与测试向量;Python `re` 与 JS RegExp 方言不同,`re:` 触发词又不设超时,一个回溯灾难正则可以卡死 hook,规则 4 对正则的「逐字出现」也没定义。
  6. 排序奖励宽触发词:按命中条数排,5 个宽词的条目永远第一;disputed 条目是否继续注入未定义;人未 confirm 的 supersede 候选与目标 confirmed 一起进 repair 互相打架;fact 的 trigger 无门、scope 不参与匹配、memory_context.json 无预算。
- 证据:critic-mechanism M-01、M-06、M-08、M-12、M-13;critic-alignment C-3、C-4、C-6、C-15;prior-research F-1;ext-scoring 建议 8 与 F5;ext-products §2.7、§4.1、F3;ext-memory-libs §5.1、§5.2、F1、F2;ext-experience-research §3.3、F3;critic-operations §3.7、OPS-06;核实清单 M-01、M-06、M-08、M-12、C-3、C-4、C-6、C-15、F-1(prior)、F5(scoring)、F3(products)、F1/F2(libs)、F3(exp)、OPS-06。代码位置:run_validate.py 508-512、check_env.py 258、validate.ts 138-264(报告引)。
- 改法(不动决定 12「逐字来自失败文本或证据文件」):
  1. 匹配文本:§7.2 门禁打回时 matchMemory 的文本 = 门禁脚本原始 repair(拼 Memory 块之前)+ 该 unit 在 `log_paths.json` 登记的日志尾 30 行(与 digest 同窗口,读不到就只用 repair);§8.1 打脸判定用同一份文本。log_paths.json 给 eval 补 smoke_test,给 onboard 补 build_env 的 log_path。
  2. 注入块:Memory 块的固定首行「Memory (advisory」放进 config.json 当常量,match.ts 拼、digest.py 剥共用;digest 从 `tool_result_repair` 事件优先取 `state_patch.diagnostics[].repair`(原始文本),取不到再按常量剥;digest repairs 改成头 200 + 尾 400 字符;§12 加「events 里 repair 带注入块时 digest 不含块、拿块里的词当 trigger 被拒」。§7.2 明写块会随 repair 进 lastRepair / events / result.json error,接受(不改 extension.ts);手册「记忆」一节提 output_dir 下多出的产物。
  3. 门 4 加硬判(数值进 config.json):每条 trigger 去首尾空白后 ≥ `trigger_min_chars`(建议 8);不等于停用词表(error / failed / failure / exception / warning / missing / invalid / cuda / timeout / not found);剥掉模板短语表(从 validate.ts / index.ts 固定文案手列,vitest 断言每条短语仍在源码里)后剩余非空白字符仍 ≥ 下限;每条 bad_case 至少一个 trigger 同时满足:不含本 run 的 run_id、target.id、`.sure/runs/`,去标点后不是纯数字 / 纯十六进制,不匹配 ISO 时间戳正则;每条 bad_case 至少一个 trigger 出自 repairs 或 log_tail(纯证据文件的触发词只服务 prompt 级路由,不参与 hook 命中);每条触发词条数上限 5;触发词不许含 `;`(头行用 `;` 分隔,老 ledger 63 行);agent 候选不得以 `re:` 开头。拒的 repair 写清「至少一条 trigger 要是下次同样失败还会原样出现的字串」。
  4. fact:门 4 扩到 fact,trigger 允许为空,非空时须逐字出现在它引用的证据文件里;scope 只认 `cluster` / `model_family:<名>` / `dataset:<名>`;§6.3 meta 与 §4.3 快照加 scope;§7.2 pre_start 对 fact 按 scope 机械匹配(cluster 一律命中,model_family / dataset 名归一化后是 target id 或 datasets 参数归一化后的子串),trigger 只作补充;memory_context.json 加预算(confirmed 全收、provisional ≤ N 条,N 进 config.json 先 10)。
  5. v1 删 `re:` 正则(§7.2 第 294 行、§12 第 391 行);触发词谓词只写一处规范:trigger 与文本各自 `lower()` 后原样子串比较,不折空白不做其他归一化;python 门规则 4、TS match.ts、§8.1 打脸判定三处引用它。加 `sure/runtime/memory/fixtures/match_vectors.json`,pytest 与 vitest 读同一文件断言;index.json 加 `schema: "sure.memory.index.v1"`,python 单测落 golden index.json,vitest 读同一份;match.ts 遇未知 schema 按索引损坏记 diagnostics。
  6. 排序与名额:§7.2 排序改成状态三层 confirmed > provisional > disputed(superseded 已排除),同层按「命中触发词的最长长度」降序而不是条数,再 `useful_activated - disputed`,再新旧;disputed 继续参与匹配但排最后一层,注入行加 `[disputed]` 标签;modify / supersede 候选照常进索引、匹配、计数(给 cli confirm 当依据),但与其 target_entry 同时命中时合并成一行只占一个名额(目标行后挂「pending revision: <候选路径>」),usage 记两个 id;注入行的「一句话」定死取条目 H1 标题,不取正文任何一段(命令永远不进 repair 正文);超预算规则写死:单条封顶 `inject_max_chars_per_entry`(建议 300),两行拼完超 1500 就整条丢第二条,不截半句,usage 只记实际注入的。
  7. EXTRACTION.md:触发词优先抄 repair 里能定位失败种类的那一截,日志尾次之,不抄单个通用词、log_path、时间戳、run_id、目标名、门禁固定前缀;proposal.json / proposal.md / memory_evidence 用 write 工具写,bash 只跑观察命令并 tee(bash 命令文本里出现 `scripts/*.py` 会触发脚本白名单拒掉整条 heredoc,critic-integration §2.7);log_tail 只是提示,报错行请用 path:line 引证据文件;fix_window 只有命令没有输出,只能从命令差异推。
- 涉及:§4.3、§5.2、§5.3 门 1 与门 4、§6.3、§7.2、§8.1、§10、§12。

### 2.2 中:不改会在生产检出或第一批真实 run 上出事

**M1. 生产检出多人共用一个 sure/memory/,权限让别人的写入静默失败**

- 问题:生产检出是一个 clone 很多人用(决定 1 排的是「多实例共享」,没排这个)。第一个跑的人建的 `sure/memory/`、usage.jsonl、index.json、meta/ 默认 755/644,其他账号 append / rename / 建目录全部 EACCES;§11 第 381 行写「写失败只记 diagnostics」,结果别人的候选永远落不了库、命中永远不结算,且无人察觉。设计稿没有一处提权限、umask、组。
- 证据:critic-operations §3.1;核实清单 OPS-02。手册 74-81 行、bootstrap.py 54-94(报告引)。
- 改法:§6.1 加一段:`sure/memory/` 及其下所有目录、文件按检出的组协作权限建。实现:把 bootstrap.py 的 `_apply_acl` / `_make_group_writable` 抄进 `sure/runtime/memory/paths.py`(copy,标准库),第一次建根目录跑一次(setfacl 默认 ACL 优先,退路 setgid + g+rwx),每次新建子目录、临时文件、jsonl 都经同一写入口 chmod g+rw / g+rwx;TS 侧 usage 追加与建目录用 node:fs 的 mkdirSync + chmodSync(0o2775)、appendFileSync + chmodSync(0o664),十来行;或把建根目录交给 python 先跑,TS 只在已存在目录里追加。§11 第 381 行改成写失败时 diagnostics 明写是权限问题、目录属主是谁、让维护人跑 `cli.py fix-perms`;§9 cli 加 fix-perms 子命令。手册方式 A 加一句「记忆目录是检出里所有人共用的」。顺带记一条:`.sure/runs/` 和 bootstrap 锁有同一问题,不属本稿,但集群 e2e 前得先把生产检出修成能多人跑。
- 涉及:§6.1、§9、§11、手册。

**M2. 自动转正写跟踪目录挡住生产检出的 merge --ff-only;README 路由表读改写并发丢行**

- 问题:promote 改 `bad_cases/README.md` 和新增 `<slug>.md`;上游一旦也改了 README(别的 clone 的转正被推上去,正是设计想要的),ff-only 拒绝「本地修改会被覆盖」;同 slug 的未跟踪文件也让 git 拒绝合并;生产检出 .git 属主只有一个,别人 commit 往 `.git/objects` 写会 EACCES;K=2 在生产检出最容易凑够,转正最频繁的地方恰恰最不能写跟踪文件。另外 README 追加行是读改写,两 run 同时 promote 互相覆盖;集群 NFS 上 flock 跨节点不可靠。v1 说「收编写 references 是人工串行的」,v2 自动 promote 后这句不成立,§11 没接。
- 证据:critic-operations §3.2;critic-alignment C-14;ext-memory-libs §5.4;prior-research D-5;核实清单 OPS-03(confirmed)、C-14、F4(libs)。
- 改法(不反转决定 6、7、10;和设计稿第 317 行「动 git 跟踪的文件交给人」同口径):
  1. promote.py 自动转正只改 `meta.status = confirmed (by auto)`、decisions 追加 promote、索引标 [confirmed],把 entry.md 复制到 gitignore 下的 `sure/memory/outbox/<target_skill>/<slug>/`;不碰 references/ 和 README。agent 消费走 index.md(第 276 行),新条目自带 Trigger 头,功能不受影响。
  2. cli.py 加 `export <entry_id> [--repo-root <某个 clone>]`:把 outbox 条目搬进那个 clone 的 `references/memory/bad_cases/<slug>.md`、补 README 路由行、打印 git add / commit 行(沿用 adopt_memory.py 478-487 做法,仍不跑 git)。维护人从个人 clone 提交推 GitLab,生产检出照常 ff-only。
  3. README 路由行的写法从「追加一行」改成幂等对账(所有带溯源头且 Status: confirmed 的条目缺行补一行,指向已不存在文件的行删掉,现有行含 17 条老行一字不动,内容没变不写盘),rebuild-index 顺带跑同一对账;promote 幂等(目标已存在或 meta 已 confirmed 就跳过);写 README 前 flock,同节点并发消掉,跨节点靠对账兜底。
  4. 索引器:references 里已有同 entry_id 时以 references 为准,outbox / provisional 那份不再参与匹配。
  5. 手册「记忆」一节:更新生产检出前 `git status --short sure/skills`,有人手 export 出来的文件先撤掉再 ff-only;不要在生产检出里 commit;手册改动走四件同版重建。
  若坚持自动直写 references,至少不自动改 README;同路径未跟踪文件的问题仍在,slug 加 run_id 后缀只是换成跨 clone 重复条目,不推荐。
- 涉及:§6.1、§8.2、§9、§11、手册。

**M3. 跨 run 共享的 usage.jsonl / decisions.jsonl 在 NFS 跨节点 append 不安全;slug 与临时文件名竞态**

- 问题:O_APPEND 只在同一节点内核串行化,跨节点(d6 与其他节点、vc 作业)NFS 客户端各自取长度再写,会出现半行或覆盖;slug 用 exists 再建有竞态;固定 tmp 文件名会混写。§11 第 250、382 行认为 tmp+rename 与 append-only 足够。
- 证据:critic-operations §3.1;核实清单 OPS-05。
- 改法(决定 10 只钉了放在 sure/memory/ 下,没钉单文件):usage 改为 `sure/memory/usage/<run_id>.jsonl`,每 run 单写者,读时 glob 合并;decisions.jsonl 只由 python 写,单文件保留但 append 前 flock;所有读 jsonl 的地方跳过解析失败的行并记 diagnostics;meta 计数从 usage 重算(见 H5 第 6 条);publish 建 slug 目录用 `os.mkdir` 抓 FileExistsError 再加 `-2`;临时文件用 `tempfile.mkstemp(dir=目标目录)`,TS 用 pid + 时间戳后缀;usage 单行 < 4096 字节。§11 第 250、382 行改写成「同节点 append 由内核串行化,跨节点靠每 run 单文件与 flock,计数从 usage 重算」。
- 涉及:§6.1、§6.2、§11。

**M4. 没走到 sure_finish 的失败 run 一条经验都抽不到;设计稿只写了 cancelled / aborted**

- 问题:两个 `sure.skill.json` 的 `required: true` 产物在 pre_finish 之前就由 extension 校 manifest(eval 要 run_report + execution_surface,onboard 要 verdict + runtime_inventory + deployment_ready),任何状态都一样;早期失败的 run 不手工补文件就进不了 pre_finish,多以 agent_end / session_shutdown 收场,记成 failed,只跑 on_error,没有 post_finish。决定 3「失败也抽」在这条路上落空,§4.5 第 145 行只说 cancelled / aborted 没有 finish。
- 证据:critic-integration §2.4、§2.6;critic-operations §3.11;old-code §4;核实清单 F5(integration)confirmed。extension.ts 434-439、345-373、930-988(报告引)。
- 改法:§4.5 第 145 行和 §11 第 383 行改成「没走到 sure_finish 的 run(extension 会记成 failed 或 cancelled)不抽」;决定 3 / §4.5 明写「失败也抽」的前提是能通过 required 产物检查和技能自己的终态证据检查(onboard 至少走到 package_gate 之后、eval 至少走到 execution_readiness / smoke 之后),更早挂死的只留 digest 不抽候选。补材料二选一:a. 最省事:build_run_digest.py 建 prior_runs 时每条多带 `last_repair`(从上一次 run 的 run.json lastRepair 或 events 最后一条 gate 打回文本取,≤300 字符);b. on_error hook 调 build_run_digest.py 写 artifacts/run_digest.json(不发布不写候选),§10 加 on_error 一行,session_shutdown 时机能否稳定跑完要在集群 e2e 验。§4.5 顺带明写决定 3 的代价:一个没写过抽取声明的失败 run 最少要调 sure_finish 三到四次,SKILL.md 提前告诉 agent「失败收尾前先写 extraction_declaration.json」;headless 下 4.5 两次打回叠加 3 次催办上限,pre_finish 打回文案明写「只需生成声明,不要结束回合」,e2e 用 print 模式实测一次。
- 涉及:§4.5、§10、§11、§12。

**M5. schema 位置与校验方式:schemaRef 读不到;锁定解释器没有 jsonschema;cli 不能依赖 PyYAML**

- 问题:`validateProduces` 只从 `<packageDir>/schemas/<schemaRef>` 读(我自查 validate.ts 29 行),稿子把 schema 放 `sure/runtime/memory/schemas/`,extract_lessons 的 schemaRef 会读空并静默跳过,结构检查只剩 requiredFields。锁里没有 jsonschema(requirements.in 我自查:PyYAML、pydantic、pydantic-settings、rich、structlog、typer),实现时 `import jsonschema` 本机能过、集群 ImportError 让门把所有候选拒掉;`python -s cli.py` 走系统 python3,import yaml 一处就可能跑不起来;validate.ts 不认 schema 的 `const` 只认 `enum`(老 ledger Task 8 M1)。
- 证据:critic-integration §2.14、§2.15;critic-operations §3.6;old-code §2.3、§4;核实清单 F3(integration)、OPS-12。
- 改法:sure_onboard、sure_eval 各在自己 schemas/ 下放一份 `extraction_declaration.schema.json`,以 `sure/runtime/memory/schemas/` 那份为源,§12 vitest 遍历两个状态机每个 unit 的 schemaRef 断言文件存在且两份拷贝与共享库字节相同(顺手补上目前对所有 unit 都缺的 schemaRef 存在性断言);schema 常量用 `enum` 不用 `const`,加 `infra_noise` / `infra_evidence`;proposal / run_digest / meta 的 schema 只当文档与测试夹具,门按 §5.3 手写校验(沿老门 check_memory_proposals.py 252-351);§10 第 343 行改成「只用标准库」去掉 PyYAML(digest 的 target 从 model_input_resolved.json / eval_input_resolved.json 读,不解析 YAML),补「cli.py 及其导入链必须在系统 python3 -s 下能跑」;§12 加「test_*.py 在锁定解释器和系统 python3 -s 下各跑一遍」。汇报时不要说锁里只有 PyYAML;是「有意不用 pydantic」。
- 涉及:§4.1、§10、§12。

**M6. 格子过粗把自动转正堵死;判重只认触发词集合全等;同批候选不判;similar 没挂钩**

- 问题:门 7 让同 target_skill / component / cause 格子(confirmed + provisional 都算)已有条目时 op 必须 modify / supersede,而 §6.2 说这两种只有人 confirm 才生效,于是每格一辈子只能自动学一条,第一条哪怕后来 disputed 也占着;cause 八类是 onboard 口径,eval 的失败(vc 提交被拒、数据集路径、metric 配置)全挤进 infra / config_not_set;老 17 条无 Cell 头,门 7 对它们查不了;判重只拦集合完全相同,多写一个词就绕过,之后两条同时命中各分一半 useful 谁都到不了 K=2;老代码 `_check_dedup` 只跟库里比,同一批 5 个候选之间不比;`similar` 字段(第 203 行)可选且门不查(v1 必填)。HarnessBank 的 why 维度是开放集合。
- 证据:critic-mechanism M-04、M-07;critic-alignment C-11、C-17;prior-research A-3、F-5;ext-experience-research §3.1;ext-memory-libs §5.3;old-code §5.8;核实清单 M-04、M-07、C-11(confirmed)、F-5(prior)、F5(exp)、F3(libs)。
- 改法:门 7 改成:占位只算 status=confirmed 且 superseded_by 为空的条目;格子里只有 provisional / disputed 时允许 op=add,但 `similar.entry` 必须指向占位者且 `similar.difference` 非空(similar 从可选变为「同格冲突时必填」,schema 加条件必填,`similar.entry` 非空时一律校验它在索引里);触发词集合完全相同的 add 照旧拒;候选 trigger 集合与库里同 target_skill 同 component 某条是子集关系或 Jaccard ≥ 0.5 时不拒但要求 similar 指向;同批候选之间同样两两判(proposals.py 主循环先收齐再比,约 10 行);cause 枚举写进 config.json:八类 + infra + 少量 harness 级类(job_submission、resource_limit、data_layout、result_layout、metric_bypass),不改 failure_taxonomy.md,不加 other;§6.4 明写 legacy 条目 cell 为 null 不占位,快照 component / cause 允许 null;§6.2 第 248 行把「生效」写清:modify / supersede 候选照常进索引、注入、计数,只是不改目标条目、不自动转正;目标条目被 reject 后指向它的候选标 orphan,cli list 可见;§7.2 注入排序后若某条的 similar.entry 已在列表里就跳过它;stats 加每格条目数和「格子被 provisional 占着又有 modify 候选」清单;EXTRACTION.md 明说同 component 有触发词重叠时先考虑 modify / supersede 再 add 并填 similar。老门 541-548 占位判断可直接照搬,只改扫描范围。
- 涉及:§5.3 门 7、§6.2、§6.4、§7.2、§9。

**M7. 跨技能路由被 component 过滤打死(决定 8 的后果)**

- 问题:bad_case 的 component 必须是 target_skill 的 unit id,匹配又要求 component == 当前 unit,eval 学到的条目在 onboard 里 unit 名永不相等,跨技能 bad_case 永不注入;两技能同名 unit `plan` 反而串台。
- 证据:critic-mechanism M-05;核实清单 M-05。
- 改法:§5.3 门里补一条:bad_case 的 applies_to 必须等于 [target_skill](或 bad_case 不写 applies_to,只 fact 用),跨技能一律通过 target_skill 表达;§7.2 把 bad_case 的过滤改成「target_skill == 当前技能 且 component == 当前 unit」,fact 才按「applies_to 含当前技能或 _shared」过滤;§7.1 写明 index.md 是全量的、prompt 级路由不按 applies_to 过滤。若以后确需「别的技能的 bad_case 在任意 unit 按触发词提示」,再放宽。
- 涉及:§5.3、§7.1、§7.2。

**M8. 老 17 条的触发词是散文,子串永不命中;段落名不统一,「以库里现有为准」不成立**

- 问题:§6.4 从 README 路由表取老条目触发词,第一列是给人看的整句(我自查 README 10-28 行,如「Model weights exist but wrapper cannot find them; ModelScope path differs from repo id」),子串几乎不可能命中;17 条 confirmed 排最前却几乎零命中,stats 无法解释,新候选可与老条目重复而门不拦。修复段名 Fix Pattern 8 条 / Required Fix 5 条 / Known Mitigation(s) 2 条,8 条超 200 词。
- 证据:critic-mechanism M-09;critic-alignment C-12;ext-experience-research §1;ext-scoring §6.3;prior-research F-8;ext-products F8;critic-integration F12;old-code §5.9;核实清单 M-09、C-12(confirmed)、F4(exp)confirmed、F5(scoring)。
- 改法:§6.4 改成:老条目不再从 README 整句取触发词;由人一次性给 17 个文件补 `Trigger:` / `Cell:` / `Source: legacy` / `Added:` / `Status: confirmed` 五行头(Trigger 从各文件 `## Trigger` 段反引号里挑能在真实报错里逐字出现的短串,如 `no kernel image is available`、`partition not found`、`Can't initialize NVML`、`tp_plan='auto'`,过滤掉 `.`、`!`、纯文件名;Cell 用 Affected Step 对应的 onboard unit id,对不上的用 `_`;asr_metric_bypass 这类没有报错串的 Trigger 留空只走 prompt 级),git 跟踪文件人 commit,符合决定 10;补头之前索引器把没有 `Trigger:` 头的老条目 trigger 记空、component `_`、只进 index.md 标 `[legacy]`,不参与 hook 匹配,stats 显示「无 trigger,不参与注入」而不是 0;门 7 补一句:候选任一 trigger 与某老条目片段相同时必须在 similar.entry 或 covered_by 里点名。§5.1 删「以库里现有为准」,改为「按下表段名;必有 Trigger / Affected Step / Minimum Evidence / Known Mitigation / Verification,Example Artifacts 可选;老条目不回改、不过门」,门 1 按这份清单硬判。§12 索引测试加「老格式条目无 Trigger 头时不出现在 match 结果里」。
- 涉及:§5.1、§5.3 门 7、§6.4、§9、§12。

**M9. digest 取材与成本:目标从产物读、output_dir 泄漏、eval 证据根未定义、20KB 裁剪顺序、log_tail 读法、prior_runs 扫描**

- 问题:§4.3 第 116 行「目标从 args 解析」要解析 handoff YAML,而 onboard 的 `artifacts/model_input_resolved.json` 和 eval 的 `artifacts/eval_input_resolved.json` 里都有;hooks 收到的 args 含 output_dir,digest 放 run.args 就把 output_dir 交给 agent 并固化进条目 Source / evidence,这正是手册说会引发 agent 自作主张搬产物的路;eval 传 output_dir 时产物在仓库外绝对路径下,门规则 2 禁绝对路径且没定义 eval 的目标目录,log_paths.json 若是固定相对路径 eval 的 log_tail 恒为空;20KB 裁剪永远不砍 repairs(24 unit × 3 次 × 600 字符可超 40KB)却先砍最值钱的 fix_window;log_tail 从头读几十上百 MB 的 build.log 在 NFS 上要几十秒且 spawnSync 同步阻塞,`\r` 进度条一行几 MB;events.jsonl 每个 tool_call 存完整 input(write 的整份文件),数 cutoff 要读全文件;prior_runs 扫 `.sure/runs/` 全部目录。
- 证据:critic-operations §3.3、§3.4、§3.5、§3.7;old-code §4;核实清单 OPS-06、OPS-07、OPS-10、OPS-11。
- 改法:§4.3 第 116 行改成 target 优先从两份产物读,args 兜底;digest.py 写 run.args 时按 hooks parseArgs 的切法剥掉 `output_dir=` 与 `--output_dir <v>`(约 15 行标准库,照 output-dir.ts 19-58 逻辑重写并单测),§11 第 384 行改成「目标目录从 runtime.run_dir 取,故无差别」;§5.3 规则 2 把证据根写实:门按顺序解析相对路径,(a) run 根 = `.sure/runs/<run_id>/` 整个目录(让 vc_logs/、local_logs/ 也算),(b) 目标目录:onboard = `sure/models/<model>/`,eval = eval_input_resolved.json 的 runtime.run_dir(门自己读,agent 不需要知道 output_dir);证据仍只许相对路径不许 `..`;log_paths.json 允许 `{run_dir}` 与 `{product_dir}` 两个占位符;老分支 `_resolve_path`(154-204 行)可 copy,把 (target, art) 两个根扩成 (product_dir, run_dir);裁剪顺序改「先缩后删、核心段永不整删」写进 config.json:先 memory_index_snapshot 只留 id/status/cell,再 units_registry 只留当前技能,再 prior_runs 缩到 2 条去掉 candidates,再 log_tail 30 行缩 10 行,再 repairs 每条 600 缩 300,fix_window 10 条缩 5 条放最后,缩到底还超就接受超封顶,§12 加「裁剪后 repairs / fix_window 仍在」;log_tail 从尾 seek 读 ≤64KB,按 `\n` 和 `\r` 切行,取末 30 行,每行 ≤300 字符;prior_runs 倒序扫 `.sure/runs/` 读 run.json 凑够 5 条即停;cutoff 可选用字节偏移代替行数;fix_window 非 bash 工具只记 path。
- 涉及:§4.3、§5.3 规则 2、§10、§11、§12。

**M10. 记忆的血统与证据存活:digest 无本次注入记录、候选无 derived_from、fix_window 与 evidence 只活在 run 目录、Known Mitigation 命令无来源约束、索引依赖 run 目录存在**

- 问题:agent 在 extract_lessons 里看不到本 run 哪条被注入、被打脸,Memp「误导就修订」只做了计数那一半;被注入条目诱导出的新候选没有字段指向源条目,源被 reject 后后代仍在且可靠 useful 转正(VaG 污染链、SkillJack「删源后 80% 攻击仍在」);provisional 只留 entry.md + proposal.json,§11 第 385 行索引器只收 source.run_id 在 `.sure/runs/` 里存在的条目,run 目录清理后条目从索引消失、证据失联;fresh clone 上这条规则若对 confirmed 生效,新 clone 一条 confirmed 都进不了索引;causal 只约束 path:line,不约束 Known Mitigation 里的命令是不是编的或抄自日志里的第三方文本,而注入进 repair 就是指令通道。
- 证据:critic-alignment C-7、C-9、C-10;prior-research A-7、F-4、F-11;ext-experience-research §3.5、§3.1;ext-recent F1、F5、F6;ext-products F4;critic-operations §3.3;核实清单 C-7、C-9、C-10、F-4(prior)、F1(recent)、F6(recent)、F4(products)、OPS-10。
- 改法(全部标准库,不加新门规则的硬拒):§4.3 digest 的 run 段加 `memory_usage: [{entry_id, unit, attempt, outcome: useful|disputed|open}]`,digest.py 从 usage 按 run_id 过滤(hook 已经在写);memory_index_snapshot 每行加 useful / disputed 计数;EXTRACTION.md 取材优先级最前面加一档「本 run 被注入且被打脸的条目 → 提 modify / supersede 指向它,claims 引用它被打脸的那次 gate_repair」(规则 8 已校验 target_entry);publish.py 机器推 `meta.derived_from` = 本 run usage 注入行里 unit 等于候选 claims 里某 unit 的条目 id(agent 不填,proposal.json 不变),cli show 显示,cli reject 打印后代清单只显示不级联;§11 第 385 行改成:索引器对 provisional 只收 meta 存在、meta 记的 entry.md sha256 与文件一致、decisions 有 publish 行的条目;references 下的 confirmed 与 legacy 无条件收,索引不再依赖 `.sure/runs/`,防手放文件目的照旧;§6.2 publish 把 `artifacts/run_digest.json` 拷一份到 `sure/memory/digests/<run_id>.json`(一次 run 一份 ≤20KB),proposal.json 每条 evidence 在 publish 时补记文件 sha256(只记哈希不拷大文件),cli show 对文件不在 / 哈希变了标 missing / changed,不改 status;§3 第 64 行 `{entry.md, meta.json}` 改成和 §6.1 一致的 `{entry.md, proposal.json}`;手册加「`.sure/runs/` 可以删,记忆库不靠它」;publish.py 从 digest 推布尔 `fix_exercised`(候选 cell.component 指向的 unit 在源 run 里 outcome=passed 且 attempts>1),index.md 与注入行对 false 标「[provisional, fix untested]」,不拒任何候选。可选:proposal.json 加 `verified_commands`,门要求逐字等于 digest / events 里某条 command,causal:true 要求非空。
- 涉及:§3、§4.3、§4.4、§6.2、§6.3、§9、§11、手册。

**M11. index.md 与 memory_context.json 无预算;超预算截法与 superseded 状态、老化提示没写**

- 问题:§7.1 让 agent 在 context_selection 处先看整份 index.md,provisional 自动落库后行数只涨不跌,无行数 / 字节上限、无超限报错;README 路由表自动追加。原始材料说无门自动记忆能活的最低补充是常驻索引硬预算 + 超限精简,v2 只做了一半。status 枚举没有 superseded,`list --status confirmed` 会不会列出停用条目没说;provisional 只进不出,meta.last_hit 已有但没有阈值和显示。
- 证据:critic-alignment C-8、C-19;prior-research A-10、§3.3、F-6、F-12;ext-products F1、F6;ext-memory-libs §5.5、§5.6、§5.7;核实清单 C-8、F-6(prior)、F1(products)、F5(libs)。
- 改法:config.json 加 index.md 行数与字节上限(先按 200 行 / 25KB 或 12000 字符),index.py 排序定死 confirmed 在前、provisional 新到旧、disputed 最后,superseded / rejected 不进 index.md,超限只保留最新若干 provisional 行并在末尾写「已省略 N 条,cli list --status provisional 查看」,rebuild-index / stats 打印行数字节数与上限;只提示不报错(pre_start 报错会因整理活挡住整次 run,和第 294、380 行「不阻塞主流程」相反);memory_context.json 条数预算(见 H6 第 4 条);digest 的 memory_index_snapshot 进 20KB 裁剪顺序;index.md 可改 bullet 格式顺带删门 1 的 `|` 禁令(见 §6 给用户选);meta status 加 `superseded`(或 list 默认过滤 superseded_by != null),加 `superseded_at` 系统时间与 `checked_at` 事实时间分开;stats 加「provisional 且 injections=0 的条目数与占比、入库日期」,`list --cold [--days N]`,fact 按 scope 给 `stale_after_days`,索引超龄行带 `[stale]`;只标不删。§8.2 末尾补一句:预防性生效的条目(pre_start 命中或 agent 读索引后没触发打回)不会攒 useful,长期留在 provisional 是预期行为,靠人从冷条目列表里 confirm。
- 涉及:§6.3、§6.4、§7.1、§7.2、§8.2、§9。

**M12. 接入面小坑:state 消息 agent 看不见;写死的单元数与测试目录;schema 记忆位;units.json 里 sure_reval;pre_start 索引由谁建**

- 问题:§4.2 / §7.2 的「state 消息告诉 agent」不成立,state_patch 走 `pi.appendEntry` 是 TUI 专用条目不进模型上下文,post_tool_result ok:true 时工具结果原样返回;插 unit 后两个 vitest 文件写死单元列表和 12 / 22 计数、两份 SKILL.md 单元表带序号、handbook 与 company 文档写「22 个单元」、§12 说的 `packages/coding-agent/test/sure/` 实际是 `test/suite/`;`task_classification.schema.json` 无记忆位且 additionalProperties:false,agent 好心加字段会被门打回烧重试(eval 默认只有 2 次),ROUTING.md 45-56 行示例形状与 context_selection schema 不符;sure_reval hooks 没有状态机、units.json 里它的 unit 列表是空的;§6.4 说 pre_start 检查内容哈希不一致就重建,§10 说索引逻辑只在 index.py,pre_start 要么再起一次 python 要么 TS 里重写索引逻辑。
- 证据:critic-integration §2.5、§2.8、§2.10、§2.15、F9、F11、F13、F14、F15;critic-operations §3.3;核实清单 F9(integration)、OPS-10。
- 改法:§4.2 改成「state_patch 只更新 TUI 显示;agent 靠 EXTRACTION.md 知道去读 artifacts/run_digest.json,若文件只有 {schema, error} 则声明 no_new_lessons: true 并引用该 error」;§7.2 pre_start 改成「只写文件,不通知;SKILL.md 在 context_selection / task_classification 处指示读 memory_context.json」;§10 注明 state_patch 的 message 仅供 TUI,凡要 agent 看到的文字只能走 repair 或落文件。§12 目录改 `test/suite/`,插 unit 后一起改两个测试文件、两份 SKILL.md、handbook 187 行、company_model_onboarding 91 行,整条链的回放测试要补一份能过门的声明并让 digest 建得出来。§7.1 明写 eval 不往 task_classification.json 里加字段,onboard 用 `selected_references.memory` 并改 ROUTING 示例。units.json 对 sure_reval 给空表,reval 的 component 只能是 `_`。§6.4 第 266 行加「pre_start 的索引检查在 resolveHarnessPython 成功之后由 index.py --check 完成;TS 不算哈希、不解析条目文件,只读 index.json」;fresh clone 上这次 spawn 和首跑物化在同一次 pre_start 里,手册说明第一次会多几秒。§10 memory 目录根从 `repoRootForPackage(ctx.packageDir)` 推,不用 cwd。
- 涉及:§4.2、§6.4、§7.1、§7.2、§10、§12。

**M13. 相对 v1 的改动没写明;§13 依据表有几处引证对不上**

- 问题:v1(师兄 8-12 认可)的链是「规则验证 → 人工审批 → 版本化知识库」,人工审批在入库前;v2 变成「自动入 provisional 并被下次 run 消费 → 有用计数自动进 references → 人 commit」,§13 只标「折中」,没有一句说这是对四层里治理层顺序的改动。另有:正文 120 → 200 词且「策略级、不写单模型数值」要求消失;一条一 commit 可单独回退丢失;adopt 收尾跑 check_experience_assets.py 不再调;references 写入不再天然串行;similar 从必填变可选。§13 里「Cursor / Windsurf 无门自动记忆之死」不准确(Cursor 1.2 上线一个月加了人审,2.1 整功能下架且官方未给原因;Windsurf 是新默认 agent 不再持久化);「HIT 行动偏置」不支撑「按有用不按读到」(HIT 讲的是成功经验多了 agent 更爱干,ACE helpful/harmful 计数才是先例,MemRL 是最近同款);Memp「关键词赢嵌入」是间接支持(AveFact 仍是嵌入);Live-Evo 在调研里定位是人工门之后;触发词逐字更贴的依据是 Honest Lying(程序化失败信号 RRR 0.64 → 0.10)而不是 Raven Gate-b;K=2 与一次冻结没有任何外部出处(有具体 K 的只有 HarnessBank K=3 加配对显著)。
- 证据:prior-research D 节与 F-7、F-10;ext-products F11;ext-experience-research §2.4、§2.8;critic-alignment §4、C-20;核实清单 F-7(prior)。
- 改法:设计稿第 5 行「继承 / 放弃」那句后加一段「相对 v1 的改动」六条(治理时序并注明需向师兄说明一句;词数 120 → 200 及理由;「策略级、不写单模型数值」写进 EXTRACTION.md;不再调 check_experience_assets.py 及原因,cli reject 遇被 playbook 引用的 legacy 条目先警告;commit 粒度由人掌握,cli confirm / reject 输出建议的单条 git 命令;并发);§13 按上面逐条改引证;§14 6b 保留「规则自定」,汇报时照实说。
- 涉及:§0、§13、§14。

---

## 3. 可以直接拿来用的

约束:门禁 / helper 脚本跑在锁定 Harness Runtime 下,按设计稿口径只用标准库(锁里其实还有 pydantic、typer、rich、structlog、PyYAML,但有意不用);hooks 是 Node 下的 TS,根 package.json 无运行时依赖,加依赖要强理由;只从 MIT / Apache-2.0 / BSD 抄。形式:copy(小函数或文件带出处贴)、dependency(装包)、format(借文件 / JSON / 协议形状)、reference(只借思路)。许可证是各报告核的,我没重开。

### 3.1 老分支与主线代码(同一仓库,无外部许可问题)

| 名字 | 来源与许可 | 形式 | 拿来干什么 | 可行性 |
|---|---|---|---|---|
| check_memory_proposals.py 字段清洗 / 证据 / 判重小函数:interpolation_problem、count_body_words、parse_entry_headers、_parse_evidence_ref、_is_unsafe_evidence_path、_is_single_name、_resolve_path、resolve_entry_path、provenance_line_in_body、_check_evidence、_check_causal | `D:/sure/dev/sure-harness/sure/skills/sure_check/scripts/check_memory_proposals.py` 58-80、83-102、105-137、140-225、354-373、399-426、456-480(old-code §2.1) | copy | 进 `sure/runtime/memory/proposals.py` 与 index.py,服务 §5.3 规则 1/2/6/8 与 §6.4 读头;只改常量(200/60 词、Status: 前缀)和 base 目录来源 | 纯标准库,直接进锁定解释器。`_check_dedup`(495-563)按 M6 要重写,不要原样搬;`_resolve_path` 词法收容不 resolve 是为集群模型目录软链,保留 |
| check_memory_proposals.py 声明一致段与 dedup 骨架 | 同上 495-563、610-692 | copy | 规则 10 与规则 7 骨架;需补 ≤5、`no_new_lessons:false ⇒ candidates 非空`,数据源换 index.json | 同上 |
| check_memory_proposals.py 手写 schema / 枚举 / 路径校验 | 同上 14-19、252-351、610-630 | copy | 门规则 1、2、8、10 的实现,不依赖 jsonschema | 正是 M5 要的做法 |
| adopt_memory.py 治理 CLI 写盘部分:AdoptError 模式、_skill_dir / _bad_cases_dir 路径收容、_read_text、_split_proposal_md、_slug_from_h1(改中文退化)、_check_interpolated、_provenance_lines、_read_entry_parts、README_BOOTSTRAP + _append_route_row、_repoint_route_rows、_append_decision、_print_git_suggestion、cmd_compare、_adopt_add / _modify / _supersede、cmd_reject | `.../sure_check/scripts/adopt_memory.py` 60-165、282-301、304-440、478-487、529-682(old-code §2.2) | copy(改后) | 进 cli.py 与 promote.py;候选布局、STALE、experience guard 那部分弃;`_print_git_suggestion` 给 M2 的 `cli export` 用 | 里面 import subprocess,搬过来时确认没有 git 调用;`_slug_from_h1` 对全中文 H1 会 raise,按 §6.2 退化规则改;`_cell_str` 补 dict 校验(老 ledger 145 行) |
| extraction_declaration.schema.json | `.../sure_check/schemas/extraction_declaration.schema.json` | format | 五键 + additionalProperties:false 的声明 schema | 常量用 `enum` 不用 `const`(validate.ts 不认 const);加 infra_noise / infra_evidence;每技能 schemas/ 放一份拷贝(M5) |
| test_memory_proposals.py / test_adopt_memory.py 夹具与用例 | `.../sure_check/scripts/test_memory_proposals.py` 28-172 及 old-code §2.4 列出的用例;`test_adopt_memory.py` 32-166 及 §2.5 列出的用例 | copy(改后) | 临时 run dir / skills_root 夹具、argv patch + 重定向跑 main、README / legacy / machine 条目夹具、路径逃逸 / 字符清洗 / never-crash 一族 | 把 check_context / summary / report 三份夹具换成一份假 run_digest.json;缺规则 3、4、9、≤5、fact、段落标题的用例 |
| finalize_check.py 目的地收容与 preflight / rollback:_is_unsafe / _resolve_within / _is_redirected / _has_trailing_junk;_dest_problem / _mkdir_tracked / _rollback | `.../sure_check/scripts/finalize_check.py` 45-171 | copy / reference | publish.py 写 provisional 前的目的地检查;多文件写盘的回滚参考 | 候选发布段本身(317-404)与 check_id / report_dir 绑死,不带 |
| 老 hooks/index.ts 收尾辅助:invalidDeclaredArtifacts、nonSuccessFinish、preFinish 顺序;check-hooks-finish.test.ts | `.../sure_check/hooks/index.ts` 1177-1303、1312-1385、1387-1493;`packages/coding-agent/test/sure/check-hooks-finish.test.ts` | reference | §4.5 pre_finish 三次逻辑与 `extraction: failed` diagnostics 写法;preFinish 的 vitest 夹具骨架 | 代码绑 CHECK_FLOW_UNITS 不 copy |
| 主线 checkpoints.ts / validate.ts / runBackend:readCheckpoint、advance、bumpRetry、retryExhausted、artifactPath、readArtifact、runBackend、failure;validateProduces | `D:/sure/dev/sure-memory/sure/skills/sure_onboard/hooks/checkpoints.ts` 77-263 与 sure_eval 同路径;`hooks/validate.ts` 133-270 | dependency(直接 import) | v2 hooks 改动全部建立在这些函数上;runBackend 自动补 --run-dir、cwd=packageDir、env 带 HARNESS_PYTHON_BIN;build_run_digest / publish / promote 复用同一 spawn 形状 | CheckpointData 需扩字段(H2);publish 走 runBackend 之外单独 spawnSync 给 60 秒超时 |
| 主线 python 小工具:_canonical_json、_atomic_write(带 fsync)、_write_atomic、_sha256、slugify_model_name | `sure/skills/sure_eval/scripts/run_reval.py` 128-129、145-153;`finalize_result_bundle.py` 25-28;`evaluation_runtime.py` 31-32;`sure_onboard/scripts/materialize_onboard_inputs.py` 72-76 | copy(几行) | publish / meta / index 的原子写、digest 哈希前规范化、目标 id 归一化 | 主线已有带 fsync 的原子写,不用抄 hermes 也有得用 |
| bootstrap.py `_apply_acl` / `_make_group_writable`;`.bootstrap.lock` 的 fcntl.flock 用法 | `sure/runtime/harness/bootstrap.py` 54-94、435-449 | copy | M1 记忆目录组可写 + setgid + setfacl;M2 / M3 / H5 的 flock | 纯标准库,已在这块共享盘上跑过,手册 80 行说并发排队实测过 |
| harness 运行记录形状:`.sure/runs/<id>/{run.json,state.json,events.jsonl,artifacts/}`;events 类型 tool_call(data.input.command)/ tool_result(只 isError)/ tool_result_repair(整个 gate 结果)/ post_tool_result_state / finish_repair / finished / session_shutdown | `packages/coding-agent/src/core/sure/run-manager.ts` 61-183;`extension.ts` 700-796、862-971;`hook-types.ts` 60-78 | format | digest.py 的唯一取材依据;证实 §4.3「先挂后过」窗口可从 events 复原 | 既定格式,读者要跳坏行 |
| failure_taxonomy 八类 | `sure/skills/sure_eval/references/failure_taxonomy.md` 5-143 | format | 与老门 CELL_CAUSES 逐字一致,加 infra 与 n.a. 即为 §5.3 cause 枚举;M6 建议再加少量 harness 级类进 config.json | |
| splitOutputDir / stripOutputDir | `packages/coding-agent/src/core/sure/output-dir.ts` 19-58 | reference(Python 重写) | M9 digest / memory_context 里剥 output_dir | 十几行逻辑,重写并单测比跨语言调用简单 |
| 老 hooks preStart spawnSync 用法;eval preStart 的 spawnSync + harnessRuntimeEnv | `.../sure_check/hooks/index.ts` 112-131;`sure/skills/sure_eval/hooks/index.ts` 188-193 | reference | pre_start 建索引可照抄;进 unit 时跑脚本没有先例,得在 advance() 之后新写 | |

### 3.2 外部项目

| 名字 | 来源与许可 | 形式 | 拿来干什么 | 可行性 |
|---|---|---|---|---|
| OpenHands `_keyword_matches` | OpenHands/software-agent-sdk main 98338ff,`oh_skills_skill.py` 164-175(ext-products §2.7 本地副本);MIT | copy | 字母数字边界整词匹配正则 `(?<![a-z0-9])kw(?![a-z0-9])`,Python re 与 TS RegExp 各几行 | 合。但 H6 加了长度下限后它只解决 `git` ⊂ `github` 型误伤,不是主药;若采纳,python 门与 match.ts 各写一份并进共享测试向量 |
| OpenHands `_truncate_top` / `load_memory` | 同仓库 `oh_context_memory.py` 38-97;MIT | copy | 按字符预算从顶部整行截断 + 多层公平分配 | 合。给 M11 index.md 预算用,我们砍 provisional 行优先则顺序反过来,逻辑同 |
| OpenHands 会话内去重 activated_knowledge_skills;`<EXTRA_INFO>` / `<UNTRUSTED_CONTENT>` 免责措辞;skill frontmatter `triggers:` 列表 | `oh_state.py` 129-147、`oh_local_conv.py` 1846-1861;`oh_context_prompts_templates_skill_knowledge_info.j2`、`oh_dynamic.py` 84-95;`oh_legacy_github.md` 1-8;MIT | reference / format | H5 第 2 条的去重表思路;注入块措辞(provisional 行再重一档「agent-written, not human-reviewed」);frontmatter 给用户选 | 合 |
| TencentDB `applyRecallBudget` + `truncateRecallLine` + `normalizeBudgetLimit` | TencentCloud/TencentDB-Agent-Memory main 3f11f6bf,`src/core/hooks/auto-recall.ts` 708-790;MIT(LICENSE 原文,GitHub API 报 NOASSERTION) | copy(TS) | 逐条上限、总上限、剩余不足就丢、按 code point 截、记截断数,约 80 行标准 JS | 合。放 match.ts,补 H6 第 6 条的超预算规则;配置键名 inject_max_entries / inject_max_chars_per_entry / inject_max_total_chars 照它起(format) |
| TencentDB `extractWords` | 同仓库 `src/utils/text-utils.ts` 11-32;MIT | copy(TS) | 拉丁 ≥2 字符 + CJK 切词 | 合但窄:只给 pre_start 用 args 匹配 fact 时用;H6 第 4 条改成按 scope 机械匹配后价值更低,可不用 |
| TencentDB 注入前剥掉自注入块 | 同仓库 `src/utils/sanitize.ts` 15-19;MIT | reference | H6 第 2 条的依据(「prevent feedback loops」) | |
| hermes-agent `atomic_write_text` / `atomic_replace`、`_file_lock`(fcntl / msvcrt)、`skill_ledger.append_entry` / `list_entries` | NousResearch/hermes-agent main acc614e,`utils.py` 194-345、`tools/memory_tool.py` 280-313、`tools/skill_ledger.py` 163-193、234-262;MIT | copy(Python,标准库) | 原子写含 Windows 句柄占用重试与 EXDEV 回退;双栈锁;jsonl append + 读时跳坏行 + 写失败只 warning | 合但增量小:主线已有 `_atomic_write`,hermes 多的是 Windows 重试(本机单测)与坏行容错;锁在 NFS 跨节点不保证,M2 改整表对账后锁不再必需 |
| hermes 超上限报错不截断 + 回现状 + 限重试;sidecar 使用计数与 stale / archived 生命周期 | `memory_tool.py` 160-198、421-441;`tools/skill_usage.py` 1-23、644-661;MIT | format / reference | 抽取门 repair 文本的回复形状(候选超 5 条、正文超词数);印证 §6.3 计数只在 meta;M11 老化标记思路 | |
| Anthropic SDK `_validate_path` 写法 | anthropics/anthropic-sdk-python main ad53cac,memory 示例实现 387-401(ext-products 本地副本);MIT | copy(十几行,或按写法自己写) | resolve 后 startswith(root+sep) 再查 symlink 逃逸,标准库 pathlib | 合但要分目录:run artifacts 用 resolve;集群模型目录按老分支「词法收容不 resolve」保留软链 |
| projectmem `superseded_ids()` / staleness.py 写法 | github.com/riponcm/projectmem `src/projectmem/models.py`、`staleness.py`(gh api 2026-08-18:MIT,733 star) | copy(带署名) | 读时算被取代集合;「引用文件不存在 = 最强过期信号,判不了返回 None 而不是判过期」 | 合,标准库几十行,进 index.py / cli.py;它按 git log 计数那半不适用 |
| Claude Code MEMORY.md 索引与预算;记忆文件 frontmatter name / description / type / modified;/doctor 精简规则 | code.claude.com/docs/en/memory(2026-08-18);本机 `~/.claude/projects/D--sure/memory/*.md` | format | bullet 一行一条 + 200 行 / 25KB + 写后自检超限报错;`modified` 自动盖 = checked_at 做法;精简原则进 EXTRACTION.md | 合,M11 |
| ACE playbook 三档统计口径 | github.com/ace-agent/ace `playbook_utils.py` 218-255;Apache-2.0 | format | high_performing(helpful>5 且 harmful<2)/ problematic / unused 分档 | 合,给 cli stats;不抄行解析代码 |
| Codex raw_memory `task_outcome` 枚举、MEMORY.md `applies_to; reuse_rule` 一行;Phase 2 合并 agent 约束(无网络、只本地写、全局锁、git 基线 diff) | openai/codex main 0acf302,`codex_stage_one_system.md` 401-440、`codex_consolidation.md` 201-260、`codex_memories_README.md` 84-127;Apache-2.0 | format / reference | 字段名;预留的记忆管理 agent 蓝本 | 合,低价值可选 |
| Windsurf `trigger:` 四值 / Kiro `inclusion:` 四值 / Cursor description-globs-alwaysApply | 各文档(2026-08-18) | format | meta 可加 `load: gate_repair | pre_start | index_only` | 可选 |
| mem0 history 表行形状(memory_id, old, new, event, created_at, actor_id) | mem0ai/mem0 001c235,`mem0/memory/storage.py` 66-80;Apache-2.0 | format | decisions.jsonl 每行带 old / new / event / actor(auto|human) | 合 |
| Graphiti 双时间戳(valid_at/invalid_at vs created_at/expired_at);MemOS status 枚举(activated|resolving|archived|deleted)与 evolve_to | getzep/graphiti 96ef997 `graphiti_core/edges.py` 262-281;MemTensor/MemOS 7f80d13 `src/memos/memories/textual/item.py` 49-172;Apache-2.0 | format | meta 加 `superseded_at` 与 `checked_at` 分开;加 `superseded` 状态 | 合,字段活(M11) |
| ReasoningBank 条目三段(Title / Description 含「何时不用」/ Content)与「禁止嵌字面串」;TF-GRPO 操作枚举 ADD / UPDATE / DELETE / NONE;Living-Harness 三段式;微软行为规则头 Traced To;TRIAGE 合同四字段;EvoMem 记忆卡字段;ai-memory tags 与 helpful/stale/wrong 反馈词;vectr triggers 数组形状 | google-research/reasoning-bank(Apache-2.0);TencentCloudADP/youtu-agent `utu/prompts/practice/experience.yaml` 190-212(MIT);arXiv 2607.26598、2607.13091、2608.10178、2608.10795;akitaonrails/ai-memory(MIT,Rust);swapnanil/vectr(MIT) | format | 六段式加「何时不用」;触发词禁字面值规则的出处;字段名对照 | 全是形状,无代码可抄 |
| Drain3 掩码正则(ID / IP / SEQ / HEX 四条) | github.com/logpai/Drain3 `examples/drain3.ini` [MASKING];MIT | copy | 若做归一化掩码,掩掉哈希、地址、长十六进制 | H6 第 5 条按核实清单选了「不做归一化、不做掩码」,这条暂不用;若以后改主意,别拿它的 NUM 正则(会掩掉 12.8、exit_code=1) |
| difflib.SequenceMatcher | Python 标准库 | dependency(自带) | 门里判两条触发词是否近似同一句(ratio ≥ 0.9),补充 M6 判重 | 合,零成本,只在 python 门用 |
| Raven Gate-b / HarnessBank 激活门 / RoMeRL trap / Memp Adjustment / SkillAudit 两轮窗口 / ReMe 比例规则 / EA-Graph 哈希锚定 / VaG 污染链 / AuthMem 权限标签 / Honest Lying / AutoGuide 分叉点 / 2605.23899 高效用特征 / ε-MemEvo 注入门 / MemClaw 四种失败 / SAP 共享记忆 / IBM recovery tip / Agent Memory Distillation 被动注入 / OpenClaw 25% 安全阀 | 各论文与仓库(见 §5) | reference | H5 激活信标与不同 run 才算两次、M10 血统与证据哈希、M11 stats 比例、H6 触发词禁实例 token、注入预算依据 | 全是思路无代码;ε-MemEvo 是 CC BY-NC-ND 连文字都不能抄;OpenClaw 许可未核 |

### 3.3 看着能用其实不行的

- mem0 `DEFAULT_UPDATE_MEMORY_PROMPT`(prior-research 标 copy,「唯一值得原样抄的」):它是对话事实记忆的 ADD / UPDATE / DELETE / NONE 管理提示词;我们的 EXTRACTION.md 要教的是对着 digest 和 memory_index_snapshot 给证据、选 add / modify / supersede / covered_by,措辞必须重写,只能借「先列现有条目再逐条判操作」的结构。降为 reference;youtu-agent 的操作枚举已经对齐,不用再抄一份。
- ExpeL `update_rules` / `parse_rules`(ext-experience、ext-scoring 标 copy):两份报告自己都说计数来源是 LLM 投票,我们是门禁结果,抄不上;能借的只有「新条目容错一次」的直觉。reference。
- MemoryOS `compute_time_decay`(ext-scoring 标 copy):十行 `math.exp`;设计稿 fact 不衰减、过期由人 supersede,只有 stale 显示用得上,自己写一行;ext-memory-libs 把 MemoryOS 整体列为反例。reference。
- ACE Curator 合并函数(prior-research 说「可能 copy」,未打开源码):ext-experience 打开了,开源 Curator 只实现 ADD,UPDATE / MERGE / DELETE 全是 TODO,没有可抄的合并函数。
- vectr `trigger_engine.py`(ext-recent 标 format + reference):2 star 单作者、972 行 Python、依赖它自己的 config,我们匹配在 TS;账本思路已由 OpenHands 去重表覆盖。只当 reference,汇报时别当主要依据(它的实测是一个 114 轮的案例)。
- Drain3 掩码正则(critic-mechanism 标 copy):前提是做归一化掩码,核实清单选了不做;若做,它的 NUM 正则会误伤,时间戳、run id、绝对路径掩码要自己写。整个 Drain3 包依赖 jsonpickle / cachetools 不能进锁定解释器也不需要。
- python-frontmatter(ext-products 标 dependency 后自己否了):锁定解释器没有;真要 frontmatter,PyYAML 手写十行。
- npm `safe-regex` 一类正则安全检查(critic-mechanism 提):要加 Node 依赖,v1 直接禁 `re:` 更省事。
- OpenViking(字节):AGPL-3.0,禁止抄代码;L0 / L1 / L2 分层思路设计稿已有(index.md 一行 → entry.md)。
- ε-MemEvo:CC BY-NC-ND,连文字都不能抄,只作决定 7 / 11 的旁证。
- Letta / LangMem / cognee / Memobase / MIRIX / A-MEM / Mem0 整包 / TencentDB 整插件 / agentmemory(rohitg00)/ ReMe 包:依赖重或要起 server + LLM,不装;各报告一致。
- Codex `<oai-mem-citation>` / citations.rs:Rust,不能直接用;逻辑几十行自己写。
- 「老分支没有原子写」(ext-memory-libs)与「主线 run_reval.py 已有」(old-code)两句都对,一个说老分支一个说主线;结论是不用抄 hermes 也有得用。

dependency 一栏:python 侧无需任何 pip 包,TS 侧无需任何 npm 包(十一份报告一致)。ext-recent 提到 Node 22 自带 `node:sqlite`,以后 usage 大到线性扫描吃力时可以不加依赖上 FTS,现在不需要。

---

## 4. 参考材料对照

来源:`D:/sure/参考/2026-08-11-agent记忆系统同类项目调研.md` 第 3 节 12 条建议与 `2026-08-13-记忆调研-十大关键文献.md`(prior-research 的 A 表为底,按其他报告与核实清单修正)。状态:覆盖 = v2 有对应机制;部分 = 有但缺关键一角;漏 = 没有;相反 = v2 反着做。

### 4.1 调研第 3 节 12 条建议

| # | 建议 | v2 状态 | v2 对应处 | 缺口 / 要不要补 |
|---|---|---|---|---|
| 1 | 抽取步是状态机必经态,绿跑也不能跳 | 覆盖 | §4.1、§4.5 | 成功路径连败卡死是实现后果(H1),补;没走到 finish 的 run 抽不到(M4),写明前提 |
| 2 | 候选以受限操作提交,先和最相近条目查重,人审 diff | 部分 | §5.3 op、covered_by、门 7、§9 compare | 判重只认集合全等,similar 从必填变可选且门不查(M6),补 |
| 3 | 条目 = 触发 + 正文,正文短、策略级;两维格子,同格只留最好一条 | 部分 | §5.1、§5.3 cell、门 7 | 词数 120 → 200 且策略级要求消失没写明(M13);格子太粗每格只能自动学一条(M6);老 17 条无格子(M8)。补 |
| 4 | 入人工门前两道免费机械门:有效性、触发确认 | 覆盖 | 门 2、3、4、5 | infra 从按 taxonomy 判改成 agent 自报 + infra_evidence,弱一档但 §4.4 写明了 |
| 5 | 来源可溯;原始报告原样归档,库里放蒸馏 | 覆盖 | source、Source: 头、run 目录 | 索引器依赖 run 目录存在,清理后条目消失(M10),补 |
| 6 | 原始事故记录 + 蒸馏教训两粒度都留 | 部分 | run 目录 + provisional | provisional 只留 entry.md + proposal.json,run 清理后退化成一粒度(M10),补 digest 拷贝与 evidence 哈希 |
| 7 | 修正优于追加:误导一次就提修订,作废用 supersede 不删 | 部分 | §8.2 supersede、disputed 冻结 | digest 无本次注入 / 打脸记录,agent 提不出 modify(M10),补 memory_usage 与取材优先级 |
| 8 | 对比式抽取:先挂后过对比 | 覆盖 | §4.3 fix_window、§4.4 | 无 |
| 9 | 防编故事:能定位坏在哪才许写因果 | 覆盖 | 门 6 | Known Mitigation 命令无来源约束(M10 可选项);抽取合同写「只能从命令差异推」 |
| 10 | 常驻文件硬预算,超限报错不静默截断;永不 LLM 整体重写 | 部分 | index.md 脚本生成 | index.md 与 README 无预算(M11),补;README 改幂等对账(M2) |
| 11 | 注入从简,一次 0-2 条 | 覆盖(hook 路)/ 部分(prompt 路) | §7.2 | prompt 路见 10;超预算截法没写(H6 第 6 条) |
| 12 | 库要有负面教训配平;promote 决定权在人手上 | 覆盖 | 只有 bad_case + fact | 引证 HIT 对不上(M13) |

调研「以后再说」四条:计数升降级被 v2 拉进来当转正门(有意,§13 标折中,但要在「相对 v1 的改动」里写明);定期无记忆对照跑、带 / 不带 A/B、整理巡检 diff 三条 v2 没做,第一版维持不做(§6)。调研「明确不做」四条(质量分当门、向量库、嵌入、自动改 playbook / SKILL.md)v2 全部一致。

### 4.2 十大关键文献

| 文献 | 十大里说拿了什么 | v2 状态 | 说明 |
|---|---|---|---|
| 1 TF-GRPO | 抽取挂验证步 | 覆盖 | 锚点换成门禁事实 |
| | 库操作限四种可审 | 覆盖 | add / modify / supersede + covered_by |
| | 长度上限 + 策略级(它 32,我们 120) | 部分 | v2 是 200,策略级要求没了,变动没写明(M13);另注意它只在组内有成有败时抽,我们成功 run `no_new_lessons` 会是常态,EXTRACTION.md 明写别硬凑 |
| 2 HarnessBank | 格子一格一条 | 覆盖,有粒度问题 | why 维度是开放集,我们八类枚举(M6) |
| | 无门回路噪声积累,必须设门 | 覆盖 | 十条机械门 |
| | 显著性检验跑不起,人工门是正当替代 | 相反(v2 有意) | v2 用 K=2 当转正门,人工门退成事后否决;它的 false elites 教训正是 H5 搭便车,补激活信标与不同 run |
| 3 Memp | 两粒度都存 | 部分 | M10 |
| | 误导一次就修订不堆新条 | 部分 | M10 |
| | 关键词检索赢嵌入 | 覆盖 | 引证过头:AveFact 仍是嵌入,只是键换关键词;MOOSEDev(2608.13662)倒是「supersede 类查询向量 top-k 只找回 6-27%」的直接证据 |
| 4 Devin | 机器提议、人拍板 | 部分 | cli confirm 保留,自动转正绕开 |
| | git 一条一 commit 可单独回退 | 漏 | promote 批量落文件人一次 commit;M2 的 cli export 单条打印 git 命令即可补回 |
| 5 Cursor / Windsurf | 入库门必须是人 | 相反(v2 有意) | 表述要改口(M13):Cursor 1.2 加了人审,2.1 下架无官方原因 |
| 6 TencentDB | private 升 team 过人审 | 部分 | 同 4 |
| | 强制归档 | 覆盖 | 它自己仍在 roadmap,§13 已写 |
| | 召回硬预算 | 覆盖 | §7.2;超预算截法与 sanitize 剥自注入块两条要补(H6) |
| 7 hermes | pending / diff / approve 样板 | 覆盖 | list / show / compare / confirm |
| | 30 天未用标记、90 天归档不删 | 漏 | M11 只标不删,补 |
| 8 ACE | 永不整体重写 | 覆盖 | 逐条文件、索引脚本生成 |
| 9 Reflexion / Honest Lying | 定位不到就只记现象 | 覆盖 | 门 6;Honest Lying 更该当「触发词逐字」的依据(M13) |
| 10 HIT | 负面配平、promote 在人手 | 覆盖 | 引证 HIT 行动偏置对不上(M13) |
| 备选 ExpeL | 配对对比 | 覆盖 | fix_window |
| 备选 Raven Gate-f / b | 两道机械门 | 覆盖 | Gate-b「真触发才记功」在 useful 侧没做,H5 补 |
| 备选 Zep | 取代不删 | 覆盖 | §8.2;双时间戳字段可借(M11) |

无门自动记忆能活的四样「最低补充」(原始材料 249 行:常驻索引硬预算、超限强制精简、时间戳、全部可见可编辑)对照:时间戳有、可见可编辑有、注入侧预算有,索引预算无、超限精简无、老化无。M11 补前两样,老化只标不删。

---

## 5. 新调研发现

8-11 调研之后的新东西(全部 2026-08-18 由各报告抓取,我没重开;方向上没有一篇和主线相反,五篇独立工作分别验证了主决定):

- Delivery, Not Storage(arXiv 2607.20972,2026-07-23):第二层记忆必须由 harness 按确定性触发条件投递,agent 114 轮主动读记忆 0 次;印证决定 9;它的「已触发账本」对应 H5 第 2 条。https://arxiv.org/abs/2607.20972 ;代码 https://github.com/swapnanil/vectr(MIT,2 star,分量轻)。
- PROJECTMEM(arXiv 2606.12329,2026-06-10):append-only events.jsonl 纯函数 fold 出摘要,动手前拦,过期由人判工具只标;印证决定 12 与 supersede 不删。https://arxiv.org/abs/2606.12329 ;https://github.com/riponcm/projectmem(MIT)。
- 微软行为规则闭环(arXiv 2607.13091,2026-07-13):人判「会不会再犯」才入库,字段表与五行溯源头几乎重合;单团队自报。https://arxiv.org/abs/2607.13091
- IBM Trajectory-Informed Memory(arXiv 2603.10600,2026-03-11,8-11 漏了):recovery tip 就是 fix_window。https://arxiv.org/abs/2603.10600
- Agent Memory Distillation(arXiv 2608.07169,2026-08-07):工具报错时按报错函数名查条目塞回,与 post_tool_result 注入同构。https://arxiv.org/abs/2608.07169
- Living-Harness(arXiv 2607.26598,v2 2026-08-11):触发条件 / 失败模式 / 恢复动作三段 + 五道入库门;仓库为空。https://arxiv.org/abs/2607.26598
- When Self-Evolution Backfires(arXiv 2608.05810,2026-08-06):无门累积有相变(35 条 48% → 105 条 62% → 179 条 50%),坏条目被注入后成为后续抽取材料形成污染链,事后删源头只追回 12.3 里的 1.7;M10 血统字段的直接依据。https://arxiv.org/abs/2608.05810
- GRASP(arXiv 2605.29668)/ SkillBoost(arXiv 2607.26643):候选只有在留出探针集上净改善才收,没验证门时「写技能不比不写强」;我们跑不起探针,人工门 + 有用命中是替代。https://arxiv.org/abs/2605.29668 、https://arxiv.org/abs/2607.26643
- The Blind Curator(2607.07436)/ Ratchet(2605.22148)/ Memory Reward Inflation(2608.00017):退役 / 打分靠 LLM 裁判就会坏,只有验证器式打分保得住;支持决定 7、11,但 useful 侧同样有 false-pass(H5)。https://arxiv.org/abs/2607.07436
- RoMeRL(arXiv 2608.02508,2026-08-03):memory-reward trap 定义,共检索记忆平摊奖励;Cold-Q 29% → 45% 零反馈条目;H5 与 stats 零结算占比的依据。https://arxiv.org/abs/2608.02508
- Demystifying Agent Skills(2608.14036)/ 138K SKILL.md(2608.08453):池子 5 → 100 精度掉,91.8% 有缺陷主因路由元数据弱正文臃肿;支持注入 ≤2、按 unit 过滤、≤200 词。https://arxiv.org/abs/2608.14036 、https://arxiv.org/abs/2608.08453
- One Recipe, Many Harnesses / TRIAGE(arXiv 2608.10178,2026-08-10):可证伪合同四字段,memory lessons 要先滤掉实例专属 token;H6 第 3 条依据;仓库 404。https://arxiv.org/abs/2608.10178
- EA-Graph(arXiv 2608.04278,2026-08-04):claim 锚哈希,新鲜度与强度分开,找不到替代就判不可证;M10 evidence sha256 的依据。https://arxiv.org/abs/2608.04278
- TRACE(2606.13174):Mem0 存了偏好照样 57.5% 违反,「存了 ≠ 遵守」;支持决定 9。https://arxiv.org/abs/2606.13174
- MOOSEDev(2608.13662):supersede 类查询向量 top-k 只找回 6-27%;呼应不上向量库。https://arxiv.org/abs/2608.13662
- Git 是记忆方案(2607.14390):支持决定 10。https://arxiv.org/abs/2607.14390
- ε-MemEvo(arXiv 2608.12522,2026-08-12,CC BY-NC-ND):注入 → 窗口后看有没有涨 → 记奖励;总是注入会灾难性失败,门自动退回跳过;决定 7 / 11 与注入预算的旁证。https://arxiv.org/abs/2608.12522
- EvoMem(arXiv 2608.10795,2026-08-11):先挂后好对比抽取,卡带 provenance 与 usage statistics,注入是建议不是约束。https://arxiv.org/abs/2608.10795
- MemClaw(2606.24535)、Governed Collaborative Memory(2605.04264)、MATM(2606.19911)、SAP 共享组织记忆(2608.00122):多实例共享的失败清单与治理制度,决定 1 排后的事,以后用。https://arxiv.org/abs/2606.24535 、https://arxiv.org/abs/2608.00122
- SkillJack(arXiv 2608.03509,腾讯 AI-Infra-Guard)与 PoisonedEvolution / TBA / EvoBreak / MemCollusion / Cross-Session Stored Prompt Injection(2608.05563 等):攻击者不用碰库,只要日志里反复放「条件-动作」对提升机制自己会收进去,删源后 80% 仍在;M10 可选项 verified_commands 的依据。https://arxiv.org/abs/2608.03509 ;https://github.com/Tencent/AI-Infra-Guard(Apache-2.0,SkillJack 目录内容未打开)。
- AuthMem-Bench(2608.01679):记忆整合丢来源权限,49 配置 48 出现;advisory 头与 [provisional] 标记方向对,再加一句「agent 写的,人没看过」。https://arxiv.org/abs/2608.01679
- ReMe(阿里,arXiv 2512.10696 v2 2026-04-15,ACL 2026 Findings;https://github.com/agentscope-ai/ReMe Apache-2.0):唯一和「按命中升降」直接可比的公开规则(f ≥ 5 且 u/f ≤ 0.5 删);stats 该算 useful / injections 比例。8-11 调研说阿里没有,不准。
- OpenViking(字节火山引擎,https://github.com/volcengine/OpenViking AGPL-3.0,28,976 star;VikingMem arXiv 2605.29640):L0 / L1 / L2 分层加载;8-11 说字节没有带机制文档的经验库,不准;AGPL 禁抄代码。
- DeepSeek Harness(https://github.com/deepseek-ai/deepseek-harness MIT,2026-08-13 公开):append-only 事件日志、压缩遮蔽不删、三态 surface;没有教训记忆,只是以后 events 压缩的参考。源码未打开。
- rohitg00/agentmemory(Apache-2.0,27,128 star):无门自动记忆 + 向量 + 衰减,我们明确不做的那种;有 pi 扩展样例。
- akitaonrails/ai-memory(MIT,Rust):三层权威 ≈ provisional / confirmed / _shared,反馈词 helpful / stale / wrong。
- TencentDB 2.0.1-beta(2026-08-14/15)、Hermes v0.20.x、Raven v0.1.11/12、Codex changelog、Claude Code memory 文档(v2.1.214 起自动盖 modified 时间戳,/doctor 精简规则):无机制变化,记一笔。youtu-agent 自 2026-03 停更。

对设计有影响的旧东西(8-11 已列但这次核了源码或文档):OpenHands 从裸子串改整词匹配的原因与代码(https://github.com/OpenHands/software-agent-sdk ,MIT);TencentDB `sanitize.ts` 剥自注入块防反馈回路(https://github.com/TencentCloud/TencentDB-Agent-Memory ,MIT);hermes 原子写、锁、jsonl 遥测(https://github.com/NousResearch/hermes-agent ,MIT);ACE 开源 Curator 只实现 ADD(https://github.com/ace-agent/ace ,Apache-2.0);ExpeL 计数常量(https://github.com/LeapLabTHU/ExpeL ,Apache-2.0);Memp Adjustment 提示词(https://github.com/zjunlp/MemP ,MIT);Honest Lying(https://arxiv.org/abs/2605.29463);HarnessBank 四道门与 false elites(https://arxiv.org/abs/2607.13683);MemRL(https://arxiv.org/abs/2601.03192)自认多条同注归因模糊;Counterfactual Trace Auditing(https://arxiv.org/abs/2605.11946)过 / 不过与「有没有被用」几乎脱钩;Cursor Memories 时间线(1.0 → 1.2 加人审 → 2.1 下架,https://forum.cursor.com/t/are-my-memories-gone/144057);Claude Code auto memory 200 行 / 25KB(https://code.claude.com/docs/en/memory);Codex memories 两阶段流水线与 30 天规则(https://github.com/openai/codex);Copilot memory 用前核 citations、28 天未用删(https://docs.github.com/en/copilot/concepts/agents/copilot-memory);Devin Knowledge trigger_description(https://docs.devin.ai/product-guides/knowledge)。

---

## 6. 不采纳的和为什么

被 refute 的发现:本轮核实清单里没有(refuted 列表为空)。所有进入核实的发现都是 confirmed 或 partially,partially 的都已按最小改法收进第 2 节。

不采纳或本轮不做的:

- 影子注入(ext-scoring 建议 3,shadow_rate 20% 命中只记录不注入):和「同坑不踩两次」直接冲突,报告自己也说默认关。不做;它给的免费对照(没有任何条目命中的打回,下一次通过率是多少)进 stats。
- 整词匹配当主药(OpenHands `_keyword_matches`):H6 加了长度下限与停用词后,它只解决 `git` ⊂ `github` 型误伤;可作可选小改,不是必须。
- 归一化 + 掩码(critic-mechanism M-01 修法 1,Drain3 正则):核实清单选了「两边 lower() 原样子串,不折空白不做其他归一化」,靠长度门槛、停用词、实例 token 黑名单、来源要求四条替代,避免双实现分叉;`re:` 正则整体删除。
- 「useful 只数独占注入」(ext-recent F2 的一种写法):会把与 confirmed 老条目同 unit 的 provisional 全部卡死;改成激活条件 + shared 标记进 stats。
- disputed 分 activated / ignored(ext-scoring 建议 5):要拿 Known Mitigation 命令对 events 工具调用,门禁没这信号,只影响 §8.2「confirmed 连续两次 disputed 退回」这条少走之路;留第二步。
- preventive_pass 计数、agent 自报 memory_used:决定 9 说命中只从 hook 记录算;pre_start 命中 bad_case 与「读到就避开」不计数,改成 stats 可见性(冷条目列表)而不是新计数;自报按 Honest Lying 不可靠,只当参考。
- 命令与 Verification / Known Mitigation 文本比对当 useful 必要条件(prior F-3 第二层):要解析 markdown、误判多;首选「events 里读了该条目文件路径」的零成本判法,beacon 命令作可选加强。
- cause 加 `other:<自由子标签>`:M6 用「同格允许 add 但 similar 必填」加少量 harness 级类解决,不开自由标签。
- 格子键加 scope 维、cause 按技能分表(critic-mechanism M-04 修法 2/3):M6 改 1 之后不再必要。
- disputed ≥ 2 的条目不再注入、modify / supersede 候选不参与 hook 注入(critic-mechanism M-08 原提法):核实清单改成「disputed 排最后仍注入」「候选与目标同命中合并一行」,保留 cli confirm 需要的计数。
- 自动老化删除、时间衰减(hermes 90 天、Copilot 28 天、MemoryOS):与不删原则相反;只标不删。
- 每 run 都跑带 / 不带对照(Live-Evo、SkillAudit、AgentEvolver 混合 rollout):真集群作业翻倍不现实;调研「以后再说」四条第一版维持不做。
- 结算点改「unit 终局」的另一种极端(所有中间轮次一律不记):选 B 保留了 pending 机制,首注入 useful 仍在同 unit 判。
- 溯源头换 YAML frontmatter(ext-products F7、critic-alignment C-18):决定 13 没钉头格式,可以换,收益是和业界一致、少一条正文行首禁令、Superseded-by 变字段;代价是老分支 `parse_entry_headers` / `_provenance_lines` 和测试要重写。给用户选,本轮不列为必改。
- index.md 改 bullet 并删门 1 的 `|` 禁令:同上给用户选;真实错误信息里 `|` 不少(shell 管道),倾向改。
- meta 加 `load:` 字段、注入文本带 Verification 行、provisional 标 `[provisional, unreviewed]`:可选,低价值。
- 让 pre_start 索引超限报错:会因整理活挡住整次 run,与「不阻塞主流程」相反,只提示。
- README 路由表整个改成从 meta 重建的派生物(ext-memory-libs §5.4 第一方案):README 含手写表和说明文字,重建会盖掉;改成幂等对账。
- 门 4 加「至少一个触发词出自 repairs」的严格版(critic-alignment C-3 原提法):对 build_env、smoke_test 这类门 repairs 只有固定句,会逼 agent 取过宽触发词;放宽成「出自 repairs 或 log_tail」并让匹配面加日志尾。
- 注入块与 repair 分离要动 core extension.ts(critic-operations §3.7 修法):设计稿改动点只列技能钩子;核实清单选了「接受块随 repair 进 lastRepair / events / result.json error,digest 建时剥掉」,不动 extension。
- 单独落 `artifacts/memory_state.json` 代替 checkpoint 字段(old-code §5.1 备选):与「checkpoint 一处存状态」不一致,选改 checkpoints.ts。
- 「候选先写、声明最后写」合同(critic-integration §2.12 方案 B):靠 agent 记得,选 gateInputs 联合哈希。
- 抽取门耗尽提示语改成让 agent 写 no_new_lessons: true(F2 integration 备选):会把「门没过」记成「没有经验」,丢掉 extraction: failed 这个事实。
- 用 jsonschema / pydantic 做门校验:jsonschema 不在锁里,pydantic 在锁里但设计稿有意不用;手写校验。
- 「一次打脸即冻结」本身:决定 11 钉死,ext-scoring 查了一圈没人一次冻结,但它只是退回人批不是删,站得住;要做的是收窄哪些打回算打脸(H5)。
- K=2 本身:无外部出处,但只是把文件挪进 git 目录、人 commit 才生效,双门可接受;先补激活与不同 run,再看 Cold ratio 调。

---

## 7. 建议的修改顺序

先改设计稿文字,再动手写代码;每步都不动十五条决定。

第一批(决定 usage 行格式、config.json 内容与 hooks 数据结构,后面全依赖它们):
1. H2 checkpoint 扩 `memory` 子对象(两份 checkpoints.ts)。
2. H1 抽取门耗尽自动 advance 并记 extractionStatus,与 §4.5 统一次数;M4 的措辞与前提。
3. H3 digest sha 进 checkpoint、门三方比对不重建、删「agent 也可以重跑」、`--mark-passed`。
4. H4 gateInputs 联合哈希。
5. H5 结算规则:门禁结果定义、同 unit 不重复注入、激活条件、不同 run、结算点选 A 还是 B(用户定)、TS 只写 usage 行 python 重放。
6. H6 触发词纪律:匹配文本、注入块剥离常量、门 4 门槛、fact 的 scope 匹配、删 `re:`、共享测试向量、排序与超预算。

第二批(生产检出上跑起来之前):
7. M1 权限与 fix-perms;M3 每 run 一个 usage 文件、flock、mkdir 抓 EEXIST、tempfile。
8. M2 promote 只改状态 + outbox,cli export,README 幂等对账;手册「记忆」一节与四件同版。
9. M5 schema 拷贝、手写校验、cli 纯标准库、两种解释器各跑一遍测试。
10. M9 target 从产物读、剥 output_dir、eval 证据根、裁剪顺序、log_tail seek 读。
11. M12 接入面小坑一次清掉(state 消息措辞、测试目录、写死单元数、schema 记忆位、reval、pre_start 谁建索引)。

第三批(第一批真实 run 之前):
12. M6 格子与判重、similar 条件必填、cause 枚举进 config.json。
13. M7 跨技能过滤。
14. M8 老 17 条一次性人工补头,补头前只进 index.md;§5.1 段名清单。
15. M10 digest memory_usage、derived_from、索引不依赖 run 目录、digest 拷贝与 evidence 哈希、fix_exercised 标签。
16. M11 index.md 与 memory_context 预算、superseded 状态、stale 标记、stats 新列。
17. M13 补「相对 v1 的改动」一段并改 §13 引证;给师兄一句话说明治理时序变了。

第二步(本稿不做,记下):disputed 分 activated / ignored;beacon 命令加强激活判定;YAML frontmatter 与 bullet 索引(若用户选换);Known Mitigation 命令来源硬门(verified_commands);记忆管理 agent(Codex Phase 2 约束当蓝本);带 / 不带对照;稳定 bad_case 升级成门禁脚本检查(SkillWeaver / ASI 方向);recipe 类条目;feed / reval 接入。

集群 e2e(§12)必须补的场景(critic-operations §4.2):两账号同时跑生产检出、跨节点 append、fresh clone 首跑、抽取门连败仍 success 收尾、非 success 收尾三次逻辑与 print 模式轮数、output_dir 下 digest 与 memory_context 无 output_dir 字样、kill -9 后 prior_runs 可见、转正后 fetch + merge --ff-only 演练、200MB 带 `\r` 的 build.log、`python3 -s cli.py` 与 `$HARNESS_PYTHON_BIN` 各跑一遍子命令。

---

## 附录 A:被 refute 的发现

无。本轮核实清单的 refuted 列表为空。

## 附录 B:低严重度、未进核实的发现(供参考,已按根因归入第 2 节的用括号注明)

- 注入块被写回 events 进下一次 digest,触发词可能落在记忆文字上(mechanism)。已并入 H6。
- 若干未定义小口子:注入行一句话的来源字段、转正撞名、provisional 只增不减、同格多候选浪费重试(mechanism)。一句话来源已并入 H6 第 6 条(取 H1);promote 到 bad_cases 撞名应改名并更新 entry_id 与 meta,cli 可见;其余并入 M11、M6。
- 插 unit 后要改的写死处与 §12 测试目录写错(integration)。已并入 M12。
- 老条目路由表触发词是整句描述(integration)。已并入 M8。
- task_classification 无记忆位;ROUTING.md 示例与 context_selection schema 不符(integration)。已并入 M12。
- units.json「四个技能」里 sure_reval 没有 unit;重试上限两技能不同(integration)。已并入 M12、H1。
- pre_start 索引检查是 TS 做还是 python 做没定(integration)。已并入 M12。
- 结算规则没覆盖两种 ok:true 的无结果情形(integration)。已并入 H5。
- headless 下 4.5 的两次 pre_finish 打回叠加 3 次催办上限(operations)。已并入 M4。
- 手册与 e2e 缺项(operations)。已并入 M2、§7 末尾。
- K=2 与一次冻结无外部出处;排序没用 useful;反馈稀疏无可见性(alignment)。已并入 H5、H6、M11、M13。
- 判重只拦触发词集合全等,similar 没和判重挂钩(alignment)。已并入 M6。
- 溯源头是自定义五行,业界一致用 YAML frontmatter;表格索引逼出 `|` 禁令(alignment、products)。列入 §6 给用户选。
- 没有时间维度:无老化标记、fact 无 stale、superseded 状态未定义、超预算截法未写(alignment、libs、products、scoring)。已并入 M11、H6。
- §13 依据表引证对不上;相对 v1 的改动没写(alignment、prior)。已并入 M13。
- post_finish 并发写 README 路由表无锁(prior)。已并入 M2。
- provisional 条目依赖 run 目录存活;provisional 层没有老化(prior)。已并入 M10、M11。
- 锁定解释器实际不止标准库 + PyYAML(prior、old、libs、products):锁里有 pydantic 2.13.3、typer、rich、structlog、python-dotenv,jsonschema 不在。设计稿按更严口径没坏处,汇报别说错。已并入 M5。
- trigger 里的分号在 Trigger: 头里会被拆开(old ledger 63 行)。已并入 H6 第 3 条。
- 库里 bad_case 段落名不统一,与「以库里现有为准」冲突(old)。已并入 M8。
- provisional 参与判重后一条差候选占格子拖住后来者(old)。已并入 M6。
- schema 用 const 时 validate.ts 不校验(old)。已并入 M5。
- 被 supersede 的条目 status 未定义(libs)。已并入 M11。
- Windows 下 os.replace 会被读句柄卡住(libs):CPython 打开文件不带 FILE_SHARE_DELETE,本机单测若同时开着 index.json 读句柄会偶发失败;抄 hermes 重试分支能避开,集群 Linux 无此问题。可选。
- 原始修复窗口只活在 run 目录;causal:false 候选的 Known Mitigation 无落地约束(exp)。已并入 M10。
- 注入排序没用 useful / last_hit;没有「久未命中」的可见性(products)。已并入 H6 第 6 条、M11。
- 门 2 的路径校验是字符串判断,挡不住 symlink 逃逸(products):run artifacts 用 resolve + relative_to,集群模型目录按老分支词法收容保留软链。已并入 §3.2 Anthropic 一行,实现时按目录分开。
- Confirmed 退回应只数激活且消耗过重试、来自不同 run 的 disputed(scoring)。列入第二步。
- fact 无 staleness 信号(scoring)。已并入 M11。
- 没有对照;可选影子注入(scoring)。列入 §6 不采纳,免费对照进 stats。
- 证据只记路径不记哈希(recent)。已并入 M10。
- 8-11 调研两处口径需更正:字节 OpenViking、阿里 ReMe 各有带机制文档的记忆项目(recent)。已并入 §5。
