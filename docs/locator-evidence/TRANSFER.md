# 中转结果页：定位器证据源

> 本文只记录**实测到的事实**，不写推测。每条结论都能指回一个探针产物。
> 目的：以后改中转定位器或写断言时，先看这里，别凭"页面上应该有个 XX"重新猜一遍。

- 探测时间：2026-11-02 23:22 ~ 23:24（GMT+8）
- 探测方式：有头（`HEADED=1`）。无头下 trains 子域被 WAF 拦，见 `docs/KNOWN-LIMITATIONS.md` K-05
- 探针：`tools/probes/probe_transfer.py`、`probe_transfer2.py`、`probe_transfer3.py`
- 产物：`reports/probe/transfer_20261102-2322*.json`、`transfer_matrix_20261102-232428.json`、
  `transfer_tree_20261102-2323*.json`（`reports/` 未跟踪，不入库）

---

## 一、URL 口径

`ticketType` 决定页面形态，三者互斥：

| 值 | 形态 | 依据 |
|---|---|---|
| `0` | 单程 | 本项目既有探测（`tools/probes/probe2.py`） |
| `1` | 往返（两栏 + `rDate`） | 2026-11-02 往返探测 + 用户截图 |
| `2` | **中转**（「中转方案推荐」区块） | 2026-11-02 用户截图 2 的真实地址 |
| `gaotie-dongche` | 不是 `ticketType`，是 `trainType` 的取值 —— 「只搜高铁动车」 | 同上截图 URL 里的 `trainType=gaotie-dongche` |

完整形态（截图原文）：

```
https://trains.ctrip.com/webapp/train/list?ticketType=2&dStation=汉口&aStation=深圳&dDate=2026-11-06&trainType=gaotie-dongche…
```

本轨道**只用** `ticketType / dStation / aStation / dDate` 四个参数。「指定中转城市」不传 ——
四组探测数据都没用到它，不凭想象加参数。

> **2026-11-02 变更**：入口不再用 URL 直达（用户裁决子菜单必须点按钮进）。
> 现在流程是 `open() → select_mode("transfer") → 填两地 → 点日期 → 点搜索`，
> 上表的 URL 参数**只作为断言依据**（`url_ticket_type()` / `url_depart_date()` 回读）。
> 详见 `FORM-MODE.md`；「指定中转城市」留空实测可搜（201 张方案卡）。

---

## 二、页面结构（实测 DOM）

```
@ref: transfer_tree_20261102-2323*.json → html_head
<div class="transfer-box">                          ← 整块容器，含标题 + 全部卡片
  <h3>中转方案推荐</h3>
  <div class="card-white list-item">                ← **一张方案卡**（汉口→深圳 205 张）
    <div class="tag-tzhc"></div>                    ← 「同站换乘」标签（204/205 命中）
    <div class="list-bd">
      <div class="from">
        <div class="time">15:43</div>
        <div class="station">武汉</div>
      </div>
      <div class="trans">
        <p>全程5时17分</p>
        <div class="trans-station">深圳北转</div>
        <p>换乘16分</p>
      </div>
      <div class="to">
        <div class="time">21:00</div>
        <div class="station">福田</div>
      </div>
      <div class="rbox">
        <div class="price">559.5</div>
        <ul class="surplus-list">
          <li>第一程 G3785 二等座有票</li>
          <li>第二程 D7565 二等座有票</li>
        </ul>
      </div>
      <button class="btn-orange btn-show">订</button>
    </div>
  </div>
  <div class="list-open">…</div>                     ← **详情面板**，与卡片 1:1（205=205）
</div>
```

祖先链（由内往外）：`.transfer-box` → `section` → `.train-content` →
`.train-wrapper train-flex pb120` → `div` → `div`

---

## 三、三条硬结论（每条都推翻过一版错的写法）

### 1. `.transfer-box` 是容器，不是卡片

第一轮探针把 `chain` 当成"由外往内"读，于是把 `.transfer-box` 判成单张卡，写下了
`box.querySelectorAll("section")` —— 结果恒为 **0**（`section` 是它的**父**，不是子）。
症状：`title` 能拿到、卡片数永远是 0，看起来像"页面没渲染方案"。

正确：卡片 = `.transfer-box .card-white.list-item`。

### 2. `.list-open` 是详情面板，数与卡片一一对应

汉口→深圳 实测：卡片 **205**，`.list-open` 也是 **205**。
数错任何一边都会让「方案条数」正好翻倍或对不上。
定位器里显式写了 `:not(.list-open)` 防御（当前 `.list-open` 不带 `list-item`，但两者
数量一一对应，万一站点给它加上就会翻倍）。

### 3. 卡片 class 与单程/往返**相同**（`.card-white.list-item`）

不存在 `.transfer-list-item` 这种专用 class。想靠 class 名区分"这是中转卡"行不通；
区分方式看**内容**：卡内有 `.trans-station`（「深圳北转」）与 `.rbox .surplus-list li`
（「第一程 / 第二程」）。

---

## 四、定位器对照表

| 用途 | 选择器 | 实测值 |
|---|---|---|
| 整块容器 | `.transfer-box` | 1 个 |
| 区块标题 | `.transfer-box h3` | 文本恒为「中转方案推荐」 |
| 方案卡 | `.transfer-box .card-white.list-item:not(.list-open)` | 汉口→深圳 205 / 北京→上海 144 / 广州→东莞 196 |
| 出发时刻/站 | `.from .time` / `.from .station` | 「15:43」/「武汉」 |
| 到达时刻/站 | `.to .time` / `.to .station` | 「21:00」/「福田」 |
| 中转站 | `.trans-station` | 「深圳北转」「广州南转」「南京南转」… |
| 换乘时长 | `.trans p` 第二段 | 「换乘16分」 |
| 票价 | `.rbox .price` | 「559.5」 |
| 两段车次 | `.rbox .surplus-list li` | 「第一程 G3785 二等座有票」 |
| 同站换乘标签 | `.tag-tzhc` | 汉口→深圳 204/205 |

---

## 五、用户方案里被实测推翻的条目

| 方案写法 | 实测 | 后果 |
|---|---|---|
| `page.locator("text=中转")` 当单选框点 | 首页「中转」是个 **`button`**（不是 radio，选中态落在 `li.cur`），且点击后还要填 4 个控件 | ~~走 URL 直达，不点它~~ → **2026-11-02 用户裁决改为必须点它**，4 个控件已探完（`FORM-MODE.md`），入口改走 `select_mode("transfer")` |
| `input[placeholder='出发城市']` | 本项目表单实际是 `#label-departStation` 那套 | 选择器不存在 |
| `.transfer-list-item` | **不存在**。卡片就是 `.card-white.list-item` | 计数恒为 0 |
| `text=中转方案推荐` | 真实存在，但它是 `<h3>`，父容器 `.transfer-box` | 可用，取 `.transfer-box h3` 更稳 |
| `page.content()` 里查城市名 | 整页 HTML 里当然有城市名，几乎恒真 | 假断言，已改为断言 `.from .station` / `.to .station` |
| `page.keyboard.press("Enter")` 选联想第一项 | 本项目 K-01/K-11 记录过：回车会选中"北京北"这类错误项 | 往返/单程都走精确文本匹配，不按回车 |

---

## 六、数据可用性（2026-11-02 实测，`transfer_matrix_20261102-232428.json`）

| 区间 | 日期 | 偏移 | 方案卡 | 含两段车次号 | 备注 |
|---|---|---|---|---|---|
| 汉口→深圳 | 2026-11-06 | +4 | 205 | 205 | 用户 Case 1 |
| 北京→上海 | 2026-11-15 | +13 | 144 | 143 | 用户 Case 2；差 1 条是 S 字头未识别，已修 |
| 广州→东莞 | 2026-11-14 | +12 | 196 | 181 | 用户 Case 3 的区间；差额全是 `S` 字头 |
| 广州→东莞 | 2026-11-20 | +18 | 70 | 54 | 用户 Case 3 原日期，**超本站 15 天预售期口径** |

`S` 字头（市郊列车）已补进 `TRAIN_NO_RE`，补后三组数据的两段车次号覆盖率均为 **100%**。

### 关于 +18 天那条

实测 2026-11-20（今天 +18）仍能查出 **70 个方案**，说明站点实际接受期比
`settings.MAX_PRESALE_DAYS = 15` 长。本轮**没有**改那个常量：

- 它同时被单程（`3..15`）与往返校验引用，改它属基础设施改动，会影响两条既有轨道；
- 所以数据里第三条取 `+12`（在售，实测 196 个方案），+18 的实测数字保留在本表，不丢。

要放宽的话，应当单独一轮评估「站点真实预售期」，而不是在一条用例的数据文件里悄悄写大。

---

## 七、未查实项（别当成已知）

- **「指定中转城市」输入框**：截图里存在，但本轨道不用，未探测其 DOM 与行为。
- **车型筛选（`trainType`）的完整枚举**：只知道 `gaotie-dongche` 一个值，其余未探。
  所以中转轨道**不做**车型过滤，也不断言字头集合。
- **方案排序规则**：205 条里先出哪些、是否有"推荐"权重，未探；用例不依赖顺序。
- **分页/懒加载**：`.transfer-box` 内 205 张卡一次性都在 DOM 里，未验证是否有滚动分页。

---

## 八、运行记录（2026-11-02 23:28，有头 + 一次登录）

```
HEADED=1 HUMAN=0 E2E_CLEAN_HOURS=72 pytest suites/thirdparty/tests/test_train_transfer.py -v -rA
→ 3 passed in 23.02s
```

| 区间 | 方案卡 | 两段车次号 | 覆盖率 | 字头分布 |
|---|---|---|---|---|
| 汉口→深圳 | 205 | 205 | 100% | C13 D26 G320 K33 T2 Z16 |
| 广州→东莞 | 196 | 196 | 100% | C163 D83 G121 K4 S17 Z4 |
| 北京→上海 | 144 | 144 | 100% | C11 D10 G263 K1 S1 T1 Z1 |

用 `shared_page`（session 级 context），三条共用一次登录。
