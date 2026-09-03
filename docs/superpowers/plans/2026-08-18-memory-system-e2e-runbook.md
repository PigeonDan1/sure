# 记忆系统 v2 集群 e2e 清单

前提:分支 `feat/memory-system` 已 clone 到集群家目录(手册 §2 方式 B),`npm install --ignore-scripts` 跑完,`sure/.runtime/harness/` 已物化(跑过一次任意命令,harness runtime 材料化完成);生产检出不动,本清单全部动作在自己的 clone 里做。

集群纪律照旧:PTY 交互终端只接受单行命令,粘贴多行或带 `>` 重定向的命令会被复写、跑坏——凡是下面写了多行或 `>` 的命令,先把内容写成脚本文件上传到集群(`hpc_put` 或同等方式),再在 PTY 里跑那一个脚本文件,不要把多行内容直接粘进 PTY。文件 mtime 不可信,任何一步都不许拿"看起来改过"当证据,只认文件内容、命令输出、`sha256sum`,或者应用自己写在文件里的时间戳字段(不是文件系统的 mtime)。密码/认证失败立刻停,不重试。

集群共享 python 解释器一律 `python3 -s`(不吃用户 site-packages);harness 自己物化出来的解释器在环境变量 `$HARNESS_PYTHON_BIN` 里,两者都要跑到,因为 hooks 内部走的是 `$HARNESS_PYTHON_BIN`(或它取不到时的 `resolveHarnessPython` 兜底),人工用 `cli.py` 时惯例用 `python3 -s`——两条路径都得验证能跑记忆系统的纯标准库代码。

本清单出现的用户名、家目录路径、分区名全部是占位符(`<你的用户名>`、`<集群家目录>`、`<test_model>` 这类尖括号),照自己的账号替换;不要把替换后的真实值抄回这份文档或任何要提交的文件。

**找一次刚起跑的 run 的 run_id,统一用这个办法(不认 mtime)**:起跑前先 `ls .sure/runs/`,记下当时已有的目录名;跑完(或跑到你要检查的那个阶段)再 `ls .sure/runs/` 一次,两次列表的差集——新出现的那个目录名就是这次的 run_id。这是 [noninteractive_usage.md](../../noninteractive_usage.md) 里记录的标准做法(它原话是"起跑前记一下 `.sure/runs/` 里已有的目录,跑完取新出现的那个")。如果同一检出里同时有别人在跑命令,新出现的目录可能不止一个,这时 `cat .sure/runs/<候选目录>/run.json`,核对 `skillName` 和 `args` 里的模型 id,挑对得上的那个。下文每一步用到 `<run_id>` 都按这个办法拿,不再重复说明。

每一步都记录:run_id(或没有 run_id 的步骤记命令与产物路径)、看到的文件/字段、结论(过 / 不过 / 备注)。结果表格式见文末。

---

## A. 自动化测试:先把本地跑不出结论的部分在集群上跑绿

这几组测试分两类。**A1-A3 是本地真的跑不出结论的**,原因各不相同:`sure/runtime/harness/bootstrap.py` 第 7 行 `import fcntl`,Windows 没有这个模块,凡是依赖它材料化 harness runtime 的测试在本地只能报 `ModuleNotFoundError`,不代表真的坏;`sure-onboard-memory.test.ts` 里有一个 `describe.skipIf(!HARNESS.ok)` 分组(里面两个测试),本地是"没跑",不是"跑过且过"。集群是 Linux,`bootstrap.py` 能真的走完,这三组必须在这里补上结论,是 Task 11/13 报告里明确留给这份 runbook 的债。**A4 是本地已经跑绿的**,放在这里只是交叉确认,不是必须项——时间紧可以跳过 A4,不影响这份 runbook 别处的结论。跑之前确认 `packages/coding-agent` 下 `npm install` 已经装过。

**A1. 两个老状态机套件,本地因 `fcntl` 全线标红**

```bash
cd packages/coding-agent && npx vitest run test/suite/sure-onboard-state-machine.test.ts test/suite/sure-eval-state-machine.test.ts
```

过:两个文件都显示 `Test Files ... passed`、`Tests ... passed`,0 failed;输出里不出现 `fcntl`、`ModuleNotFoundError`、`WinError`、`EPERM` 字样。不过:把失败的测试名和报错原文整段记下来,不要只记数字——这是判断"记忆系统接线真的把这两个状态机套件搞坏了"还是"环境限制解除后自然全绿"的唯一证据。这一步同时是 TS 复审"没有任何平台测过"名单里两条的唯一验证点:onboard 经 `extract_lessons` 单元的完整状态机重放(`sure-onboard-state-machine.test.ts`),以及两个状态机套件里所有依赖 python 子进程(`runBackend` / `runGateScript`)的路径在 POSIX 上到底能不能跑通——本地的 38 个 `fcntl` 红只在这里能真正洗白或坐实。

**A2. onboard 记忆接线套件的 `describe.skipIf(!HARNESS.ok)` 分组(里面两个测试)**

```bash
cd packages/coding-agent && npx vitest run test/suite/sure-onboard-memory.test.ts
```

过:先自己用 `grep -cE "^\s*(it|test)(\.only|\.skip)?\(" test/suite/sure-onboard-memory.test.ts` 数出文件里顶层 `it(...)`/`test(...)` 总数(记为 N)——这条命令锚定了行首,只认缩进后紧跟 `it(`/`it.skip(`/`it.only(`/`test(` 的行,不会像不加 `^` 的 `grep -c "it("` 那样把 `split(`、`SystemExit(` 这类子串也当成测试算进去。再用同样口径单独数一遍 `describe.skipIf(!HARNESS.ok)` 这个分组里有几个 `it(...)`(记为 S):`sed -n '/describe\.skipIf(!HARNESS\.ok)/,/^});/p' test/suite/sure-onboard-memory.test.ts | grep -cE "^\s*(it|test)(\.only|\.skip)?\("`。跑测试后看到 `Tests N passed (N)` 算过(说明 `describe.skipIf` 分组也真的跑了,`HARNESS.ok` 为 true);看到 `(N-S) passed | S skipped (N)` 算不过——说明集群上 `HARNESS.ok` 仍是 false,先查 `$HARNESS_PYTHON_BIN` 有没有设置、`sure/.runtime/harness/` 是否真的物化了,再重跑。N 和 S 都不要抄这里或历史版本里写过的任何字面数字,每次跑之前现数一遍——这个文件还有别的改动在并行进行,数字不会稳定。

**A3. eval 后端回归套件(这条分支上唯一还没真正跑绿过的老套件)**

```bash
cd packages/coding-agent && npx vitest run test/suite/sure-eval-runbackend.test.ts
```

过:0 failed、0 skipped。这个文件不是记忆系统新增的,但在这条分支上到目前为止只在 Windows 上跑过,同样卡在 `fcntl`(Task 14 报告确认过,原因和 A1 一样,与记忆系统改动本身无关)——集群是第一次能看到它的真实结论。

**A4(可选:本地已经跑绿,集群重跑只是交叉确认,不是必须)**

```bash
cd packages/coding-agent && npx vitest run test/suite/sure-memory-eval-hooks.test.ts
```

```bash
python3 -s -m unittest discover -s sure/runtime/memory -p "test_*.py"
```

`sure-memory-eval-hooks.test.ts` 在实现者的 Windows 机器上(`$HARNESS_PYTHON_BIN` 指到本机 python,不需要 `bootstrap.py`)已经跑到 `20 passed (20)`,零 skip——它走的是独立于 `bootstrap.py` 的 `HARNESS_PYTHON_BIN` 路径,不属于 A1-A3 那种"本地跑不出结论"。集群上重跑一遍如果还是全绿,只是确认这个结论跟具体机器无关;如果集群上反而出现 skip 或失败,那是新问题,要单独排查,不能归因于"只有集群能解决的环境限制"(因为本地已经证明这条路径本身没问题)。python 单测那条也是同理:纯标准库,Windows 上一直绿,这里只是再确认一遍。

---

## B. 手工全链路

每步跑之前,先确认自己不是在生产检出里(`git remote -v` 认一下,是自己的 clone),再动手。

### B0. 老 bad_case 头信息落地验证(在仓库所有者 commit 那十七条之后、B1 之前做)

十七条老 bad_case 的头信息现在是 staged 状态,由仓库所有者本人 commit(seams 复审 Critical 1:没有头信息的话,这套系统永远注入不了任何东西,而且不会有任何提示)。commit 完成后先确认头信息真的进了那次 commit:

```bash
git show HEAD:sure/skills/sure_onboard/references/memory/bad_cases/asr_metric_bypass.md | head -3
```

(头信息现在是正文里的 `## Trigger` 一节,不再是文件首行的 `Trigger:`;`cuda_runtime_mismatch.md` 已随旧 bad_case 清理删除,拿 `asr_metric_bypass.md` 当样本。)

过:输出第一行是 `# ASR Metric Bypass`、第三行是 `## Trigger`。不过:输出里没有 `## Trigger`,说明这次 commit 提交的是没有头信息的老版本,后面 B1-B14 里所有"bad_case 应该被命中注入"的检查全部作废——先联系仓库所有者确认 commit 内容,不要往下做。

### B1. 真实 onboard 一次

挑一个已知能在这条分支上跑通到 `finalize_model_bundle` 的小模型(用之前验证过的目标,不要临时挑一个没试过的)。按上文的办法记下起跑前 `.sure/runs/` 里已有的目录,起 TUI,跑:

```
/sure_onboard model=<test_model>
```

等它以 `success` 收尾,再 `ls .sure/runs/` 取差集拿到这次的 run_id。

检查(`<run_id>` 换成实际值):

1. `cat .sure/runs/<run_id>/artifacts/run_digest.json`——文件存在;`.run.cutoff` 是正整数;`.units[]` 里 `id == "verdict"` 的那一项 `.outcome == "passed"`。
2. `cat .sure/runs/<run_id>/artifacts/extraction_declaration.json`——存在且是合法 JSON,说明过了 `extract_lessons` 门。
3. `cat .sure/runs/<run_id>/artifacts/extraction_declaration.json` 里 `no_new_lessons` 是 `false` 时:`ls sure/memory/provisional/sure_onboard/` 下有新出现的候选目录;是 `true` 时改成确认 `no_lessons_reason` 非空、`candidates` 是空数组,不强求有候选目录。
4. `ls sure/memory/digests/` 下有 `<run_id>.json`(post_finish 发布时把这次的 digest 拷了一份进记忆库)。
5. `tail -5 sure/memory/decisions.jsonl`(或 `grep '"action":"publish"' sure/memory/decisions.jsonl`)——能看到至少一行 `"action":"publish"`,`entry_id` 对得上新条目。
6. `cat sure/memory/index.md`——新条目的 entry_id 出现在列表里,状态是 `provisional`。
7. `cat .sure/runs/<run_id>/artifacts/memory_context.json`——pre_start 阶段写的,文件存在;`.schema == "sure.memory.context.v1"`;`.skill == "sure_onboard"`;`.target_id` 是这次的模型 id;`.facts` 是数组(可能为空,但键必须在)。再确认没有绝对路径泄漏:`grep -E '"/[^"]*"' .sure/runs/<run_id>/artifacts/memory_context.json` 应该无命中(合法的 JSON 字符串值不该以 `/` 开头;这一步没传 `output_dir`,天然该满足——专门验证 `output_dir` 不泄漏见 B9)。
8. `cat .sure/runs/<run_id>/state.json`——`.checkpoint.data.memory.digestCutoff` 是正整数,`.checkpoint.data.memory.digestSha256` 是一串十六进制字符,`.checkpoint.data.memory.digestPassed == "verdict"`。这三个字段是从 `verdict` 推进到 `extract_lessons` 时(`postToolResult` → `onEnterExtractLessons`)写进 checkpoint 的,run 收尾时这几个字段应该还在(没被后续 advance 清掉)——目前没有任何测试证明过这条路径,这一项专门补上。

过:八项全满足。不过:记录具体哪一项缺、diagnostics 原文、run 的最终 status。

### B2. 真实 infer 一次(同一模型,小 max_samples)

```
/sure_infer model=<test_model> datasets=<...> execution=local max_samples=<小数字>
```

(数据集参数按手册 §5 填;`max_samples` 挑一个能几分钟内跑完的小值。2026-09-03 拆分后推理是 `/sure_infer`,评测是 `/sure_eval`;这一步跑推理就够,两边的 digest / decisions / index 是同一份记忆库,规则一样。)

检查:同 B1 的 1、2、4、5、6,但判据里的单元名换掉——infer 的 digest 在 `execute_inference` 通过时建,所以 B1 第 1 项看 `.units[]` 里 `id == "execute_inference"` 的 `.outcome == "passed"`,第 8 项的 `digestPassed == "execute_inference"`(`/sure_eval` 对应的是 `assessment`)。另外:

7. `cat .sure/runs/<run_id>/artifacts/memory_context.json`——pre_start 阶段写的,文件存在;`.schema == "sure.memory.context.v1"`;`.skill == "sure_infer"`;`.target_id` 是这次跑的模型 id;`.facts` 是数组(可能为空,但键必须在)。**这一项是专门补的**:TS 复审量过全部套件后发现,`preStart` 真的写了 `memory_context.json`、`target_id` 填对了这件事在 eval 这一侧从来没有测试证明过——之前的说法(Task 17)以为 eval 这条已经被单测盖住了,是错的,onboard 和 eval 都只能靠这份 e2e 清单证。

过:六项(B1 的 1/2/4/5/6 加上这里的 7)全满足。不过:记录具体哪一项缺、diagnostics 原文、run 的最终 status。

### B3. 制造同一处打回(第一次命中)

再跑一次同目标 `/sure_onboard`,在 B1 发布的那条 bad_case 命中的 unit(即该条目 meta 里 `component` 对应的 unit,或直接看 `sure/memory/index.md` 里那一行的路由信息)故意让门必挂——按那条 bad_case 正文 `Trigger` 段描述的症状人工复现,或者临时改坏一个该 unit 会校验的产物字段。

打回之后、改产物之前,先让 agent 真的用 Read 工具打开那条 Memory 提示里指向的条目正文文件(路径就在 Memory 行里,形如 `sure/memory/provisional/sure_onboard/<slug>/entry.md`)——这一步决定后面 settle 记成 `useful_activated` 还是 `useful_unattributed`,只有 `useful_activated` 才计入自动转正(下一步 B4)。读完再把产物修好、真的过这道门。

检查:

1. 打回时 agent 收到的 repair 文本末尾出现 `Memory (advisory, agent-written, not human-reviewed; verify against evidence before relying):` 开头的一段,里面能看到该 entry_id。
2. `cat sure/memory/usage/<run_id>.jsonl`——有一行 `"kind":"inject"`,`unit` 是命中的 unit id,`entries[]` 里含该 entry_id。
3. 过门后:同一个 usage 文件里再出现一行 `"kind":"settle"`,`entry_id` 是同一条,`outcome` 是 `useful_activated`(前提是真的读过条目文件;没读过就是 `useful_unattributed`)。

过:三项都在,`outcome == "useful_activated"`。这只是两次命中里的第一次,单独这一次不会让条目自动转正——B4 需要来自另一个 run 的第二次。如果这里 `outcome` 记成了 `useful_unattributed`,B4 的自动转正条件凑不齐,回来重做这一步,确保真的用 Read 工具读了条目文件。

### B4. 第二次命中,验证自动转正(不经手工 confirm)

这是整套系统真正的核心闭环:一条经验被两次不同的 run 用上、没被打过脸,应该自己升级成 confirmed,不需要人碰。自动转正的判定在 `promote._qualifies_for_promotion`,四个条件同时成立才会触发:

- 条目 `type == "bad_case"`(fact 永不自动转正,见手册 §11);
- 产生这条目的那个候选当初声明的 `op == "add"`(`modify` / `supersede` 也永不自动转正);
- 到目前为止 `disputed == 0`(这条目从没在打回后又一次失败过);
- `useful_activated` 次数 ≥ `sure/runtime/memory/config.json` 的 `promote_useful_activated`(当前值 2),并且这些 `useful_activated` 来自 ≥ `promote_min_distinct_runs`(当前值 2)个**不同的 run_id**——同一个 run 里对同一个 unit 命中两次,在 `useful_runs` 里只算一个 distinct run,不够。

跑第三次同目标 `/sure_onboard`(记这次的 run_id 为 C,必须是一个跟 B1、B3 都不同的新 run),重复 B3 的手法:让同一个 unit 再挂在同一条 bad_case 上,打回后同样先用 Read 工具真的打开条目正文文件,再把产物修好过门。

run C 走完收尾(post_finish 会自动跑 publish + promote,不需要人工介入)之后检查:

1. `cat sure/memory/usage/<run C 的 run_id>.jsonl`——有一行 `"kind":"settle"`,`outcome == "useful_activated"`。
2. `python3 -s sure/runtime/memory/cli.py show sure_onboard/<slug>`(或直接 `cat sure/memory/meta/sure_onboard/<slug>.json`)——`status` 已经是 `confirmed`,`confirmed.by == "auto"`。
3. `ls sure/memory/outbox/sure_onboard/<slug>/`——条目已经在 outbox 里,这一步过程中没有人手工跑过 `cli.py confirm`。
4. `grep '"action":"promote"' sure/memory/decisions.jsonl | tail -1`——`by == "auto"`,`entry_id` 对得上,`reason` 里的 `useful_activated=` 数值 ≥ 2,列出的 run 里包含 B3 和 run C 两个不同的 run_id。

过:四项都成立,而且全程没有手工执行过 `cli.py confirm`。不过按条件逐个排查,最常见的两个坑:某一次 settle 其实记成了 `useful_unattributed`(那次 agent 没有真的读条目文件);或者两次 `useful_activated` 来自同一个 run_id(同一次 run 里那个 unit 重试两次都命中同一条目,在 `useful_runs` 里只算一次 distinct run)。

### B4a. 用 `cli.py stats` 确认系统真的在工作

seams 复审的结论:一个 clone 可以看起来装好了、索引也建了、条目也列在 `index.md` 里,实际上从来没存过东西、也没往任何 gate 打回里注入过东西——没有任何 hook 会主动提醒操作者这件事,唯一会说话的命令是这条(跑过 B1-B4 之后再看):

```bash
python3 -s sure/runtime/memory/cli.py stats
```

健康系统:表格里至少有一行 `inject` 列非零(B3、B4 打回命中过的那条 bad_case);末尾 `cold ratio: N/M entries have no settle row` 这一行,N 应该小于 M。死系统的样子:所有条目 `inject` 列全是 0,`cold ratio` 是 `M/M`(100%)——如果 B1-B4 都按前面步骤真跑过一遍,还是这个结果,说明记忆系统实际上没在工作,不能只凭"索引建了、条目在 index.md 里"就当它是好的。

对照检查(任一条命中都说明对应环节真的没发生过):

1. `grep '"action":"publish"' sure/memory/decisions.jsonl`——无命中:从来没存过任何条目。
2. `cat sure/memory/usage/<任选一个 run_id>.jsonl`——只有 `"kind":"pre_start"` 那一行、没有 `"kind":"inject"`:这次 run 没往任何 unit 里注入过东西。
3. `ls sure/memory/digests/<run_id>.json` 报不存在:post_finish 没走到 publish 那一步。

过:`inject` 列至少一处非零,`cold ratio` 不是 100%,三条对照检查都跟预期对得上(该有的文件/行都在)。

### B4b. preFinish 结算卡住的 unit(`settleStuckUnit`)

两侧都有单测证过这条路径(`sure-memory-eval-hooks.test.ts` 的 `it("settles a stuck unit's ...")` 两条,onboard 的 `it("pre_finish settles the stuck unit's injected entries as abandoned", ...)`),这一步在真 run 上再走一遍。

跑一次新的 `/sure_onboard`(记这次的 run_id 为 D,与前面用过的 run 都不同)。按 B3 的手法让某个 unit 的门必挂、命中一条 bad_case,agent 收到带 Memory 段的 repair。这次**不要**把产物修好过门——直接让这次 run 以 `status=failed` 调 `sure_finish` 收尾,让这个 unit 保持"卡住"(未完成、也没耗尽重试次数)的状态直接进收尾。

检查:

1. `cat sure/memory/usage/<run D 的 run_id>.jsonl`——出现一行 `"kind":"settle"`,`entry_id` 是被注入的那条,`outcome == "abandoned"`(只打回过一次、收尾时失败文本没再点名它)——即使这次 run 从没让这个 unit 的重试次数耗尽过。只有失败文本再次命中它的触发词时才是 `disputed`。

过:这一行存在且 `outcome == "abandoned"`。`abandoned` 不进任何计数器,所以这一步复用 B3/B4 用过的 bad_case 也不会动它的升降级状态。

### B4c. `config.json` 读不出时不会把 run 卡死

spec 8.2 说 `config.json` 是留给人手调的;这一步验证手滑改坏它会怎样。目前没有任何测试真正跑过 `runMemoryGate` 在 config 读不出时的路径。**只在自己的 clone 里做,不要动生产检出。**

先备份:

```bash
cp sure/runtime/memory/config.json sure/runtime/memory/config.json.bak
```

改成不合法 JSON(单行,不用重定向):

```bash
python3 -s -c "import pathlib; pathlib.Path('sure/runtime/memory/config.json').write_text('not json', encoding='utf-8')"
```

跑一次 `/sure_onboard` 到 `extract_lessons`(或收尾)。检查:

1. run 没有卡死、没有崩溃退出,照常能跑到 `extract_lessons` 或收尾——记忆系统坏了不该挡住技能本身(spec 11)。
2. diagnostics 或 repair 文本里出现类似 `memory config unreadable` 的字样,severity 是 `warning` 不是 `error`。
3. 顺手看一眼这段文本有没有把 `config.json` 的完整路径抄进去。TS 复审 Critical 2 记录过这是一个已知未修的泄漏点,不是这一步要修的,只需要如实记一句"仍泄漏路径"或"已经不泄漏"。

跑完把 `config.json` 还原:

```bash
cp sure/runtime/memory/config.json.bak sure/runtime/memory/config.json
```

过:1、2 成立;第 3 项如实记录观察到的情况即可,泄漏与否都不算这一步失败。

### B5. 两个账号同时跑生产检出

先在两个独立的 clone(自己的两份方式 B checkout,或找同事各自的 clone)里**同时**各起一条命令(两个都用不同的 target,避免互相踩同一个候选目录名),确认都不报 `PermissionError`。演练过一遍没问题后,再在真正的生产检出上找一位同事配合,同时各跑一条。

检查:

1. 全程无 `PermissionError`(agent 输出、diagnostics 都翻一遍)。
2. `ls -l sure/memory/`(以及 `provisional/`、`usage/`、`digests/` 几个子目录)——权限位里 group 有 `w`(例如 `drwxrwsr-x` 或至少 `rwxrwx---` 一类,组可写)。
3. `ls sure/memory/usage/`——两个 run 各自的 `<run_id>.jsonl` 都在,互不覆盖。
4. 两次操作跑之前先 `wc -l sure/memory/decisions.jsonl`(文件还不存在就记 0),记为 N0;都跑完再 `wc -l sure/memory/decisions.jsonl`,记为 N1。新增的具体行数不能凭空猜,读这两次操作各自的 `.sure/runs/<run_id>/artifacts/extraction_declaration.json`,把两边 `candidates` 数组的长度加起来(`no_new_lessons:true` 的那次算 0),正常情况下这个和应该等于 `N1 - N0`(publish 只在候选彻底重复或崩溃时跳过,一般一一对应)。再用 `sed -n '<N0+1>,<N1>p' sure/memory/decisions.jsonl` 把新增的这些行摘出来,逐行确认:`action == "publish"`;`run_id` 是这两次操作里的一个,不是别的;`entry_id` 的技能前缀和那次操作用的技能对得上。`tail -c 1 sure/memory/decisions.jsonl | xxd` 顺手确认文件以换行结尾,不是被并发写坏、截成半行。

过:1-4 全部成立——无 PermissionError;组可写;两个 usage 文件独立存在;新增 decisions 行的数量、`action`/`run_id`/`entry_id` 前缀都跟预期对得上。

### B6. fresh clone 首跑

新建一个从没跑过任何命令的 clone(方式 B,全新目录)。跑之前:

```bash
ls sure/memory/ 2>&1
```

预期:目录不存在或存在但 `index.json` 不在里面(如果仓库带了一个空的 `.gitkeep` 之类,那没关系,只要没有 `index.json`)。

起 TUI,跑第一条命令(任意一条,比如一次不涉及真实资源的 `/sure_onboard`,只要能走到 pre_start)。检查:

1. 跑完之后 `sure/memory/index.json` 存在,`cat` 出来是合法 JSON,`.schema == "sure.memory.index.v1"`。
2. 这一次的 diagnostics 里没有 `severity: "error"` 的记忆相关条目。
3. 记下 `sha256sum sure/memory/index.json` 的值。
4. 再跑第二条命令(第二次触发 pre_start),然后手工确认索引没有被无谓重建(不看 mtime,看内容哈希和 `--check` 自己的判断):

```bash
python3 -s sure/runtime/memory/index.py --repo-root . --check
```

过:输出 `index: up to date`,且 `sha256sum sure/memory/index.json` 与第 3 步记的值相同。如果输出 `index: rebuilt`,说明两次 pre_start 之间源内容确实变了(比如别的步骤的候选/发布动作发生在中间),属于正常情况,不算不过;只有"源内容没变但索引仍被判定要重建"才是问题。

### B7. 抽取门连败

在 `extract_lessons` 单元故意写一个过不了门的候选(例如 trigger 全是 stopword,或缺一个必需 section),提交声明,失败后**改过内容**(哪怕只改一个字——EXTRACTION.md 写明白了:提交同样的字节不会重新跑门)再提交一次,让它连续失败两次。

检查:

1. 第二次失败后,状态机自动往下推进(不再停在 `extract_lessons`),不需要人工干预。
2. 这次 run 的 diagnostics 里出现 `extraction: failed`(或含这个短语的一条)。
3. run 最终仍能以 `success` 收尾(抽取失败不挡收尾)。
4. `ls sure/memory/digests/ | grep <run_id>`——**不存在**这次 run 的 digest 文件(post_finish 里 `extractionStatus == "failed"` 时跳过 publish,不发布);`sure/memory/provisional/` 下这次 run 产生的候选目录也没有被转正。

过:1-4 全部成立。

### B7a. 抽取声明本身不是合法 JSON

B7 试的是"候选内容过不了门",这一步试另一种坏法:`extraction_declaration.json` 这个文件本身就不是合法 JSON。2026-08-30 起这条路径不再"卡住不报错":`validateProduces`(`hooks/validate.ts`)当场打回,repair 是 `extraction_declaration.json must be a JSON object. Expected shape: …`,和其他门禁失败一样消耗一次重试。

在 `extract_lessons` 单元,把声明文件写成不合法 JSON(单行,不用重定向):

```bash
python3 -s -c "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('{not json', encoding='utf-8')" .sure/runs/<run_id>/artifacts/extraction_declaration.json
```

检查:

1. 调 `sure_update_state` 后立刻被打回,repair 原文含 `must be a JSON object`;`state.json` 里 `extract_lessons` 的重试计数加一;单元不前进。
2. 如果这次改用非 success 收尾(`sure_finish status=failed`),`pre_finish` 会先要一份合法的声明(最多两次),repair 同样点名 JSON 形状;第三次放行并记 `extraction: failed`——记录 repair 原文和最终 `extractionStatus`。
3. 把文件内容换回合法 JSON,确认这次真的能过门、正常前进,排除是环境问题而不是这条路径本身的问题。

过:1、2 的 repair 都点名了 JSON 形状且重试计数如实增加;3 必须成立。

### B8. 非 success 收尾三次逻辑

让 run 走到能调用 `sure_finish` 的位置,以 `status=failed` 收尾但**不写** `extraction_declaration.json`。检查:

1. 第一次 `sure_finish` 被打回,repair 里要求补一份抽取声明。
2. 照做但故意让声明本身也过不了门(或者干脆再不写),第二次 `sure_finish` 还是被打回。
3. 第三次 `sure_finish`(这次给一份合法声明,或者继续不给)应该被放行,run 记为收尾,diagnostics 里能看到抽取相关的失败记录。
4. 额外用非交互 print 模式(`-p` 参数,见 [noninteractive_usage.md](../../noninteractive_usage.md))跑一遍同样的场景,数一下 harness 自己对"迟迟不给合规产物"的催办轮数上限(设计里这个上限是 3),记下实际观察到的轮数和最终是放行还是判 incomplete。

过:1-3 成立,且 print 模式那一轮观察到的催办轮数、最终状态都记下来。

### B9. output_dir 不泄漏

`/sure_onboard` 和 `/sure_eval` 各传 `output_dir=<绝对路径>` 跑一次(能走到 `extract_lessons` 即可)——eval 那次别省,B2 第 7 项只证过 eval 的 `memory_context.json` 字段对不对,没有专门证过 `output_dir` 不进去,这一步是唯一补这个点的地方。每次跑完:

```bash
grep -r "<output_dir 路径>" <output_dir>/artifacts/run_digest.json <output_dir>/artifacts/memory_context.json
```

过:无命中(把 `<output_dir 路径>` 换成实际传的绝对路径字符串)。再检查:

```bash
grep -rl "<output_dir 路径>" sure/memory/provisional/
```

过:无命中,或者命令报"没有匹配的文件"(即 `provisional/` 下任何 `proposal.json` 都不含这个路径字符串)。

### B10. kill -9

跑一次 `/sure_onboard`(同一个 target),在它经过至少一次 gate 打回之后、还没到 `sure_finish` 之前,找到进程 PID 强杀:

```bash
kill -9 <pid>
```

(PTY 里这是单行命令,能直接跑;找 PID 用 `ps` 配合关键词,不要用固定端口/固定路径去猜。)

记下这次被杀掉的 run 的 run_id(设为 A)。再跑一次同目标的 `/sure_onboard`(run B),等它走到 `extract_lessons`(这时 hook 已经建好 `run_digest.json`)。检查:

```bash
cat .sure/runs/<run B id>/artifacts/run_digest.json
```

过:`.prior_runs[0].run_id == <run A 的 run_id>`,且 `.prior_runs[0].last_repair` 非空(杀之前至少经历过一次打回或失败,才有内容可填;如果为空,说明杀的时机太早,重来一次,晚一点再杀)。

### B11. 转正 → export → commit → 生产检出更新演练

这一步演练的是人工兜底路径,不是 B4 那种自动转正的替代——挑一条这次凑不齐自动转正条件的条目来练,比如一条 fact,或者一个 `op` 是 `modify`/`supersede` 的候选(它们按设计永远不会自动转正,见手册 §11),这样才不会跟 B4 的自动转正结果混在一起。

用 cli 手工把它标成 confirmed:

```bash
python3 -s sure/runtime/memory/cli.py confirm sure_onboard/<slug> --reason "e2e 演练"
```

确认它出现在 `sure/memory/outbox/` 下,然后导出进自己的另一份 clone:

```bash
python3 -s sure/runtime/memory/cli.py export sure_onboard/<slug> --repo-root <另一份 clone 的路径>
```

命令会打印一条建议的 `git add … && git commit -m …`。照打印的原样跑(commit 到本地即可,**不要推公司主线**——真要推公司主线需要用户同意,这一步只演练到本地 commit)。检查:

1. `<另一份 clone>/sure/skills/sure_onboard/references/memory/bad_cases/<slug>.md`(fact 则是 `sure/skills/_shared/memory/facts/<slug>.md`)存在,内容含五行头(`Trigger:` / `Cell:` / `Source:` / `Added:` / `Status:`)。
2. `git -C <另一份 clone> log -1 --stat` 显示刚才的 commit,只改了预期的文件(新增的 `.md`、README 路由表)。
3. 在第三份 clone(或原 clone,只要不是刚才 commit 的那份)里 `git fetch && git merge --ff-only` 只有当第二份 clone 真的有一个可达的远端或者是本地路径 remote 时才有意义——如果没有现成的第三份 clone,这一步可以改成 `git -C <另一份 clone> log --oneline -3` 加一句备注"未演练 merge,只验证了 export+commit 两步",不要编造一个 merge 结果。

过:1、2 成立;3 要么真的演练过 merge 且无冲突,要么如实记录"未演练"。

### B12. 200MB 带 `\r` 的 build.log

写一个小脚本(**上传后再跑,不要在 PTY 里粘贴多行**),生成一个几百 MB、每行都用 `\r` 结尾的假日志,放到某次 onboard run 的 `build_env` 产物路径上,让 `build_env` 挂掉:

```bash
#!/usr/bin/env bash
# make_big_crlf_log.sh <目标路径>
python3 -s -c "
import sys
path = sys.argv[1]
line = ('x' * 78 + '\r\n').encode()
with open(path, 'wb') as f:
    for _ in range(2_700_000):   # 78+2 字节 * 2.7M ≈ 216MB
        f.write(line)
" "$1"
```

上传并执行(单行调用,不含重定向):

```bash
bash make_big_crlf_log.sh .sure/runs/<run_id>/artifacts/build_env.log
```

让 `build_env` 单元因此判失败,推进到 `extract_lessons`(或直接手工触发一次 digest 构建)。检查:

```bash
time python3 -s sure/skills/sure_onboard/scripts/build_run_digest.py --run-dir .sure/runs/<run_id> --repo-root . --skill sure_onboard
```

1. 命令返回的 `run_digest.json` 里,`build_env` 这个 unit 的 `log_tail.lines` 数组长度 ≤ 30。
2. 数组里每一行长度 ≤ 300 字符。
3. `time` 报的 real 时间 < 2 秒。

过:1-3 全部成立。

### B13. cli 两个解释器

```bash
python3 -s sure/runtime/memory/cli.py stats
```

```bash
"$HARNESS_PYTHON_BIN" sure/runtime/memory/cli.py stats
```

过:两条都输出一张表(有用/打脸/冷条目/每单元重试趋势那几列),没有 traceback。接着各跑一遍(两种解释器都跑,一共八条):

```bash
python3 -s sure/runtime/memory/cli.py list
python3 -s sure/runtime/memory/cli.py show sure_onboard/<slug>
python3 -s sure/runtime/memory/cli.py rebuild-index
python3 -s sure/runtime/memory/cli.py fix-perms
```

过:四条都有合理输出,`fix-perms` 报告的"仍不可写路径"列表为空(或者列出的路径本来就不该由这个账号写,备注说明)。

### B14. python 单测两个解释器

```bash
python3 -s -m unittest discover -s sure/runtime/memory -p "test_*.py"
```

```bash
"$HARNESS_PYTHON_BIN" -m unittest discover -s sure/runtime/memory -p "test_*.py"
```

过:两条都是最后一行 `OK`,无 `FAIL` / `ERROR`。(这一步和 A4 的差别是这里额外验证了 harness 物化出的解释器;A4 只验证了 `python3 -s`。)

---

## C. 已知没有覆盖的行为

TS 复审列过一份"任何平台都没有测试证过"的清单,共十一条。preStart 写 `memory_context.json`(B1/B2/B9 补)、onboard 经 `extract_lessons` 的状态机重放和 POSIX 上的 python 子进程路径(A1 补)、`postToolResult` → `onEnterExtractLessons` 的 digest 字段(B1 第 8 项补)、onboard 的 `settleStuckUnit`(B4b 补)、`config.json` 读不出(B4c 补)、不合法的抽取声明(B7a 补)——七条已经在上面找到了能查的步骤。剩下四条这份清单不做,原因如下,不是漏掉了:

- **`runMemoryGate` 的 `ranFailed` 标志在 `postToolResult` 路径上被忽略。** 要复现得人为弄坏 python 解释器或让记忆库导入失败(比如指错 `$HARNESS_PYTHON_BIN`、抠掉 bundle 里的模块),这在共享检出上做风险太大,可能真把检出弄坏、影响同检出的其他人;只有在一个可以随便弄坏重建的独占 clone 上才该试,不满足这份清单"照着做、不留手尾"的前提,不写成一步。
- **`gateDigest` 对 `artifacts/candidates` 或 `artifacts/memory_evidence` 下坏输入(符号链接、不可读文件、缺失 produces)的处理。** 这是 TS 复审的 Critical 1;写这份清单的时候,相关修复还在改动中(比如给 `memory_evidence` 加字节上限)。现状下故意放一个符号链接或超大文件进去,有可能把一次 run 卡死在需要人工清理的状态,不满足"每步独立可复核、不留后患"的要求。等修复落定后应该单独补一步,这次不加。
- **同一个 run 中断后,同一进程带着 `state.json` 里已有的 `memory.injected` / `pendingDisputed` / `digestSha256` 恢复继续跑**(不是 B10 那种"杀掉后另起一个新 run")。人在终端上没有可靠的单命令办法精确卡在"某个 unit 跑到一半、状态已经落盘"这个时间点再恢复同一个进程,做不到可复核,不写成一步。
- **两个并发 run 之间,一个 run 的 post_finish 重建索引恰好卡在另一个 run 自己两次注入之间。** B5 验证的是基础并发(不报错、usage 文件独立、decisions 行数对得上),没有单独证过这个更细的时序竞争;人工没办法可靠地卡这个时间点,不写成一步。

## 结果记录

结果表格式:`| 步骤 | run_id | 结论 | 备注 |`,A1-A4、B0-B14 各占一行,其中 B4 之后插了 B4a/B4b/B4c、B7 之后插了 B7a,这五步也各占一行(没有 run_id 的步骤填命令本身)。填完贴进记忆系统目录的接手盘点后面。
