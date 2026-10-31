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
| `scope` | `ui` / `unit` | 统计口径；**门禁只认 `ui`，两种不得混算** |
| `total` | int | 该口径下的用例数，**含 known-limit 跳过** |
| `counted` | int | **判定分母** = `blocked + effective_pass + failed` |
| `blocked` | int | `message` 以 `blocked:` 开头的 xfail 数 |
| `effective_pass` | int | 真正通过（不含 xfail / skip） |
| `failed` | int | failure + error |
| `known_limit` | int | `message` 以 `known-limit:` 开头的 skip 数，**不进分母** |
| `ratio` | float | **`blocked ÷ counted`** |
| `verdict` | `GREEN` / `YELLOW` / `RED` | 门禁结论，取命中项里的最高严重级 |
| `gate` | string | **本次「触发/命中」的判定编号**，逗号分隔，如 `G1,G2`；无命中为空串 |
| `top_signal` | string | 出现最多的拦截信号，形如 `waf_block(whaleguard)`；无则空串 |
| `provisional` | bool | `true` = 阈值尚未校准（当前恒为 true，见规范 §6） |
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

配套两点：

- **幂等键 `ts|scope`**：同一份 junit 重跑（workflow 重试、本地重放）得到同一个键，
  第二次写入被识别为重复，不产生第二行。
- **并发**：CI 用 `concurrency.group` 串行化；本地用 `.git/blocked-ledger.lock`
  （600 秒视为陈旧锁）。两个写入者各自 fetch 同一个 tip 再 push，后者必然非快进。

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
