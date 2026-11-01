# 自动发现机制（DISCOVER）——设计、边界与验收记录

> 2026-11-02 定稿。阶段 1 范围：采集器 + 候选池 + 显式调用点。
> 审批工具与 L2 数据扩展属阶段 2/3，另行实现。

## 0. 一句话定位

测试跑的时候从**被测页面自身**提取正常值域（筛选项/排序/席别/房型…），
与已入库数据集 diff 后写入候选池；人工按「站点 × 维度」成批审批后转正进
`data/*.yaml`，下一轮 parametrize 自动纳入 —— 「越跑越全」的闭环。

**旁路声明**：采集与候选全程不参与断言。不改 passed/failed、不改 XFAIL、
不进 blocked_gate、不进 CI。默认开关 `DISCOVER` 未设 = 关闭 = 与现状零差异。

## 1. 明确不做（2026-11-02 用户裁决）

- **不做异常数据**：空值/超长/特殊字符/非法日期/网络失败/超时/WAF 拦截/
  登录过期/并发冲突一律不测，只测正常业务范围内的多样性；
- **不做随机爬取式探索**：每条发现必须带来源用例与提取器标识，跑探针可复现；
- 不把 `HUMAN=1` 拉进 CI；不改红线五样；不引入 stealth/UA/代理。

## 2. 采集器（utils/discover.py）

### 铁律

1. **兜底优先于采集**：collect 全程 try/except，任何异常只记
   `collect_errors`，不抛出、不影响主判定、不阻断用例。
   验收含「喂错提取器仍全绿」（tools/tests/test_discover.py）。
   兜底边界：捕获 `Exception`；`BaseException`（KeyboardInterrupt /
   SystemExit）按设计穿透，不视为采集失败。
2. **只读 DOM，零新增导航、零额外请求**：JS 提取器不发请求、不滚动、
   不点击、不等待，拿到值立即返回。

### 调用方式：显式调用式（hook 式已否决）

hook 在 teardown 时页面停在用例末尾状态（结果页/面板已关），采到的是
**静默错数据**而非异常 —— 比空值更糟。故采集挂在用例内「结果页已断言」
之后的确定时刻显式调用。若未来重议 hook，必须同时满足：
① 只对 `-m e2e` 用例生效；② 仅 `DISCOVER=1` 执行；③ 非 e2e 轨零影响
（跑 thirdparty 确认），并补「页面存活」证明。

调用点（3 处覆盖 12 条参数化 + 1 处自检 = 13 条用例全接）：
`test_search_chain.py` 三站用例断言后各 1 处；`test_merged_login_state.py`
每站判定后 1 处（自检页是首页，筛选面板未呈现 —— not_present 本身是有效观测）。

### hint 接口（统一两种形态）

```python
Hint = {
    "type": "css" | "js",
    "selector":   str,   # css 形态
    "extractor":  str,   # js 形态：参数化函数体，只读 DOM
    "args":       dict,  # js 形态参数（如 group_title="坐席"）
    "dimension":  str,
    "expected_shape": "list[str]",  # 形状不符记 collect_errors，不抛
}
```

JS 提取器**必须参数化**（args 传入组标题/选择器），同一函数体复用于多维度；
禁止为某站写死不可复用的 JS。现有三支：
`_JS_GROUPED`（火车标题分组，4 维度复用）、`_JS_HOTEL_GROUP`（酒店 5 维度）、
`_JS_FLIGHT_SORT/_JS_FLIGHT_KEYS`（机票，item_sel 参数化）。

### selector 来源：实测，不猜

- 结构勘察：`tools/probes/probe_dom_filters.py`（两轮：全量 class 扫描 →
  种子容器两层级下钻），输出 `reports/probes/dom_filters_*.txt`；
- 定稿验证：`tools/probes/probe_hint_validate.py` 用**真实 HINTS 提取器**
  在三站结果页实跑（2026-11-02 三站全部命中，单维度提取耗时 3–15ms），
  输出 `reports/probes/hint_validate_*.txt`。
- **站点改版后先重跑 probe_hint_validate.py**，哪个 hint 失效一目了然。

已知正常失配形态：值文本混入私有区图标字符（`\ue000-\uf8ff`）由
`normalize` 统一剥除；「更多排序」折叠项由独立 `sort_more` hint 收。

## 3. 候选池（utils/candidate_pool.py）

- 落盘：`reports/candidates/candidates.jsonl`（append-only，每条首字段
  `v: 1` = schema 版本）；归档 `archive/candidates-YYYYMM.jsonl`；
  汇总 `summary.json`（每次 summarize 重生成）。
- 记录字段：value / site / dimension / source_case / source_selector /
  url（脱敏：只留白名单 query 前缀）/ collected_at / round_id /
  est_cost_s / status / batch_id / promoted_to。
- **快照只存 selector + 值列表 + 脱敏 URL，不存 HTML 原文**；
  可复现性靠重跑探针。
- 状态机：`candidate → approved / rejected / promoted`。
- 清理策略（2026-11-02 裁决）：按月归档；promoted/rejected 超 **30 天**
  压缩出主文件；**candidate 永不清理**；待审超 **14 天**在 summary 高亮。

## 4. 四道闸

| 闸 | 内容 | 实现处 |
|---|---|---|
| 1 值域合法 | 值必须来自页面实际渲染的 DOM（提取器天然保证） | discover |
| 2 去重 | 规范化后与池中 (site, dimension, value) 比对 | discover |
| 3 轮次阈值 | 每轮每维度最多 `PER_DIMENSION_LIMIT`(=3) 条；被截的值下一轮继续收（渐进吸纳） | discover |
| 4 可复现 | 必带 source_case + 提取器标识 + 脱敏 URL | discover |

## 5. 转正与审批（阶段 3，未实现）

审批按「站点 × 维度」成批：写 `approve-<batch_id>.yaml` 批文 → 工具生成
case（ASCII id 带 `auto_` 前缀隔离；id 冲突**跳过不炸批**；日期偏移只取
既有档位；expected 只落实测值，城市 id 类须探针实测）→ schema 校验 +
**dry-run 打印将新增 id 列表与 yaml diff，不落盘**；报错整批拒绝。
生成后下一轮 parametrize 自动纳入。

## 6. 运行分层（防时长爆炸）

| 层 | 内容 | 模式 | 触发 |
|---|---|---|---|
| L1 smoke | 现有 `-m e2e` 13 条（采集随跑） | 有头 | 本地/CI 门禁（采集默认关） |
| L2 扩展 | 数据多样性全量（阶段 2） | 有头，酒店可部分无头 | 手动/nightly，非阻塞，`-m l2_data` |
| L3 发现 | 采集 + 候选池 + 覆盖报告 | 跟随 L2 | 同 L2，+0 耗时 |

L2 耗时口径（2026-11-02 审定）：最小集 24 条 = 酒店无头 + 火车/机票有头
H1 ≈ **13.5min**，门禁 ≤15min。`l2_data` marker 隔离三点：注册进
pytest.ini；只带 `l2_data` 不带其他轨 marker；addopts 追加
`-m "not l2_data"` 防裸跑收集（CLI `-m` 覆盖 addopts）。

## 7. 阶段 1 验收记录（2026-11-02）

（验收跑完后由实施者回填：四项全过附实测数据。）
