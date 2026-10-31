# blocked-ledger

本分支**只存数据**，不放源码。用于跨 run 累计第三方站点的拦截情况，
让「G3 连续 3 次」「G4 连续 7 天」这两条阈值成为可计算的，而不是靠人记。

规范见主分支 `docs/BLOCKED-GATE.md`。

## 文件

- `ops/blocked-ledger.jsonl`：每行一条运行记录，**append-only**，永不改写历史行。

## 字段

| 字段 | 取值 | 含义 |
|---|---|---|
| `ts` | ISO 8601 带时区 | 运行时间 |
| `run` | string | CI run id 或本地标识 |
| `source` | `ci` / `local` | 来源 |
| `scope` | `ui` / `unit` | **统计口径，门禁只认 `ui`** |
| `total` | int | 该口径下用例总数 |
| `blocked` | int | reason 以 `blocked:` 开头的 xfail 数 |
| `effective_pass` | int | 真正通过（不含 xfail / skip） |
| `failed` | int | failure + error |
| `skipped` | int | reason 以 `known-limit:` 开头的 skip，**不进分母** |
| `ratio` | float | `blocked / (blocked + effective_pass + failed)` |
| `verdict` | `GREEN` / `YELLOW` / `RED` | 门禁判定 |
| `gate` | string | 命中的阈值，如 `G1,G2` |
| `top_signal` | string | 出现最多的拦截信号 |
| `note` | string | 可选，人工补充 |

## 口径纪律

1. **`scope=ui` 与 `scope=unit` 不得混算。** 若把 27 条数据层单测算进分母，
   `ratio` 被稀释到 12.9%，G1（要 `effective_pass==0`）和 G2（要 `>=0.5`）**都不会触发**，
   门禁等于没有。因此 CI 必须用 `-m ui` 单独跑浏览器用例、产出独立 junit，门禁只吃那份。
2. `verdict` 由脚本计算，不允许人工改；人工判断写 `note`。
3. 轮转：超过 5000 行时保留最近 1000 行，被裁掉的区间写进该次 commit message。

## 读取（不必 checkout）

```bash
gh api "repos/testhy22/train-e2e/contents/ops/blocked-ledger.jsonl?ref=ops/metrics" \
  --jq .content | base64 -d | tail -10
```
