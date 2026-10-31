# blocked 门禁规范（唯一来源）

> **一句话**：`blocked → xfail` 让 pytest 永远退出 0，于是必须有一个独立判定器，
> 把「绿了但什么都没验」判出来。
>
> **本文件是这套判定规则的唯一规范来源。** PLAN / README / 看板 / Issue 模板里
> 只允许**引用本文件**（§编号 + 链接），不得复制指标定义与判定表——
> 复制过的那份迟早跟这份不一样，而没人会发现自己看的是旧版。
>
> 实现：`tools/blocked_gate.py`（判定）· `tools/ledger.py`（记录结构）
> · `tools/ledger_append.py`（追加与读回）。
> 规范与实现不一致时，以本文件为准，并改实现。

---

## 1. 输入契约

| 项 | 要求 |
|---|---|
| 输入文件 | **一条轨道一份 junit**：火车票 `pytest suites/thirdparty -m ui`、酒店 `pytest suites/hotels -m hotels`，各自 `--junitxml` |
| 轨道声明 | 判定时必须用 `--scope ui`（火车票）或 `--scope ui_hotels`（酒店）声明本次判哪条；junit 里每条的 `scope` 属性必须**全部等于**它，混入别的轨道即 exit 2 |
| 口径属性 | 每条 testcase 必须带 `<property name="scope" value="ui"/>` 或 `"ui_hotels"`，由 `conftest.py` 的 `pytest_collection_modifyitems` 按标记统一盖（`hotels` 标记优先于 `ui`） |
| blocked 标记 | `pytest.xfail(reason="blocked: <signal>")`，前缀固定 `blocked:` |
| 已知限制 | `pytest.skip(reason="known-limit: K-xx <原因>")`，前缀固定 `known-limit:` |
| 真实失败 | 普通 `assert` 失败 / 异常，**禁止**转成 xfail 或 skip |

四条不可妥协的理由：

1. **口径不能混算。** 2026-10-31 实测：31 条里 27 条是数据层单测。一起进分母时
   `ratio = 4/(4+27+0) = 12.9%`，G1（要 `effective_pass == 0`，实际是 27）与
   G2（要 `≥0.5`，实际 0.129）双双失守，门禁变成装饰。
   这条反例已固化为测试 `tools/tests/test_blocked_gate.py::test_全量31条junit必须被拒收`：
   混入全量 junit 必须 exit 2，不是算出一个数就完事。
2. **`scope` 用属性而不是目录名。** junit 的 `classname` 是 rootdir 相对路径，
   换个 rootdir 就变；`user_properties` 跟着用例走。实测 pytest 9.2.0 会把
   `user_properties` 写成 `<properties><property name="scope" value="ui"/></properties>`，
   pass / xfail / fail 三种结果都写。
3. **拦截率不可跨站点比较。** 火车票与酒店是两个站点、两套 WAF。共用一个 `scope`
   时，酒店那条 25% 会把火车票的 1.0 序列打断，G3（连续 3 次 ≥0.3）与 G4（连续 7 天 ≥0.1）
   永不触发——阈值不是不灵敏，是被稀释。因此 **G3/G4 只在同一 `scope` 内累计**
   （裁决 2026-11-01），账本的 `scope` 字段就是这个分组键。
4. **未知前缀不得静默归类。** junit 里 xfail 与 skip 都落在 `<skipped>` 元素，
   只能靠 `message` 前缀区分「被对方拦」和「我们自己承认的限制」。
   前缀不认识的，一律判**契约不成立**（exit 2），不允许默认算作 skip——
   「看不懂就当跳过」会让 `counted` 悄悄变小，`ratio` 悄悄变大。

---

## 2. 指标

```
total           = scope=<本次 --scope> 的用例数（含 known-limit 跳过）
known_limit     = message 以 "known-limit:" 开头的跳过
counted         = blocked + effective_pass + failed        ← 分母，known_limit 不进
blocked         = message 以 "blocked:" 开头的结果
effective_pass  = passed
failed          = failure + error
ratio           = blocked / counted                        （counted 为 0 时记 0.0）
```

`effective_pass` 是核心指标：它回答「这次到底验证成功了几条」，`blocked` 只解释为什么少。
`ratio` 的分母是 `counted` 而**不是** `total`——这一点在看板上最容易被误读，
所以 ledger 里两个字段都存、`total != counted` 时一眼可见。

---

## 3. 判定表

**逐条全部计算，不短路。** 判定表不是 `if/elif` 链：G1 与 G2 常常同时成立，
只报第一条会让人以为「修掉一条就能变绿」。最终 `verdict` 取命中项里的最高严重级，
`gate` 字段列出**全部**命中编号。

| 编号 | 条件 | 判定 | 理由 |
|---|---|---|---|
| **G0 真失败** | `failed ≥ 1` | 🔴 红 | 功能失败优先级高于一切，不看 ratio |
| **G1 空转** | `effective_pass == 0` 且 `total ≥ 2` | 🔴 红 | 一条都没验证成功，绿色无意义 |
| **G2 高比例** | `counted ≥ 4` 且 `ratio ≥ 0.5` | 🔴 红 | 半数以上被拦，样本足够 |
| **G3 趋势** | **同一 `scope`** 的最近连续 3 次（含本次）`ratio ≥ 0.3` | 🔴 红 | 单次 30% 不起眼，连续 3 次是 IP 在被逐步收紧 |
| **G4 长期** | **同一 `scope`** 的最近 7 个自然日都有记录，且每天都 `blocked ≥ 1` 且 `ratio ≥ 0.1` | 🟡 黄 + 开 Issue | 长期标记，不是抖动 |
| **G5 提示** | `blocked ≥ 1`，且以上都没命中 | 🟡 黄（`::warning::`） | 提醒但不判死 |
| **G6 正常** | `blocked == 0` 且 `effective_pass ≥ 1`，且以上都没命中 | 🟢 绿 | — |

**G1 的写法改过一次**：原为 `blocked ≥ 2 且 effective_pass == 0`。2026-10-31 的记录两种写法
同判红，但原写法漏掉「全部被 `known-limit` 跳过」这种同样什么都没测的空转，
现改为 `effective_pass == 0 且 total ≥ 2`。

**G3/G4 按轨道分组**（裁决 2026-11-01）：只用 `scope` 等于本次 `--scope` 的历史行，
其余行（火车票 / 酒店 / 将来新增的轨道）一概不进序列，输出里会写明
「轨道历史：scope=X 共 N 条（账本总计 M 条，其余 M-N 条属于别的轨道，不计入 G3/G4）」。
两条轨道共用同一个 jsonl 而不是拆文件：拆文件会让读回、schema 校验、CI 聚合各多一层，
而分组的唯一需求只是「不串味」，一个字段就够。反例已固化为测试
`tools/tests/test_blocked_gate.py::test_hotels的25不打断trains的1_0序列`
与 `::test_hotels轨道G4独立计算`。

**小样本豁免**：`counted < 4` 时关闭 G2/G3/G4（3 条里拦 2 条 ratio 就是 66%，没有统计意义），
G0/G1/G5/G6 照常——它们不是比例判定，不受样本量影响。

**G3/G4 不可用要说出来**。历史记录不够时在输出里明写「G4 不可用（不是不触发）」，
不许安静地当作没命中。

---

## 4. 退出码

| 判定 | 退出码 | Job | 附加 |
|---|---|---|---|
| 绿 / 黄 | 0 | success | 追加 ledger（黄另出 `::warning::`） |
| 红 | 1 | failure | `::error::` + 追加 ledger |
| 契约不成立 | 2 | failure | 不写 ledger——脏口径不许进账本 |

**黄必须保持 job 绿**：整站被拦（红）才阻断，零散拦截只提示。
契约问题（2）比红更早暴露，因为它说明「统计本身坏了」，此时算出来的 Ratio 没有意义。

---

## 5. ledger 契约

数据在 `ops/metrics` 分支的 `ops/blocked-ledger.jsonl`（孤儿分支，只存数据，与源码分离）。
字段语义的表在**该分支的 `ops/README.md`**，本节只规定写入行为。

| 规则 | 做法 | 为什么 |
|---|---|---|
| **只 append** | 永不改写历史行、不做行轮转。文件胀到影响读取时，新建分卷文件，旧文件原样冻结 | 改一行就等于改历史，G3/G4 从此不可复核 |
| **schema_version** | 每条带版本号；缺字段的历史行按 v0 只做旧字段校验，**不改写** | 结构演进要能与历史共存 |
| **`scope` 取值** | 只允许 `ui`（火车票）/ `ui_hotels`（酒店），清单登记在 `tools/ledger.py` 的 `GATE_SCOPES`；`unit` 是 junit 侧标记，账本拒收 | 新增轨道必须先登记，否则 G3/G4 会跨轨道串味；`unit` 进账本说明写入端绕过了门禁 |
| **幂等键 `ts\|scope`** | `ts` 取自 junit 的 `testsuite timestamp`，不是墙上时钟 | workflow 重试 / 本地重放同一份 junit 会得到同一个键，第二次写入被识别为重复 |
| **父提交** | 每次先 fetch 当前 tip，在 worktree 里基于该 tip 提交，再快进 push | 禁止「孤儿 commit + force push」：那样 git 历史每天是互不相关的快照，出问题答不出「这条记录在哪次之后加的」 |
| **并发** | CI `concurrency.group` 串行化 + 本地文件锁（`.git/blocked-ledger.lock`，600s 视为陈旧） | 两个写入者同时 fetch 会导致后者非快进，重试也救不了 |
| **推完读回** | `gh api repos/<owner>/<repo>/contents/ops/blocked-ledger.jsonl?ref=ops/metrics --jq .content \| base64 -d`，逐行跑 schema 校验，任一失败即红 | 只相信 push 的退出码，等于相信「我说我推了」 |
| **分账前遗留记录** | 分账（2026-11-01）之前以 `scope=ui` 写入的那条酒店运行（第 3 行，ratio 25%）**原样保留**；不提供 `scope` override，也不提供 legacy exclusion | 改写历史行 = 账本可被事后调整，G3/G4 从此不可复核；override 会把「分账」退化成「账本修正」。代价是 ui 轨道的 G3 被推迟（见 §8.6），所以**门禁输出必须点名该行**，不许让它悄悄压制趋势 |
| **`provisional` 转正** | 字段在写入时固定、**永不回改**；「这条还能不能当单样本基线」是**读侧派生**的：同一 `scope` 出现第 2 条记录时，先前那条 `provisional: true` 自动降为普通历史样本 | 14 天校准窗口管的是阈值（§6 的三条），与单条记录的样本地位无关；把两者绑在一起，会让「阈值未校准」变成「这条记录永远不能转正」 |

G3/G4 依赖历史，所以 ledger 丢失 = 这两条阈值失效。这也是不选 Actions cache 的原因
（cache 7 天不用即清，会让趋势判定突然失忆）。

---

## 6. 阈值是 provisional，不是推导值

`0.3 / 0.5 / 3 次 / 7 天` 四个数字是**拍的**，没有任何数据支撑：

- G2 的 0.5 来自「半数以上被拦」这个说法本身；
- G3 的 0.3 与「3 次」来自「一次是抖动，三次是趋势」的直觉；
- G4 的 7 天来自「一周」；
- 没有一条是从真实拦截率分布推出来的。

因此：

1. 门禁输出**必须**带 `[provisional：阈值未校准…]` 标记，ledger 记录带 `"provisional": true`。
2. ledger 攒到 **14 天**真实数据后重新校准，校准时把依据（分布、分位数、为什么取这个值）
   写进本节，并把代码里的 `PROVISIONAL` 置为 `False`。
3. 校准前任何「今天被判红是否合理」的讨论，都只能作为观察，不能作为调参依据。

---

## 7. CI 接入

```yaml
- name: UI 用例（独立 junit，供门禁消费）
  id: ui
  continue-on-error: true          # blocked 是 xfail，pytest 本身退出 0；真失败靠 G0 兜
  run: python -m pytest suites/thirdparty -m ui --junitxml=reports/junit-ui.xml

- name: blocked 门禁
  id: gate
  continue-on-error: true          # 红也要先把记录写进 ledger，再由最后一步把 job 弄红
  run: python tools/blocked_gate.py reports/junit-ui.xml --scope ui \
         --ledger .ledger/blocked-ledger.jsonl --record-out reports/gate-record.json

- name: 追加 ledger 并读回校验
  run: python tools/ledger_append.py reports/gate-record.json

- name: 门禁红则 job 红
  if: steps.gate.outcome == 'failure'
  run: exit 1
```

酒店轨道另起一份 job，把 `--junitxml`、`-m hotels`、`--scope ui_hotels` 三处同时换掉即可；
`--scope` 与 junit 口径不一致会 exit 2，不存在「忘了改一处还照常出数」。

`continue-on-error` 只用在用例与门禁两步，理由：红也必须留下记录，
但 job 最终结果必须是红，否则「红了但 CI 绿」= 另一种形式的假绿。

---

## 8. 已知边界

1. 门禁**只判断拦截与有效验证数**，不判断被测站点功能是否正确。功能正确性归回归套件（容器靶站）。
2. G3/G4 依赖推送权限。若无 `contents: write`，两条阈值判为**不可用**并在报告里明写，不假装有。
3. `strict` xfail 不启用：blocked 是运行时检测的结果，用例一旦没检测到拦截会正常跑断言，
   不存在「意外通过」，不需要 strict 兜底。
4. 门禁拒收旧格式 junit（无 `scope` 属性）。现场存证见
   `tools/tests/fixtures/junit-mixed-31.xml` 与
   `tools/tests/test_blocked_gate.py::test_旧格式junit没有scope属性必须被拒收`。
5. 本文件的判定表有 13 个变异被测试杀死（ratio 分母换成 total、未知前缀静默跳过、
   不检查 scope、G1 退回旧写法、真失败不红，以及分轨相关的 4 个：历史不按轨道过滤、
   记录 scope 写死 ui、聚合只比默认轨道、白名单不登记 ui_hotels；遗留提示相关 4 个：
   G3 窗口长度 3→2、遗留过滤去掉 `ratio` 条件、遗留提示恒不输出、遗留提示混进判定），
   见 `tools/tests/`。
6. **分账前遗留记录会推迟 G3，这是已知代价，不是故障。** ui 轨道第 3 行是分账前写入的
   25%（酒店运行），append-only 下留在 ui 序列；G3 取 `series[-3:]`，因此它需要
   **3 条**新 ui 记录才滑出窗口 —— 现有 3 条 ui 记录，G3 最早在**第 6 条 ui 记录**才可能命中。
   门禁输出会点名该行（`legacy_notes`），避免把「G3 不触发」误读成阈值失灵。
   时点由测试锁定：
   `tools/tests/test_blocked_gate.py::test_分账前遗留的25记录在第6条ui记录后滑出窗口`。

---

## 9. 变更记录

- **2026-11-01 v4**：两处遗留裁决落地，均不改账本历史行。
  ① **第 3 行（分账前写进 `ui` 的酒店 25%）不改写**，接受 G3 推迟到第 6 条 ui 记录；
  门禁新增 `legacy_notes()`，在输出与 Step Summary 里点名该行，明确「不是阈值问题、无 override」；
  新增 3 条单测（第 6 条时点、点名且不改判不改行、单轨道时不猜分账点）+ 4 个变异全杀死。
  ② **`provisional` 转正条件写死为读侧派生**：同 `scope` 出现第 2 条记录即自动降为普通历史样本，
  不改行；否决「14 天校准后转正」（校准管阈值，不管单条记录的样本地位）。
  `top_signal` 旧格式不改，仍按前缀匹配；结构化 `top_signal_key` 留到 14 天校准后再议。
- **2026-11-01 v3**：账本按轨道分账。`scope` 白名单 `{ui, ui_hotels}`（登记在 `ledger.GATE_SCOPES`），
  门禁新增 `--scope` 声明本次判哪条轨道，junit 口径不符即 exit 2；**G3/G4 改为只在同一 `scope` 内累计**，
  输出写明本轨道用了几条历史。理由：酒店那条 25% 打断了火车票的 1.0 序列，G3 永不触发。
  历史行（第 1–3 行，scope=ui）不改写，酒店从第 4 行起写 `ui_hotels`。
  新增 4 条单测 + 4 个变异；不加单独 jsonl（读回/schema/CI 聚合会各多一层，而分组只需一个字段）。
- **2026-10-31 v2**：G1 改写为 `effective_pass == 0 且 total ≥ 2`；新增 G0；
  判定表改为「不短路、gate 列全部命中」；`ratio` 分母显式定为 `counted`；
  ledger 增加 `schema_version` / `counted` / `known_limit` / `provisional` 与父提交、幂等、读回三项约束；
  阈值标注 provisional；本文件确立为唯一来源，PLAN 与 README 改为引用。
- **2026-10-31 v1**：初版（G1 为 `blocked ≥ 2 且 effective_pass == 0`，未定义 scope 属性）。
