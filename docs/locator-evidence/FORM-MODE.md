# 首页表单三态（单程/往返/中转）：定位器证据源

> 本文只记录**实测到的事实**，不写推测。每条结论都能指回一个探针产物。
> 目的：改「子菜单怎么进」这件事之前先看这里，别凭"页面上应该有个 radio"重新猜一遍。

- 探测时间：2026-11-02 23:56 ~ 23:58（GMT+8）
- 探测方式：有头（`HEADED=1`）。无头下 trains 子域被 WAF 拦，见 `docs/KNOWN-LIMITATIONS.md` K-05
- 探针：`tools/probes/probe_form_mode.py`（只读结构快照）、`probe_form_mode2.py`（真点真搜）
- 产物：`reports/probe/form_mode_20261102-2357*.json`、`form_mode2_20261102-2358*.json`（`reports/` 未跟踪，不入库）

## 起因

往返与中转两条轨道原先是**改 URL 直达结果页**（`?ticketType=1/2&…`）。2026-11-02 用户裁决：

> 往返要求通过点击按钮、输入框输入值的方式测试，不是通过改变 URL 进行测试；
> 中转也按要求修改。只有回到大的菜单（`https://trains.ctrip.com/`）才能通过 URL 的方式，
> 子菜单都需要通过点击按钮的方式进入。

于是本轮把两条轨道的入口全换成首页表单，URL 只留作**断言依据**。

---

## 一、三态切换项：是 `button`，不是 `radio`

```
ul.search-select
  └─ li.cur          ← 选中态唯一落点（当前态）
       └─ button     ← 真正可点的元素，文本恰好是「单程」/「往返」/「中转」

  └─ li
       └─ button
```

实测数据（`form_mode_20261102-2357*.json → modes`）：

| 态 | 可见候选数 | 真正的按钮 | 父级 | 选中态落点 |
|---|---|---|---|---|
| 单程 | 2（`BUTTON` + `LI`） | `<BUTTON>`（无 class、无 id） | `LI.cur` | `li.cur` |
| 往返 | 2 | 同上 | `LI` | 点到后变成 `LI.cur` |
| 中转 | 2 | 同上 | `LI` | 同上 |

两个必须记住的坑：

1. **`aria-checked` 与 `input[checked]` 全是 `None`** —— 用 `get_by_role("radio")` 拿不到任何东西，
   判"当前在哪一态"只能读 `ul.search-select li.cur button` 的文本。
2. 每个态在页面上有 **2 个**候选（外层 `li` 与内层 `button`），按文本 `exact` 会同时命中两个。
   定位器必须收敛到 `button`，否则点 `li` 也能生效但拿不到稳定的 selected 语义。

## 二、三态的 `<input>` 集合完全相同

四个态快照里带 id 的 input 恒为这 7 个，**一个不多一个不少**：

```
_allSearchKeyword, label-arriveStation, label-departDate, label-departStation,
label-returnDate, label-transitStation, page_id
```

即三态**不是**换一套表单，而是同一张表单按态显隐。这直接解释了"返程日历当年没探过"——
它一直在 DOM 里，只是单程态不可见。

## 三、日期块：任何态都返回 2 个 `div.date`

```
div.search-date
  ├─ div.date   ← 出发日期（三态恒可见）
  └─ div.date   ← 返回日期（只有往返态可见）
```

| 态 | `div.search-date div.date` 个数 | 第 2 个的 `visible` | 第 2 个的边界框 | 第 2 个的文本 |
|---|---|---|---|---|
| 单程 | 2 | `false` | `[0,0,0,0]` | `返回日期取消返程2026-11-04星期三` |
| 往返 | 2 | `true` | `[1066,285,244,76]` | `返回日期 2026-11-04 星期三` |
| 中转 | 2 | `false` | `[0,0,0,0]` | 同单程 |

⇒ 取日期块必须**按"可见 + 文本前缀"筛**（`DATE_BLOCK_ANY` + `DATE_BLOCK_WORD`），
不能按 index 写死，也不能用 `.count()` 判断"这态有没有返回日期"（永远返回 2）。

## 四、两个日历：`depart` 与 **`back`**

| 态 | 点第 1 个日期块弹出的 wrap | 点第 2 个日期块弹出的 wrap |
|---|---|---|
| 往返 | `.widget-calendar-depart-wrap` | **`.widget-calendar-back-wrap`** |

**是 `back` 不是 `return`** —— 实测 `.widget-calendar-return-wrap` 数量为 **0**。
按"返回日期 → return"的直觉写，会找不到日历，然后误报成"返程日历没弹出来"。

两者内部同构，所以翻月/点日逻辑可以按 `which` 复用一套实现：

```
div.widget-calendar-{depart|back}-wrap       ← overflow 容器，宽度 0，不能当"已弹出"锚点
  └─ div.widget-box.widget-calendar
       ├─ ul.widget-calendar-hd > li > h3     ← 双月标题：['2026年 11月', '2026年 12月']
       └─ ul.date-bd × 2                      ← 与标题同序，各 30/33 个 li > strong
```

## 五、中转态多出的「指定中转城市」

```html
<div class="search-trans">
  <form class="city">
    <label for="label-transitStation">指定中转城市</label>
    <input id="label-transitStation" type="text" placeholder="中转城市" class="input-txt" autocomplete="off">
  </form>
</div>
```

**留空可以直接搜**（实测汉口→深圳 201 张方案卡，标题「中转方案推荐」）。
本轨道因此不填它 —— 填它要另探一套联想面板，没有实测样本就不写定位器（K-01 那类假通过）。

## 六、点按钮 vs URL 直达：结果页逐参数等价

同一天同一区间，两条路径产出的结果页 URL：

```
点「往返」后搜索：
/webapp/train/list?ticketType=1&dStation=%E6%B1%89%E5%8F%A3&aStation=%E6%B7%B1%E5%9C%B3
                   &dDate=2026-11-06&rDate=2026-11-08&trainsType=&hubCityName=&highSpeedOnly=0
点「中转」后搜索：
/webapp/train/list?ticketType=2&…&dDate=2026-11-06&rDate=&trainsType=&hubCityName=&highSpeedOnly=0
```

⇒ 走 UI **不改变被测对象**，只是把"表单这条路通不通"也纳入覆盖。
`ticketType` 因此成了"点对了子菜单按钮"的唯一硬证据（0=单程 / 1=往返 / 2=中转），
用例里必须断言它 —— 否则"点了往返其实还在单程态"会因结果页同样有车次列表而蒙混过关。

---

## 七、改走 UI 后暴露的两个真实缺陷

这两条都不是"表单不好用"，是**取值时机与点击时机**的问题，改 URL 时被掩盖了。

### 7.1 首卡可见 ≠ 整栏文本就绪（覆盖率凭空掉 22 个点）

`rt-hk-sz-weekend-T+3`（汉口→深圳 2026-11-06）首跑：去程 55 张卡都在 DOM 里、
第一张也可见，但只有 43 张的 `innerText` 有内容，另 12 张是空壳
⇒ 覆盖率算成 **78%（43/55）**，红在 90% 门槛上。

同一 URL 直接 `goto`（等得更久）时 **55/55 全有文本**。
⇒ 差异不是数据不同，是**取值时机**不同；`expect(cards.first).to_be_visible()` 是个过早的信号。

修法：`leg_texts` 里加 `_wait_leg_text_ready()` —— 用 `wait_for_function` 轮询
「非空文本的卡片占比 ≥ 95%」，超时就用当前快照交给覆盖率断言去红。
判据**不用车次号覆盖率**，否则要在 JS 里复刻 `TRAIN_NO_RE`，等于把车次号口径写成两套。

### 7.2 点选瞬间面板消失 → 15s click 超时（K-11 变体）

`rt-wc-hk-samecity-back-T+8`（武昌→汉口）在 HUMAN=1 拟人节奏下失败：

```
playwright._impl._errors.TimeoutError: Locator.click: Timeout 15000ms exceeded.
  waiting for locator("div.depart-station-picker-wrap ul.widget-search-bd").first.locator("li").nth(1)
```

`_click_option` 里 `self.human.click(items.nth(i))` 抛的是 `PlaywrightTimeout`，
而外层 `select_city` 的 3 轮重试只接"返回 False"这一种失败形态 ⇒ 一次瞬时重排直接炸成 failed。

修法：`_click_option` 里捕获 `PlaywrightTimeout` → 记 `(点击瞬间消失)` 进 `_pick_trace` → 返回 False，
交给既有的 3 轮重试。**没有放宽标准**：3 轮都选不上仍然抛 `WidgetUnavailable`（转 skip known-limit）。

---

## 八、操作链自证

用户要求"每一步操作后保留可追溯性"，所以 `TrainSearchPage` 记 `self.actions`，
每个真实 UI 动作一行：`[序号] 控件类型 | 定位方式 | 执行动作 | 等待条件 | 结果断言`，
由用例写进 `call` 报告节（`操作链`）。

同时 POM 覆写 `goto()` 做计数（`self.navigations`），用例断言 **`navigations == 1`**：
整条用例只允许 `open()` 导航一次，多出来的那次就一定是子菜单 URL 直达。
这条断言比"代码里没写 goto"更硬 —— 它连间接调用也一起挡住。

---

## 九、跑通记录与一次站点降级（2026-11-03）

### 9.1 改走 UI 后全量跑通

`HEADED=1 E2E_CLEAN_HOURS=72 pytest test_train_search.py test_train_round_trip.py test_train_transfer.py -q -rA`
⇒ **15 passed / 308.88s**（单程 3 + 往返 9 + 中转 3，当时还没上「每功能 3 条」配额）。
每条往返用例的操作链都是 7 步、中转 6 步，末行固定 `页面导航次数（只允许 open() 那一次）：1`。

### 9.2 每功能 3 条配额（用户 2026-11-02 裁决）

同一条命令在配额生效后收集 **9 条**（3+3+3）。往返由 `utils/case_data.QUOTA_PICK` 挑
`rt-gz-cs-near / rt-bj-gz-night / rt-hk-wc-samecity`，四维度（城市组合 3/3、字头 G/D/C/Z/T/K、
跨度 3..12 天、时段 3/3）都留在里面；`E2E_FULL=1` 才跑全部 9 条。

### 9.3 单程直达列表被站点降级（不是本仓库代码）

00:19 单程 `gz-sz-T+10-2026-11-13` 取到 **521 条**（点到点 516、覆盖率 100%）；
00:27 起同一条只剩 **5 条**（点到点 0），页面显示「共0车次」+「未找到符合条件的车次」，
下方仍有「中转方案推荐」区。

排除链（矩阵探针 `tools/probes/probe_degrade.py`）：

| 变量 | 结果 | 结论 |
|---|---|---|
| 匿名 vs 登录态 | 都是 5 条 | 与登录态无关 |
| 三组区间/日期（广深 11-13、广深 11-14、沪京 11-10） | 全 5 条 | 与单条数据无关 |
| 往返（`ticketType=1`）/ 中转（`ticketType=2`） | 同期仍 193~205 张卡 | 不是整站不可用 |
| 表单回填 / URL `dDate` / 页面渲染 | 全部正确 | 不是选择器或交互错 |

⇒ 站点侧状态（用户判断「估计是被限制了」）。处置：**不改选择器、不放宽阈值**，
在单程用例里加显式归因断言（`declared == 0` 且出现「未找到符合条件的车次」时直接
`pytest.fail` 并说明"直达列表为空、不是选择器问题"）。完整记录见
`docs/KNOWN-LIMITATIONS.md` **K-13**。
