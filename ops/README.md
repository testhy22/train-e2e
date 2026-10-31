# blocked-ledger

本分支**只存数据**，不放源码。用于跨 run 累计第三方站点的拦截情况，
让「G3 连续 3 次」「G4 连续 7 天」这两条阈值成为可计算的，而不是靠人记。

判定规则（指标定义、G0–G6、退出码）的**唯一来源是主分支 `docs/BLOCKED-GATE.md`**；
本节只规定「字段是什么意思、怎么写进来」。

---

## 1. 字段语义（看板按这个读，不要自己解释）

| 字段 | 取值 | 含义 |
|---|---|---|
| `schema_version` | int | 记录结构版本。**缺失 = 0（历史种子行）**，只按旧字段校验，且永不改写 |
| `ts` | ISO 8601 带时区 | 取自 junit 的 `testsuite timestamp`，**不是墙上时钟** |
| `run` | string | CI run id，或本地标识 |
| `source` | `ci` / `local` | 来源 |
| `scope` | `ui` / `ui_hotels` | **轨道 = G3/G4 的分组键**。`ui` = 火车票（trains.ctrip.com），`ui_hotels` = 酒店（www.ctrip.com 首页酒店模块）。两种不得混算，也不得跨轨道累计趋势。`unit` 是 junit 侧标记，账本拒收 |
| `total` | int | 该口径下的用例数，**含 known-limit 跳过** |
| `counted` | int | **判定分母** = `blocked + effective_pass + failed` |
| `blocked` | int | `message` 以 `blocked:` 开头的 xfail 数 |
| `effective_pass` | int | 真正通过（不含 xfail / skip） |
| `failed` | int | failure + error |
| `known_limit` | int | `message` 以 `known-limit:` 开头的 skip 数，**不进分母** |
| `ratio` | float | **`blocked ÷ counted`** |
| `verdict` | `GREEN` / `YELLOW` / `RED` | 门禁结论，取命中项里的最高严重级 |
| `gate` | string | **本次「触发/命中」的判定编号**，逗号分隔，如 `G1,G2`；无命中为空串 |
| `top_signal` | string | 出现最多的拦截信号，形如 `waf_block(whaleguard)`；无则空串。⚠️ `login_wall` 的 detail 是整条回跳 URL（长度 ~130 字符），形如 `login_wall(https://passport…)`，看板请按前缀匹配，不要按全串分组 |
| `provisional` | bool | `true` = **写入那一刻**阈值尚未校准（规范 §6）。字段**永不回改**；「还能不能当单样本基线」看 §4.2 |
| `note` | string | 可选，人工补充。**人工判断只写这里，永不写 verdict** |

### 三条最容易读错的，写死在这里

1. **`gate` 是「命中」，不是「通过」。**
   `gate: "G1,G2"` 表示 G1 与 G2 **都被触发**，不是「G1、G2 通过了」。
   这与 `docs/BLOCKED-GATE.md` §3 的判定表一致：表里的 ✓ 一律表示「触发」。
   判定表**不短路**，同时成立几条就报几条 —— 只报第一条会让人以为修一条就变绿。

2. **`ratio` 的分母是 `counted`，不是 `total`，也不是「通过的条数」。**
   `ratio = blocked / (blocked + effective_pass + failed)`。
   例：total=5、known-limit=1、blocked=4 → `counted=4`、`ratio=1.0`，不是 4/5=0.8。
   用 `total` 或 `pass` 做分母算出来的数**不属于本账本的 ratio**。

3. **`total ≠ counted` 是正常的。**
   差值就是 `known_limit`。看到两者不等不要当 bug，那是 K-01 这类已知限制被排除出分母。

---

## 2. 首条种子记录怎么读（2026-10-31 23:44）

```json
{"ts":"...","scope":"ui","total":4,"blocked":4,"effective_pass":0,"ratio":1.0,"verdict":"RED","gate":"G1,G2"}
```

- 无 `schema_version` / `counted` / `known_limit` / `provisional` → **v0 历史行**，不补写、不改写。
- `verdict: RED` 与 `gate: "G1,G2"` 同义：G1（空转）与 G2（半数以上被拦）**同时触发**。
- 当时那条是 `total=4`（还没有 K-01 这条 skip），所以 `counted` 也等于 4，
  v0 行看不出 `total` 与 `counted` 的差；从 v1 起两者分开存。

---

## 3. 写入约束（三条硬约束，实现见 `tools/ledger_append.py`）

1. **只 append。** 历史行永不改写、不做行轮转。文件大到影响读取时新建分卷文件，
   旧文件原样冻结。轮转 = 改历史，G3/G4 从此不可复核。
2. **每次提交必须有父提交。** 先 fetch 当前 tip，在 worktree 里基于该 tip 提交，再快进 push。
   **禁止**「孤儿 commit + force push」：那样 git 历史每天是互不相关的快照，
   出事时答不出「这条记录是在哪次提交之后加的」，git 历史失去审计价值。
3. **推完必须读回。** `gh api ... --jq .content | base64 -d` 取回远端内容，逐行跑 schema 校验；
   读不回或任一行不合法就报错（CI 红）。只相信 push 的退出码，等于相信「我说我推了」。

配套四点（第 3、4 点 2026-11-01 追加）：

- **`scope` 只在同一轨道内累计。** G3（连续 3 次 ≥0.3）与 G4（连续 7 天 ≥0.1）只读 `scope`
  等于本次门禁 `--scope` 的行。理由：酒店那条 25% 曾把火车票的 1.0 序列打断，G3 永不触发
  ——阈值不是不灵敏，是被稀释。第 1–3 行的 `scope` 是 `ui`（分账前的历史，不改写），
  酒店从第 4 行起写 `ui_hotels`；因此第 3 行那条 0.25 仍在火车票序列里，属于历史遗留（见 §4.1）。
- **`scope` 白名单**登记在 `tools/ledger.py` 的 `GATE_SCOPES`，新增轨道必须先登记；
  否则校验端会直接拒收，避免「悄悄多一条没人算的轨道」。
- **幂等键 `ts|scope`**：同一份 junit 重跑（workflow 重试、本地重放）得到同一个键，
  第二次写入被识别为重复，不产生第二行。
- **并发**：CI 用 `concurrency.group` 串行化；本地用 `.git/blocked-ledger.lock`
  （600 秒视为陈旧锁）。两个写入者各自 fetch 同一个 tip 再 push，后者必然非快进。

## 4. 账本现状：两处遗留，都不改写历史行（2026-11-01 裁决）

### 4.1 第 3 行是分账前遗留的 ui 记录，G3 要等到第 6 条 ui 记录

**第 3 行为分账前遗留 ui 记录；ui 轨道 G3 预计在第 6 条 ui 记录后恢复；
不提供 override / legacy exclusion，保持 append-only。**

- **事实**：ui 轨道现有 **3 条**（`1.0` / `1.0` / `0.25`）。第 3 行那条 0.25 是分账前
  写进 `ui` 的酒店运行，按 append-only 留在 ui 序列里。
- **恢复窗口**：G3 取 `series[-3:]`，所以那条 0.25 需要 **3 条**新 ui 记录才滑出窗口 ——
  第 4 条时末三位 `[1.0, 0.25, 新]`、第 5 条时 `[0.25, 新, 新]` **都仍含 0.25**，
  到第 6 条 `[新, 新, 新]` 才可能命中。（「再新增 2 条就恢复」少算一条。）
  时点由 `tools/tests/test_blocked_gate.py::test_分账前遗留的25记录在第6条ui记录后滑出窗口` 锁定。
- **不改写、不排除**：改写历史行 = 账本可被事后调整，G3/G4 从此不可复核；
  `scope` override 会把「分账」退化成「账本修正」。以后有审计需求另议，现阶段一律不加。
- **会被说出来**：门禁输出与 Step Summary 里有
  `· 分账前遗留记录：scope=ui 有 1 条 ratio<30% 的行…`（规则见 `docs/BLOCKED-GATE.md` §5、§8.6），
  免得看板把「G3 不触发」读成阈值失灵。

### 4.2 第 4 行的 `provisional` 何时转正（二选一，已写死）

**转正条件：同一 `scope` 出现第 2 条 `ui_hotels` 记录时自动转正 —— 读侧派生，不改行。**

- **含义**：`provisional: true` 表示「本条是分账后首条、单样本，不足以单独代表该轨道」。
  第 2 条同轨道记录一写入，G3/G4 的窗口就不再由它单独构成，这个含义自动失效。
- **否决「14 天校准后转正」**：14 天窗口管的是阈值（规范 §6），与单条记录的样本地位无关；
  把两者绑在一起，会让「阈值还没校准」变成「这条记录永远不能转正」的理由。
- **字段永不回改**：`provisional: true` 是写入那一刻的事实，append-only 下永久保留。
  「转正」只是读侧不再把它当单样本基线，**账本字节不变**。
- **没有自动提拔**：15 天没跑酒店，第 4 行就仍然是单样本 —— 这是诚实的，
  不能靠「过期时间」把一条孤证升格成基线。

---

## 读取（不必 checkout）

```bash
gh api "repos/testhy22/train-e2e/contents/ops/blocked-ledger.jsonl?ref=ops/metrics" \
  --jq .content | base64 -d | tail -10
```

校验整份账本（CI 推完就是这么验的）：

```bash
gh api "repos/testhy22/train-e2e/contents/ops/blocked-ledger.jsonl?ref=ops/metrics" \
  --jq .content | base64 -d > /tmp/ledger.jsonl
python -c "import sys,pathlib;sys.path.insert(0,'tools');import ledger;print(len(ledger.load_ledger(pathlib.Path('/tmp/ledger.jsonl'))),'行全部合法')"
```
