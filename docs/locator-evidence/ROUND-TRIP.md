# 往返结果页 · 定位器证据源（2026-11-02）

本文件保存 `pages/train_search_page.py` 里**往返相关定位器**背后的实测证据，
目的只有一个：**别人改这几个定位器时，先看到它们是从哪来的**，而不是照着"看起来合理"重写一遍。

> 规矩（与 POM 文件头一致）：没有实测记录的定位器不写进代码。
> 想加新选择器，先跑探针、把证据贴到这里、再改代码。

## 探测方法

| 探针 | 目的 | 产物 |
|---|---|---|
| `tools/probes/probe_round_trip.py` | 整体结构：容器 / 标题 / 车型 chip / 日期条 | `reports/probe/round_trip_<ts>.json` |
| `tools/probes/probe_round_trip2.py` | `.list-item` 按 className 分组、两栏归属、车次号承载元素 | `reports/probe/round_trip2_<ts>.json` |
| `tools/probes/probe_round_trip3.py` | 时段 chip 的 class 与"点了是否生效" | `reports/probe/round_trip3_<ts>.json` |
| `tools/probes/probe_round_trip_verify.py` | 同城两站区间的真实条数（给 `min_results_each_leg` 定下界） | `reports/probe/round_trip_verify_<ts>.json` |

环境：`HEADED=1`（无头下整站 432，见 KNOWN-LIMITATIONS K-05），
`storage_state=config/auth/ctrip_state_trains.json`，viewport 1600×1000，zh-CN，Asia/Shanghai。

探测 URL（参数口径来自身 2026-11-02 用户截图里的真实地址）：

```
https://trains.ctrip.com/webapp/train/list?ticketType=1&dStation=汉口&aStation=深圳&dDate=2026-11-06&rDate=2026-11-08
```

## 结构事实（实测，不是推断）

### 1. 双栏只有一个外层容器

```
div.train-wrapper.return-view
└── div.return-box                     ← 唯一的外层；**没有** .depart-box 这种东西
    ├── div.return-hd
    │   ├── h3  去程
    │   └── h3  返程
    └── div.return-list  × 2           ← 0 = 去程，1 = 返程
        ├── ul.date-list   （5 天日期条：11-05 周四 / 11-06 周五 …）
        ├── div.item × 4   （车型：高铁(G/C) 动车(D) 普通(Z/T/K) 其他(L/Y)）
        ├── div.item × 4   （出发时间：0点 - 6点 / 6点 - 12点 / 12点 - 18点 / 18点 - 24点）
        └── section
            └── div.card-white.list-item    ← 真车次卡
```

> 注意 `div.item` 的文本带空格：是 `6点 - 12点`，不是 `6点-12点`。
> 第一次按 `^6点-12点$` 找，一条都命中不了（probe3 首次返回空数组）。

### 2. `.list-item` 被复用 —— 这是最大的坑

2026-11-02 实测：`.list-item` 共 **114** 个，分两类：

| className | 数量 | 第一条文本 |
|---|---|---|
| `card-white list-item` | **112** | `09:49 汉口 5时48分 G1030 15:37 深圳北 640.5 订 二等座有票 …` |
| `card-white list-item station-filter-bd` | **2** | `精确搜索 汉口` |

⇒ 直接用 `.list-item` 计数会把车次数**虚报 +2**，并把"精确搜索"卡塞进车次号覆盖率的分母。
POM 里因此用 `.card-white.list-item:not(.station-filter-bd)`。

### 3. 车次号在 `div.checi` 里，是完整文本

```
div.mid
└── div.checi   ← 文本就是 "G1030" / "Y719" / "D105"，没有被拆成 <span>G</span><span>1030</span>
```

⇒ 不需要"抓子节点再拼"，整卡 `innerText` + 按 token 切分即可（POM 的 `train_number_of`）。

### 4. 卡片文本的两种开头

```
09:49 汉口 5时48分 G1030 15:37 深圳北 640.5 订 二等座有票           ← 有票卡：行首是出发时刻
成功率：较高 20:21 汉口 0时18分 D105 20:39 武昌 5 抢 暂无余票…      ← 抢票卡：行首是「成功率」
```

⇒ 取出发时刻不能用 `^\d{1,2}:\d{2}` 锚行首（抢票卡会全部取不到），要用 search 取第一个 HH:MM。

### 5. Y 字头真实存在（这次修覆盖率缺口的起因）

汉口↔武昌（同城两站）实测出现 `Y719 / Y713 / Y720` 三趟旅游/临客列车。
旧正则 `^[GDCZTK]\d{1,4}$` 漏认 ⇒ 该栏覆盖率 4/6 = 67%，往返用例红在「抽不到车次号」。
现正则为 `^(?:[GDCZTKY]\d{1,4}|\d{4})$`。

**只加了 Y。** L（临时）、S（市郊）没有实测记录，不凭"应该也有"扩字头；
真要加，先在这里补一条实测证据。

## 未查实项（别当成已知）

| 项 | 状态 | 影响 |
|---|---|---|
| 时段 chip 的归属容器 | ⚠️**未查实**：`div.item` 能找到（probe3 第二次），但"限定在第 1 栏内点击"两次都超时；`closest('div.return-list')` 对两栏的 chip 都返回 0，说明它不在 `return-list` 子树里 | 时段维度**不做硬断言**，只统计结果里落在偏好时段的趟数（`prefer_window` 软指标） |
| 车型筛选的 URL 参数取值 | ⚠️**未查实**：截图里 `trainType=gaotie-don…` 被截断，实际枚举没探 | 车型只做"至少命中一种 + 字头分布"，不断言"结果里只有这些" |
| 返程日是否受 15 天预售期限制 | 实测反证：截图 `dDate=2026-11-06&rDate=2026-12-09`（差 33 天）页面正常接受 | 数据校验对返程**不设上限**，只要求晚于出发日 |
| 首页「往返」单选 + 返程日历 | ⚠️**未探测** | 往返走 URL 直达；首页表单那条路由单程冒烟守着 |

---

## 运行记录（2026-11-02 22:36，同一提交、同一登录态，只切 `HEADED`）

| 模式 | 命令 | 结果 | 判读 |
|---|---|---|---|
| 无头 | `HEADED=0 HUMAN=0 E2E_CLEAN_HOURS=72 pytest suites/thirdparty/tests/test_train_round_trip.py -q -rx` | **9 xfailed / 0 passed，12.23s** | 9 条逐条原因均为 `blocked: waf_block（whaleguard）`，**无一进入 DOM 断言**（1.4s/条 ⇒ 在 `open_round_trip` 的 `assert_not_blocked()` 就被拦）。属 K-05 已知限制，不是本轨道代码问题 |
| 有头 | `HEADED=1 HUMAN=0 E2E_CLEAN_HOURS=72 pytest …` | **9 passed，107.84s** | 同一批数据、同一 URL 直达路径；单条 8~14s，说明确实走了两栏取数与车次号覆盖率断言 |

结论：

1. **无头不可用的原因不是视口/UA，而是「有没有真实显示设备」** —— 真视口问题会表现为「页面出来了但元素不可见」，这里连页面都没拿到（否则单条不可能 1.4s 就结束）。
2. **不进 CI 的顾虑不成立**：`.github/workflows/ci.yml:75-101` 用 `xvfb` 提供虚拟屏 + `HEADED=1 HUMAN=0` 跑 `-m ui`，本来就是有头模式，不依赖无头。
3. 因此**不引入 stealth / UA 伪装 / viewport 调整**去绕 WAF：与拦截机制无关（同机改一个开关就 200），且违反项目「不伪装身份」的硬约定（`TODO.md` 附录第 2 条）。
4. 复跑命令固定带 `E2E_CLEAN_HOURS=72` 规避 K-12（清理器 4 个/秒）。**不要用 `E2E_CLEAN_DISABLE=1`**，那会让 `test_cleaner` 4 条全红。
