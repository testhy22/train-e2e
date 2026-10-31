# 已知限制（K-xx）

用途：把「我们改不了、或暂时不改」的东西编号，让它们有资格用
`pytest.skip(reason="known-limit: K-xx ...")` 跳过，并且**排除出门禁分母**。
口径见 `docs/BLOCKED-GATE.md` §1–§2。

规矩：

1. 每条必须有编号、现象、取证时间、**为什么不是缺陷**、**什么条件下可以销号**。
2. 编号一旦用过不复用。销号时把状态改成「已解决 + 日期 + 证据」，不删条目。
3. 只有这里列过的编号才能在代码里 skip；临时跳过的用 `pytest.skip("...")` 会因
   缺 `known-limit:` 前缀被门禁判契约不成立（exit 2）。

---

## K-01 日历控件唤不起

| 项 | 内容 |
|---|---|
| 现象 | 日期输入框点击后，`.widget-calendar-depart-wrap` 面板不再出现；面板出现时带 `.widget-calendar-mask` 遮罩，会拦住搜索按钮点击 |
| 取证 | 2026-10-31，`tools/probes/probe8.py` / `probe_interact.py`，截图见 `docs/locator-evidence/cal-attempt1.png`、`cal-open.png` |
| 已试无效 | 点日期文字、点 `.date` 容器、JS `focus()`、JS 派发 `click`、`hover` 后点击 |
| 为什么不是缺陷 | 这是对方页面的自定义控件行为，与本工程代码无关；加重试只会变成「靠运气过」 |
| 影响 | `test_train_search` 走到 `select_date()` 时抛 `WidgetUnavailable` → 记为 `known-limit` 跳过，不进分母 |
| 销号条件 | 用 1 次只读探针证明日历能稳定唤起（≥3 次连续成功），且 `test_train_search` 连续 3 次通过 |
| 备选方案 | 走 URL 预置 `dDate=YYYY-MM-DD` 直连结果页。**未启用**：⚠️[未查实] 现有证据只是「UI 操作后 URL 出现 dDate」，不是「手工构造 URL 直达生效」。动这条路径前必须先做 1 次只读探针 |

## K-02 预售期上限 15 天（含当日）

| 项 | 内容 |
|---|---|
| 现象 | 用例数据只能取 `T+3 … T+15`，越界由 `utils/data_loader.py` 直接 `ValueError` |
| 依据 | 12306 官方口径 15 天（2026-10 核实） |
| 为什么不是缺陷 | 这是业务约束，不是系统限制 |
| 销号条件 | 官方口径变化时改 `config/settings.py` 的 `MAX_PRESALE_DAYS`，并同步本条 |

## K-03 blocked 不留 trace

| 项 | 内容 |
|---|---|
| 现象 | `blocked` 走 xfail，不属于 failure，`--tracing=retain-on-failure` 不会产出 `trace.zip` |
| 现在留什么 | 视口截图 + 全页截图 + URL + title，写进报告 section |
| 为什么不改 | 改成 `--tracing=on` 会让每次运行都录完整 trace（体积与耗时翻倍）；把 blocked 当失败又违背「blocked 是一等结果」 |
| 销号条件 | 出现「只有 trace 才能定位」的拦截问题，且复现成本高于全量录制 |
