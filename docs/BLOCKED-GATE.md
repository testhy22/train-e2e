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
| 输入文件 | **只吃浏览器用例那份 junit**：`pytest suites/thirdparty -m ui --junitxml=reports/junit-ui.xml` |
| 口径属性 | 每条 testcase 必须带 `<property name="scope" value="ui"/>`，由 `conftest.py` 的 `pytest_collection_modifyitems` 统一盖 |
| blocked 标记 | `pytest.xfail(reason="blocked: <signal>")`，前缀固定 `blocked:` |
| 已知限制 | `pytest.skip(reason="known-limit: K-xx <原因>")`，前缀固定 `known-limit:` |
| 真实失败 | 普通 `assert` 失败 / 异常，**禁止**转成 xfail 或 skip |

三条不可妥协的理由：

1. **口径不能混算。** 2026-10-31 实测：31 条里 27 条是数据层单测。一起进分母时
   `ratio = 4/(4+27+0) = 12.9%`，G1（要 `effective_pass == 0`，实际是 27）与
   G2（要 `≥0.5`，实际 0.129）双双失守，门禁变成装饰。
   这条反例已固化为测试 `tools/tests/test_blocked_gate.py::test_全量31条junit必须被拒收`：
   混入全量 junit 必须 exit 2，不是算出一个数就完事。
2. **`scope` 用属性而不是目录名。** junit 的 `classname` 是 rootdir 相对路径，
   换个 rootdir 就变；`user_properties` 跟着用例走。实测 pytest 9.2.0 会把
   `user_properties` 写成 `<properties><property name="scope" value="ui"/></properties>`，
   pass / xfail / fail 三种结果都写。
3. **未知前缀不得静默归类。** junit 里 xfail 与 skip 都落在 `<skipped>` 元素，
   只能靠 `message` 前缀区分「被对方拦」和「我们自己承认的限制」。
   前缀不认识的，一律判**契约不成立**（exit 2），不允许默认算作 skip——
   「看不懂就当跳过」会让 `counted` 悄悄变小，`ratio` 悄悄变大。

---

## 2. 指标

```
total           = scope=ui 的用例数（含 known-limit 跳过）
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
| **G3 趋势** | 最近连续 3 次（含本次）`ratio ≥ 0.3` | 🔴 红 | 单次 30% 不起眼，连续 3 次是 IP 在被逐步收紧 |
| **G4 长期** | 最近 7 个自然日都有记录，且每天都 `blocked ≥ 1` 且 `ratio ≥ 0.1` | 🟡 黄 + 开 Issue | 长期标记，不是抖动 |
| **G5 提示** | `blocked ≥ 1`，且以上都没命中 | 🟡 黄（`::warning::`） | 提醒但不判死 |
| **G6 正常** | `blocked == 0` 且 `effective_pass ≥ 1`，且以上都没命中 | 🟢 绿 | — |

**G1 的写法改过一次**：原为 `blocked ≥ 2 且 effective_pass == 0`。2026-10-31 的记录两种写法
同判红，但原写法漏掉「全部被 `known-limit` 跳过」这种同样什么都没测的空转，
现改为 `effective_pass == 0 且 total ≥ 2`。

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
| **幂等键 `ts\|scope`** | `ts` 取自 junit 的 `testsuite timestamp`，不是墙上时钟 | workflow 重试 / 本地重放同一份 junit 会得到同一个键，第二次写入被识别为重复 |
| **父提交** | 每次先 fetch 当前 tip，在 worktree 里基于该 tip 提交，再快进 push | 禁止「孤儿 commit + force push」：那样 git 历史每天是互不相关的快照，出问题答不出「这条记录在哪次之后加的」 |
| **并发** | CI `concurrency.group` 串行化 + 本地文件锁（`.git/blocked-ledger.lock`，600s 视为陈旧） | 两个写入者同时 fetch 会导致后者非快进，重试也救不了 |
| **推完读回** | `gh api repos/<owner>/<repo>/contents/ops/blocked-ledger.jsonl?ref=ops/metrics --jq .content \| base64 -d`，逐行跑 schema 校验，任一失败即红 | 只相信 push 的退出码，等于相信「我说我推了」 |

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
  run: python tools/blocked_gate.py reports/junit-ui.xml \
         --ledger .ledger/blocked-ledger.jsonl --record-out reports/gate-record.json

- name: 追加 ledger 并读回校验
  run: python tools/ledger_append.py reports/gate-record.json

- name: 门禁红则 job 红
  if: steps.gate.outcome == 'failure'
  run: exit 1
```

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
5. 本文件的判定表有 5 个变异被测试杀死（ratio 分母换成 total、未知前缀静默跳过、
   不检查 scope、G1 退回旧写法、真失败不红），见 `tools/tests/`。

---

## 9. 变更记录

- **2026-10-31 v2**：G1 改写为 `effective_pass == 0 且 total ≥ 2`；新增 G0；
  判定表改为「不短路、gate 列全部命中」；`ratio` 分母显式定为 `counted`；
  ledger 增加 `schema_version` / `counted` / `known_limit` / `provisional` 与父提交、幂等、读回三项约束；
  阈值标注 provisional；本文件确立为唯一来源，PLAN 与 README 改为引用。
- **2026-10-31 v1**：初版（G1 为 `blocked ≥ 2 且 effective_pass == 0`，未定义 scope 属性）。
