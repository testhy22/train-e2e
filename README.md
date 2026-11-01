# ctrip-train-e2e

携程火车票查询的浏览器驱动 E2E，按 Page Object Model 组织，数据驱动，失败留证。

**定位：合规、不绕过任何风控。** 目标站点拒绝自动化访问时，本工程不会伪装身份继续访问，
而是把 `blocked` 当作一等结果记录进报告。这不是妥协，是设计前提。

---

## 一、工程规则（原文，接手人请照此执行）

1. 禁止把定位器和业务操作写在测试函数里；测试只做「编排 + 断言」，元素与交互封装在 `pages/`。
2. **禁止用裸 `time.sleep()` / `page.wait_for_timeout()` 做同步等待**，一律用 Playwright
   自动等待或显式 `expect(...)` 条件等待。
   —— 允许：`expect(locator).to_be_visible()`；以及 `utils/humanize.py` 的拟人节奏
   （只在动作之间停顿，**不得**用来等页面状态，边界见该文件头）。
3. 测试数据不得硬编码在测试文件内，全部写进 `data/**/*.yaml`，经 `utils/case_data.py` 加载。
4. 失败必须留下证据：截图 + URL + title 写进报告；trace 用 `retain-on-failure`。
5. 选择器优先语义定位（`get_by_role` / `get_by_placeholder` / `get_by_text`），避免层级 CSS。
6. 城市输入优先 `fill()`；实测 `fill` 触发不了联想时才退到 `press_sequentially`，
   并在注释里写明「功能必要，非规避手段」。
7. 每条用例可独立运行，不依赖前一条的状态。
8. **不写任何反检测代码**：不改 UA、不加 `--disable-blink-features=AutomationControlled`、
   不注入 stealth 脚本、不按「哪里被拦」调参重试。被拦就如实记录。
   拟人节奏（`HUMAN`）只调**时间**、不碰**身份**，用途是本地有头时让人看清操作；
   它**不是**规避手段，CI 里恒关闭（`HUMAN=0`）。

---

## 二、跑起来

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium        # 需与 playwright 版本匹配，见「常见问题」

python -m pytest                             # 全量（含 27 条数据层单测）
scripts/run-headed.sh suites/hotels -m hotels    # 有头，本地看窗口（见下节）
BASE_URL=https://your-site.example python -m pytest suites/thirdparty   # 换目标站
```

`BASE_URL` 默认值在 `config/settings.py` 的 `DEFAULT_BASE_URL`，环境变量优先。
除这一处外，代码里不允许出现硬编码 URL、超时数值、路径。

### 常用命令（两条轨道各一份 junit，各判各的）

```bash
# 只看「今天能不能打开」
python -m pytest suites/thirdparty -m ui -k smoke -q

# 火车票轨道（scope=ui）
python -m pytest suites/thirdparty -m ui --junitxml=reports/junit-ui.xml
python tools/blocked_gate.py reports/junit-ui.xml --scope ui

# 酒店轨道（scope=ui_hotels）
python -m pytest suites/hotels -m hotels --junitxml=reports/junit-hotels.xml
python tools/blocked_gate.py reports/junit-hotels.xml --scope ui_hotels
```

门禁退出码：`0` 绿/黄，`1` 红，`2` 口径不成立。

`-m ui` / `-m hotels` 不是装饰：不带它跑出来的 junit 里混着 27 条数据层单测，
门禁会直接判「口径混算」并拒绝出数（见 `docs/BLOCKED-GATE.md` §1）。

`--scope` 也不是装饰：**G3/G4 只在同一 scope 内累计**。两个站点的拦截率不可比，
混算时酒店那条 25% 会把火车票的 1.0 序列打断，G3 永不触发。`--scope` 与 junit 口径
不一致会 exit 2，不会「忘了改一处还照常出数」。

### 本地可见调试（有头，仅本地）

```bash
scripts/run-headed.sh suites/hotels -m hotels
SLOWMO=800 scripts/run-headed.sh suites/thirdparty -m ui     # trains 还没接拟人，要慢放
```

脚本固定 `HEADED=1 HUMAN=1`。四个开关（`HEADED` / `HUMAN` / `HUMAN_SEED` / `SLOWMO`）、
Linux 无显示服务时的行为、与 CI 的差异表，全部在
[`docs/HEADED-DEBUG.md`](docs/HEADED-DEBUG.md)。

两条要点：

- `HEADED=1` 会自动开拟人，**默认不要再叠加 `--slowmo`**，看不清再加 `SLOWMO=300/800`。
- 拟人只调时间不碰身份，被拦照旧记 `blocked` → `xfail`，**不因为被拦就调慢重试**。

---

## 三、目录与职责

```
config/settings.py          唯一配置出口：站点、超时、视口、预售期常量、证据目录
pages/base_page.py          有界等待、风控识别（detect_block）、失败截图、拟人原语入口
pages/hotel_search_page.py  酒店页 POM：目的地/日期/房间住客/提交并抓请求参数
pages/train_search_page.py  火车票页 POM：城市选择、日历选择、搜索、取结果状态
utils/case_data.py          用例数据统一入口：data/ 定位、version 包装、CASE_DATA_DIR
utils/hotel_data.py         酒店数据解析：偏移量+晚数 → 具体日期，字段校验
utils/data_loader.py        火车票数据解析：偏移量 → 日期，预售期与 id 校验
utils/humanize.py           拟人操作节奏（只调时间不碰身份，边界见文件头）
data/hotels/search_cases.yaml  酒店用例数据
data/trains/smoke_cases.yaml   火车票冒烟数据
suites/hotels/tests/        track B：酒店首页模块
  test_hotel_search.py      四字段搜索：断言提交时真实发出的 /hotels/list 参数
suites/thirdparty/tests/    track B：火车票
  test_smoke.py             冒烟：只打开页面、检测 blocked、记录 title/URL
  test_train_search.py      功能：三层断言（结构 / 内容 / 辅助）
  test_calendar_widget.py   K-01 的活样本，故意 skip，用来说明 known-limit 口径
  test_data_loader.py       数据层单测（30 项，不需要浏览器，不带 ui 标记）
suites/regression/          track A：容器靶站，空骨架，等 M1-3
tools/blocked_gate.py       blocked 门禁判定器（规范：docs/BLOCKED-GATE.md）
tools/ledger.py             ledger 记录的结构与校验
tools/ledger_append.py      追加到 ops/metrics 分支并读回校验
tools/tests/                门禁、账本、拟人节奏、数据质量的单测（69 项，不需要浏览器）
tools/probes/               一次性定位器探针（不参与测试运行，留作取证）
scripts/run-headed.sh       本地可见调试入口（有头 + 拟人）
docs/BLOCKED-GATE.md        判定规则的唯一来源
docs/HEADED-DEBUG.md        本地可见调试与用例数据目录（HEADED/HUMAN/SLOWMO/SEED、CASE_DATA_DIR）
docs/KNOWN-LIMITATIONS.md   K-01 … K-05
docs/locator-evidence/      探针结果 JSON 与拦截截图（定位器的取证来源）
conftest.py                 浏览器上下文、scope 标记、blocked→xfail、known-limit→skip、失败取证
```

---

## 四、blocked 是一等结果

| 情况 | 处理 | 报告表现 |
|---|---|---|
| 页面正常 | 继续跑断言 | `passed` |
| 滑块 / 验证码 / 限流文案 / WAF 纯文本页 | `pytest.xfail(reason="blocked: <signal>")` | `XFAIL` + 截图 + URL |
| 自定义控件（日历）没按预期响应 | `pytest.skip(reason="known-limit: K-01 ...")` | `SKIPPED` + 截图 + URL |
| 断言失败、元素超时 | 普通失败 | `FAILED` |

`blocked` 用 xfail 而不是 fail，是因为它不说明代码坏了，只说明站点当天拒绝了自动化访问。
**但它绝不用于掩盖普通失败**：`blocked_guard` 只捕获 `BlockedByAntiBot`，
`widget_guard` 只捕获 `WidgetUnavailable`，其余异常原样上抛。

**为什么分成两种前缀、怎么算、多红才算红** —— 全部在
[`docs/BLOCKED-GATE.md`](docs/BLOCKED-GATE.md)。本节不复制那些规则：
复制过的那份迟早跟规范不一样，而没人会发现自己看的是旧版。

---

## 五、证据在哪

| 产物 | 路径 | 何时产生 |
|---|---|---|
| HTML 报告 | `reports/html/report.html` | 每次都产生 |
| 失败/被拦截图 | `reports/screenshots/<nodeid>__viewport.png`、`__full.png` | 失败或 blocked 时 |
| trace | `reports/trace/<用例名>/trace.zip` | **仅真实失败时**（`retain-on-failure`） |
| junit | `reports/junit-ui.xml`（门禁只认这份）| 带 `--junitxml=reports/junit-ui.xml` 时 |
| 门禁记录 | `reports/gate-record.json` | `tools/blocked_gate.py` 每次运行都写 |

⚠️ 注意一条实测事实：**blocked 走的是 xfail，不算 failure，所以不会产出 trace**。
若需要「被拦也留 trace」，只能把 `--tracing` 改成 `on`（每次都留，代价是体积与耗时），
或把 blocked 当失败处理（与本节设计冲突）。当前选择是「blocked 不留 trace，但必有截图 + URL」。

---

## 六、定位器从哪来（不是猜的）

2026-10-31 用 `tools/probes/` 下的脚本逐个实测，原始输出存 `docs/locator-evidence/`：

| 元素 | 定位器 | 依据 |
|---|---|---|
| 出发城市 | `#label-departStation` | probe.json：`id` 稳定，placeholder=出发城市，可见 |
| 到达城市 | `#label-arriveStation` | 同上 |
| 日期输入 | `#label-departDate` | 真实 `<input>`，被 `assist-*-dom` 类隐藏，值形如 `2026-10-31` |
| 日历容器 | `.widget-calendar-depart-wrap` | probe8：遮罩 `.widget-calendar-mask` 会拦住搜索按钮点击 |
| 搜索按钮 | `role=button[name="搜索"]` | probe.json buttons 列表 |
| 结果列表项 | `.list-item` | interact2.json：`card-white list-item`，文本含「06:10 广州东 1时12分 C7001」 |
| 车次号 | `^[GDCZTK]\d{1,4}$` | 结果页实际值 C7001 / G3017 / D7577 / G6025 |
| 结果页 URL | `/webapp/train/list?...&dDate=YYYY-MM-DD` | 实点搜索后抓到的真实 URL |

页面改版时**只改这一个文件**，测试不用动。

---

## 七、本轮实测结果（2026-11-01 00:06，M1-2 合入后，`-m ui` 口径）

```
1 skipped, 27 deselected, 4 xfailed, 1 warning in 6.47s
XFAIL   test_train_search[chromium-sh-bj-T+7-2026-11-08]  - blocked: waf_block（whaleguard）
XFAIL   test_train_search[chromium-gz-sz-T+10-2026-11-11] - blocked: waf_block（whaleguard）
XFAIL   test_train_search[chromium-hz-nj-T+13-2026-11-14] - blocked: waf_block（whaleguard）
XFAIL   test_smoke::test_homepage_reachable[chromium]      - blocked: waf_block（whaleguard）
SKIPPED test_calendar_widget                              - known-limit: K-01 日历控件唤不起
```

门禁对这份 junit 的判定（`tools/blocked_gate.py`，退出码 1）：

```
scope=ui  total=5  counted=4  blocked=4  pass=0  failed=0  known-limit=1  ratio=100.0%
verdict: RED   触发: G1, G2
```

对应的记录已写进 `ops/metrics` 分支（第 2 行，v1 schema），追加方式：
父提交指向当时 tip，快进 push，再从远端读回逐行校验。

注意 `total=5` 与 `counted=4` 的差：K-01 那条 skip 不进分母，
所以 `ratio` 是 4/4 而不是 4/5 —— 这是口径，不是笔误（见 `docs/BLOCKED-GATE.md` §2）。

另一条轨道（酒店一期，2026-11-01 00:31，`-m hotels`）：

```
3 passed, 1 skipped, 1 xfailed
scope=ui_hotels  total=5  counted=4  blocked=1  pass=3  failed=0  known-limit=1  ratio=25.0%
verdict: YELLOW   触发: G5
```

25% 不当失败回退：`blocked` 是事实，G5 只提示；把它当红会让「今天被拦了」变成
「今天不许提交」，那就又回到掩盖拦截的老路。

时间线（同一台机器、同一 IP、约 30 分钟内）：

1. 23:0x 起连续多次打开首页 → **完全正常**，拿到全部定位器，并成功完成一次真实查询
   （广州→深圳，结果页显示「共 1 车次」，抓到了 G6025 广州南→深圳北）。
2. 约十余次访问后 → 整页只剩 `whaleguard block`，`<title>` 为空，即携程 WAF 开始拦截。
   截图：`docs/locator-evidence/blocked-whaleguard.png`。
3. 拦截出现后，所有用例如实转 xfail，没有一条被伪装成通过。

**这就是本工程存在的意义**：拦截是可观测的事实，不是需要掩盖的失败。

---

## 八、已知限制（务必读）

1. **日期控件不稳定。** 2026-10-31 实测：日历 widget 前两次点击能弹出，之后多次尝试
   （点日期文字、点 `.date` 容器、JS focus、JS 派发 click、hover 后点击）都唤不起。
   `select_date()` 不做重试，唤不起就抛 `WidgetUnavailable` → 用例 `skip("known-limit: K-01 ...")`
   并留截图。这条限制**没有绕过方案**，也与风控无关，是控件本身的行为。
   编号化记录见 [`docs/KNOWN-LIMITATIONS.md`](docs/KNOWN-LIMITATIONS.md)。
2. 功能用例需要日期生效，而日期依赖上述 widget，所以拦截出现前也未必稳定通过。
3. `min_results` 只是下界。末班车时段同区间可能只剩 1 条，这是正常业务形态，不是缺陷。
4. 目前覆盖国内火车票查询 + 酒店搜索一期（目的地 / 日期 / 房间及住客 / 搜索四字段）；
   机票未实现；登录态见第 5 条。
5. **登录态分两条轨**（2026-11-01 裁决变更，此前是「一律不碰」）：
   - **匿名轨**（火车票 `suites/thirdparty`、酒店 `suites/hotels`）：仍然不登录、
     不提交订单、不进入支付流程。这两条轨道的全部断言都以「匿名访问」为前提。
   - **`auth` 轨**（`suites/auth`，marker `auth`，scope `ui_auth`）：用一份
     **人工扫码产生、绝不入库**的 storageState 验证「手上这份会话还能不能用」。
     它不碰匿名轨的任何断言，也不进 CI —— 缺 state 时固定
     `skip("known-limit: AUTH-01 / AUTH-03 …")`，不进分母。

   **两站要各采一份**（2026-11-01 实测：登录态不互通）：

   | 入口 | state 文件 | 采集命令 |
   |---|---|---|
   | `www.ctrip.com` | `config/auth/ctrip_state.json` | `python scripts/setup_auth.py` |
   | `trains.ctrip.com` | `config/auth/ctrip_state_trains.json` | `python scripts/setup_auth.py --entry trains` |

   用 www 那份打 trains 站会稳定判「未登录」，看起来像凭据失效，其实只是拿错了文件。

   **退出用例默认不真点**：实测点一次「退出登录」会把服务端 `cticket` 一起注销，
   同一份 state 重放即失效 —— 跑一次就要重扫一次码。所以
   `test_account_menu_exposes_logout_entry` 只验证浮层与链接指向（不点、可反复跑）；
   端到端注销要显式设 `AUTH_REAL_LOGOUT=1`，跑完脚本会把 state 改名成 `*.consumed`
   避免下次莫名报「未登录」。详见 K-08。

   边界、缺口与取证状态见 [`docs/KNOWN-LIMITATIONS.md`](docs/KNOWN-LIMITATIONS.md) 的 K-07。
6. **无头模式下火车票站点整站被拦（HTTP 432 `whaleguard block`）。** 只作用于
   `trains.ctrip.com`，酒店模块在无头下正常。CI 的 trains job 因此用 `xvfb-run -a` +
   `HEADED=1`；本地怎么看窗口见「本地可见调试」一节。编号记录见 K-05。

---

## 九、常见问题

**pip 装不上，清华源返回 403。**
本文档记录：`pypi.tuna.tsinghua.edu.cn` 用 curl 访问是 200，但 pip 报 403。
换阿里云源即可：

```bash
python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt
```

**`Executable doesn't exist at ...chromium-XXXX`。**
playwright 版本与已装内核版本必须一致（1.64.1 要 chromium-1248，旧内核是 1243）。
执行 `python -m playwright install chromium` 补装，**不要**用 `PLAYWRIGHT_BROWSERS_PATH`
指向旧内核——版本错配的坑远比 150MB 贵。
国内下载慢可加镜像：`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`。

**报告里 `XFAIL` 算不算通过？**
不算通过也不算失败，是「预期内的失败」。看 `reason` 前缀：
`blocked:` 是被对方拦下，`known-limit:` 是我们自己承认的限制（这两者都会让 CI 保持绿，
但都不算 `effective_pass`）。两种前缀之外的 xfail/skip 会被门禁判为口径不成立。
