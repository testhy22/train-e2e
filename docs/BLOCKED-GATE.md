# blocked 门禁规范

> **一句话**：`blocked → xfail` 让 CI 永远退出 0，所以必须有一个独立的判定器把「绿了但什么都没验」判出来。
> 本文件是该判定器的唯一规范来源；数据存在 `ops/metrics` 分支（见文末）。

---

## 1. 输入契约

| 项 | 要求 |
|---|---|
| 输入文件 | **只吃浏览器用例那份 junit**：`pytest <suite> -m ui --junitxml=reports/junit-ui.xml` |
| 用例标记 | 所有驱动浏览器的用例必须带 `@pytest.mark.ui`；数据层单测**不得**带 |
| blocked 标记 | `pytest.xfail(reason="blocked: <signal>")`，前缀固定 `blocked:` |
| 已知限制 | `pytest.skip(reason="known-limit: K-01 ...")`，前缀固定 `known-limit:` |
| 真实失败 | 普通 `assert` 失败 / 异常，**禁止**转成 xfail |

两条不可妥协的理由：

1. **口径不能混算。** 实测（2026-10-31 23:44）：31 条里 27 条是数据层单测，
   若一起进分母，`ratio = 12.9%`，G1（要 `effective_pass==0`）与 G2（要 `>=0.5`）双双失守，
   门禁变成装饰。只算 UI 4 条时 `ratio = 100%`，两条都命中。
2. **前缀是统计的地基。** junit 里 xfail 表现为
   `<skipped type="pytest.xfail" message="...">`，与真正的 skip 同元素类型，
   只能靠 message 前缀区分「被对方拦」和「我们自己承认的限制」。

---

## 2. 指标

```
total            = scope 内用例数
blocked          = xfail 且 message 以 "blocked:" 开头
skipped          = skip 且 message 以 "known-limit:" 开头     # 不进分母
effective_pass   = passed
failed           = failure + error
ratio            = blocked / (blocked + effective_pass + failed)
```

`effective_pass` 是核心指标：它回答「这次到底验证成功了几条」，而 `blocked` 只是解释为什么少。

---

## 3. 判定表（优先级从上到下，命中即停）

| 编号 | 条件 | 判定 | 说明 |
|---|---|---|---|
| **G1 全灭** | `blocked ≥ 2` 且 `effective_pass == 0` | 🔴 红 | 一条都没验证成功，绿色无意义 |
| **G2 高比例** | `total ≥ 4` 且 `ratio ≥ 0.5` | 🔴 红 | 半数以上被拦，样本足够 |
| **G3 趋势** | 最近连续 3 次 `ratio ≥ 0.3` | 🔴 红 | 单次 30% 不起眼，连续 3 次是 IP 被逐步收紧 |
| **G4 长期** | 最近连续 7 天 `ratio ≥ 0.1` 且 `blocked ≥ 1` | 🟡 黄 + 开 Issue | 长期标记，不是抖动 |
| **G5 提示** | `1 ≤ blocked`，未命中以上 | 🟡 黄 | `::warning::`，job 保持绿 |
| **G6 正常** | `blocked == 0` 且 `effective_pass ≥ 1` | 🟢 绿 | — |

小样本豁免：`total < 4` 时**只启用 G1**，不启用 G2/G3/G4。
理由：3 条里拦 2 条 ratio 就是 66%，比例没有统计意义；但「全灭」这一事实依然成立。

### 退出码

| 判定 | 退出码 | Job | 附加 |
|---|---|---|---|
| 绿 | 0 | success | 追加 ledger |
| 黄 | 0 | success | `::warning::` + 追加 ledger |
| 红 | 1 | failure | `::error::` + 追加 ledger + 更新 Issue |

**黄必须保持 job 绿**——这是设计选择：整站被拦（红）才阻断，零散拦截只提示。

---

## 4. ledger 契约

- 位置：`ops/metrics` 分支的 `ops/blocked-ledger.jsonl`（孤儿分支，只存数据，与源码分离）
- 写入：**append-only**，一行一次运行，历史行永不改写
- 字段：见该分支 `ops/README.md`
- 轮转：> 5000 行时保留最近 1000 行，被裁区间写进该次 commit message
- 读取（无需 checkout）：

```bash
gh api "repos/testhy22/train-e2e/contents/ops/blocked-ledger.jsonl?ref=ops/metrics" \
  --jq .content | base64 -d | tail -10
```

G3/G4 依赖历史，所以 ledger 丢失 = 这两条阈值失效。这也是不选 Actions cache 的原因
（cache 7 天不用即清，会让趋势判定突然失忆）。

---

## 5. CI 接入

```yaml
- name: UI 用例（独立 junit，供门禁消费）
  run: pytest suites/thirdparty -m ui --junitxml=reports/junit-ui.xml
  continue-on-error: true          # blocked 不阻断，交给门禁判定

- name: blocked 门禁
  run: python tools/blocked_gate.py reports/junit-ui.xml --ledger-branch ops/metrics
  # 退出码 0=绿/黄, 1=红
```

`continue-on-error` 只用在用例步骤：pytest 本身在 blocked 时退出 0，但真实失败要能穿透到门禁
（`failed > 0` 时门禁直接红，不看 ratio）。

---

## 6. 已知边界

1. 门禁**只判断拦截与有效验证数**，不判断被测站点功能是否正确。功能正确性归回归套件（容器靶站）。
2. G3/G4 依赖推送权限。若 CI 无 `contents: write`，两条阈值判为**不可用**并在报告里明写，不假装有。
3. `strict` xfail 不启用：blocked 是运行时检测的结果，用例一旦没检测到拦截会正常跑断言，
   不存在「意外通过」，所以不需要 strict 兜底。
4. 阈值数字（0.3 / 0.5 / 3 次 / 7 天）是**拍的，不是推导的**。等 ledger 积累 ≥ 14 天真实数据后重新校准，
   校准时把依据写进本文件。
