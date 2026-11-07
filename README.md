# ctrip-train-e2e

携程火车票查询的浏览器驱动 E2E，按 Page Object Model 组织，数据驱动，失败留证。

**定位：合规、不绕过任何风控。** 目标站点拒绝自动化访问时，本工程不会伪装身份继续访问，
而是把 `blocked` 当作一等结果记录进报告。这不是妥协，是设计前提。

---

## 〇、技术栈与版本要求

- **语言**：Python —— CI 实测 **3.12**（依赖本身要求 3.9+，但更老版本未全量验证）。
- **测试框架**：pytest **9.2.0**。
- **浏览器驱动**：Playwright **1.64.1** + pytest-playwright **0.10.0**（需另装浏览器二进制：`python -m playwright install chromium`）。
- **测试报告**：pytest-html **4.2.0**（内置）；Allure **2.x**（可选，生成需 JRE 17+ 与 Allure CLI，见「十一、Allure 报告」）。
- **其他运行时依赖**：PyYAML **6.0.3**、pytest-timeout **2.4.0**、allure-pytest **2.16.2**。
- **开发 / 质量工具**（仅 `requirements-dev.txt`，非跑用例必需）：pytest-xdist、pytest-randomly、ruff、mypy、Faker、pillow（Windows 探针另需 `uiautomation`，已用 `win32` 标记隔离，不参与测试运行）。

> 以上版本号均为仓库内 `requirements.txt` / `requirements-dev.txt` 中 `==` 锁定的值。
> 克隆后直接按锁文件安装即可，**无需手动对齐版本**；CI 也以同一锁文件安装
> （`.github/workflows/ci.yml`，`runs-on: ubuntu-latest`，仅装 `requirements.txt`）。

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

### 首次克隆后完整启动步骤

```bash
# 1) 克隆
git clone https://github.com/testhy22/train-e2e.git
cd train-e2e

# 2) 安装依赖（按锁文件：pytest / playwright / allure 插件等）
python -m pip install -r requirements.txt

# 3) 安装浏览器二进制（版本须与 playwright 一致，见「常见问题」）
python -m playwright install chromium

# 4)（可选）环境变量：所有变量都有代码内默认值，不配也能跑；
#    仅在需要改目标站 / 超时 / 本地有头调试时才动。
cp .env.example .env

# 5) 跑一次全量（含 27 条数据层单测）
python -m pytest
```

> 环境变量清单与含义见 `.env.example`（已随仓库提交，`.env` 本身被忽略，不会入库）。
> 依赖版本以仓库内 `requirements.txt` / `requirements-dev.txt` 的 `==` 锁定值为准，无需手动对齐。

### 常用安装与运行命令

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

**运行口径（WAF，K-05）**：`trains.ctrip.com` 与 `flights.ctrip.com` 两个子域在**无头**下会被
whaleguard 拦截（HTTP 432 → 记 `blocked → xfail`，是已知外部限制不是失败）。要真实验证这两条
轨道请 `HEADED=1`（本地 `scripts/run-headed.sh`，CI `xvfb-run`）；酒店轨（`www.ctrip.com`）无头
不受影响。2026-11-02 复验：同登录态下无头 8 xfailed / `HEADED=1 HUMAN=0` 8 passed。

`-m ui` / `-m hotels` 不是装饰：不带它跑出来的 junit 里混着 27 条数据层单测，
门禁会直接判「口径混算」并拒绝出数（见 `docs/BLOCKED-GATE.md` §1）。

`--scope` 也不是装饰：**G3/G4 只在同一 scope 内累计**。两个站点的拦截率不可比，
混算时酒店那条 25% 会把火车票的 1.0 序列打断，G3 永不触发。`--scope` 与 junit 口径
不一致会 exit 2，不会「忘了改一处还照常出数」。

### 本地可见调试（有头，仅本地）

```bash
scripts/run-headed.sh suites/hotels -m hotels
SLOWMO=800 scripts/run-headed.sh suites/thirdparty -m ui     # 想把每步看得更慢才加（火车票已接拟人）
```

脚本固定 `HEADED=1 HUMAN=1`。四个开关（`HEADED` / `HUMAN` / `HUMAN_SEED` / `SLOWMO`）、
Linux 无显示服务时的行为、与 CI 的差异表，全部在
[`docs/HEADED-DEBUG.md`](docs/HEADED-DEBUG.md)。

两条要点：

- `HEADED=1` 会自动开拟人，**默认不要再叠加 `--slowmo`**，看不清再加 `SLOWMO=300/800`。
- 拟人只调时间不碰身份，被拦照旧记 `blocked` → `xfail`，**不因为被拦就调慢重试**。

拟人化的定位（2026-11-02 定案）：**预防性措施，不是解封手段** —— 解封关键已实证是
`HEADED=1`（K-05）；拟人化只降低单次被行为模型命中的概率，**不提高吞吐、不改变
「失败即停/熔断」与 blocked→xfail 语义**。

开关三态：`HUMAN=1` 强制开 / `HUMAN=0` 强制关 / **未设 ⇒ 跟随 HEADED**（有头开、
无头关）。CI 恒 `HUMAN=0`，不受影响；长期低频跑显式 `HUMAN=1`。

耗时口径（2026-11-02 实测，`-m e2e` 整轨 13 条有头）：
`HUMAN=0` ≈ 212s；`HUMAN=1` ≈ 296s（**软上限 ≈5 分钟，超 6 分钟须重新归因**）。
其中本次新增代码贡献约 +18s（火车 +5–6s/条、登录自检 +0.5s/条，**硬约束**），
其余约 +65s 是酒店/机票既有拟人调用随开关激活的存量成本。预算分层，不再用单一
整轨预算衡量。覆盖：酒店/机票/火车三页 POM 均已接入（火车票 2026-11-02 补齐）；
轮询类 wait（语义是等条件）刻意不拟人（humanize 边界 3）。

### 预期成功的控制台输出

全量 `python -m pytest` 在「目标站可正常打开」情景下的典型末尾（数字随用例数变化）：

```
=================== 126 passed in 325.03s ===================   # 实测样例
```

判读要点：

- `passed` 表示用例按预期执行并完成断言；数据层单测（`tools/tests`、`suites/...` 中不带 `ui` 标记的部分）也包含在内。
- 若目标站触发风控（滑块 / 验证码 / 限流文案 / WAF 文本页），对应用例会记为 `XFAIL`
  —— 这不是失败，是「被拦」这一**一等结果**被正确记录，不影响「跑通」判定。
- 退出码：`0` = 绿/黄（含 `XFAIL` 都属正常结果），`1` = 红，`2` = 口径不成立（见门禁规则）。
  注意：环境钩子拦截临时目录清理时也可能返回非零，需结合「有无失败用例」而非只看退出码。

> 想要趋势化报告，见「十一、Allure 报告」。

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
4. 目前覆盖国内火车票查询 + 酒店搜索一期（目的地 / 日期 / 房间及住客 / 搜索四字段）
   + **国内机票单程查询**（2026-11-01 实现，`suites/e2e` 串联轨覆盖，见第十节）；
   登录态见第 5 条。
5. **登录态分两条轨**（2026-11-01 裁决变更，此前是「一律不碰」）：
   - **匿名轨**（火车票 `suites/thirdparty`、酒店 `suites/hotels`）：仍然不登录、
     不提交订单、不进入支付流程。这两条轨道的全部断言都以「匿名访问」为前提。
   - **`auth` 轨**（`suites/auth`，marker `auth`，scope `ui_auth`）：用一份
     **人工扫码产生、绝不入库**的 storageState 验证「手上这份会话还能不能用」。
     它不碰匿名轨的任何断言，也不进 CI —— 缺 state 时固定
     `skip("known-limit: AUTH-01 / AUTH-03 …")`，不进分母。
   - **`auth` 轨里的酒店搜索**（2026-11-01 新增，2 条 × 3 组数据，数据与匿名轨共用
     `data/hotels/search_cases.yaml`）：登录态下走「首页填表 → 提交 → 落
     `hotels.ctrip.com/hotels/list` → 卡片 ≥1」，以及「结果页点左侧竖排导航的
     **酒店**回首页 → 改日期与晚数 → 再搜」。后者是用户实际操作路径（结果页顶部
     那个搜索框回填上次条件，改城市要先清空）。匿名轨的
     `test_submit_then_list_renders` **仍然保持 skip** —— 它要回答的是「匿名能拿到
     什么」，给它挂 state 会把那条序列变成「今天扫没扫码」，两者不互相替代。

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

---

## 十、日志与诊断（排障入口）

一条失败用例要能回答四个问题：**在哪挂的、页面长什么样、前端报了什么、请求被谁挡了**。
四件事分别由四个组件承担，全部默认开启：

| 组件 | 位置 | 产出 |
|---|---|---|
| 统一日志 | `utils/logger.py` | 控制台 INFO + `reports/logs/run_<时间戳>.log`（DEBUG，含 `模块:行号`），10MB × 5 份轮转 |
| 页面诊断 | `utils/diagnostics.py` | 每个 page 的 JS 报错 / HTTP ≥400 / 请求失败，失败时写进报告 |
| 失败取证 | `conftest.py` 的 `pytest_runtest_makereport` | `reports/screenshots/<用例>__viewport.png` + `__full.png`，附 URL / title |
| Trace | `pytest.ini` 的 `--tracing=retain-on-failure` | `reports/trace/<用例>/trace.zip`（函数级 context）；e2e 共享 context 需 `E2E_TRACE=1` |

要点：

- **日志一律过 `redact()`**。`cticket`、`Cookie`、`token`、手机号在写盘前就打码 ——
  日志会被贴进 issue 和提交记录，凭据进去就是事故。
- **诊断只观察、不拦截**。挂的是 `page.on("console"/"pageerror"/"response"/"requestfailed")`，
  没有 `route`。会改请求的「诊断」等于改被测对象。
- **URL 记录一律去掉 query**（`host/path`）。query 里常带凭据，而定位「哪个接口挂了」不需要它。
- **未捕获异常走 `pageerror`，不走 `console`**（2026-11-01 实测）。只挂 console 会漏掉
  「组件初始化就抛异常」这类最要命的失败。三条通道的可用性由
  `tools/probes/probe_diagnostics.py` 端到端验过（console error / pageerror / 4xx / requestfailed 全命中）。
- **数据层单测不产生页面诊断段**：没有页面的失败只记一行 debug，不往报告里塞噪音。

旧产物清理（`utils/cleaner.py`，`pytest_sessionstart` 调用）：

```bash
python -m pytest suites/e2e            # 默认清理 reports/ 下 24 小时前的文件
E2E_CLEAN_HOURS=72 python -m pytest    # 改阈值
E2E_CLEAN_DRY_RUN=1 python -m pytest   # 只统计不删
E2E_CLEAN_DISABLE=1 python -m pytest   # 完全跳过（要留证据排查时）
```

安全边界：只清 `reports/` 且必须解析在项目根内（路径写错就拒绝执行，不删任何东西）；
`PermissionError`（Windows 下被 Trace Viewer 占用）跳过并计数；
`logs/`、`screenshots/`、`trace/`、`html/` 这四个一级目录即使清空也不删。
行为由 `tools/tests/test_cleaner.py` 锁死（12 条：过期判定 / 演练 / 禁用 / 项目外拒绝 / 脱敏）。

访问强度（`utils/throttle.py`，三个维度，全部可关）：

```bash
# 一次只碰一个功能（e2e 串联轨道，收集期裁剪；auto = 每次随机挑一个）
E2E_MODULES=trains python -m pytest suites/e2e
E2E_MODULES=auto  HEADED=1 python -m pytest suites/e2e/tests/test_search_chain.py

# 动作间隔 15s / 用例间隔 30s / 每小时最多 5 次搜索（跨运行，落盘 .e2e-state/quota.json）
E2E_MIN_ACTION_INTERVAL_S=15 E2E_CASE_GAP_S=30 E2E_HOURLY_QUOTA=5 python -m pytest suites/e2e
E2E_QUOTA_WAIT=1 python -m pytest suites/e2e   # 超额时真等到窗口滑出（默认是 skip）
E2E_THROTTLE=0 python -m pytest                 # 整体关闭（只建议在跳板机上调功能时用）
```

超额默认 `pytest.skip("quota: …")` 而不是硬等：等一小时只会被 job 级超时砍掉，还占着 runner。
配额是「跨运行」的，因为动作间隔管不住「CI 每 10 分钟跑一轮」这种聚集。

**红线**：`tools/tests/test_no_stealth.py` 会扫全仓 —— `add_init_script`、`user_agent=`、
改写 `navigator.webdriver`、打码平台、`AutomationControlled` 启动参数一律判红。
只读取证（`page.evaluate("() => navigator.webdriver")`）不算违规。

## 十一、Allure 报告（项目级看板）

pytest-html 只够单次结果展示：扁平列表、没有步骤、没有历史。Allure 补的正是这三块。

```bash
# 跑一次（pytest.ini 的 addopts 已带 --alluredir，不需要额外参数）
python -m pytest tools/tests suites/e2e -p no:randomly

# 生成报告（会自动续上一份的 history，再出两份产物）
python scripts/allure_report.py            # 多文件 → reports/allure-report/
python scripts/allure_report.py --open     # 顺手打开单文件那份
python scripts/allure_report.py --serve    # 起本地服务看（趋势可交互）
```

**两份产物，各有用途，别只留一份**：

| 产物 | 怎么看 | 为什么需要 |
|---|---|---|
| `reports/allure-report/index.html` | **必须走 HTTP**（`--serve` 或任意静态服务器） | `history/` 落在这里，趋势图靠它续接 |
| `reports/allure-report-single/index.html` | 双击即可（3.9 MB） | 多文件版用 `file://` 打开会显示 `500 Failed to fetch`（见 K-10 坑②） |

报告里能看到什么：

- **Behaviors**：Epic（携程网页自动化测试）→ Feature（酒店搜索 / 跨模块串联 / 数据层自检）→ Story → 用例；
  标签由 `conftest._allure_labels` 按 scope 自动打，新用例自动继承，不需要逐个加装饰器。
- **Steps**：三个搜索流程各自拆成 4~5 步（打开首页 → 填目的地 → 选日期 → 填住客 → 提交），
  失败时红在具体那一步上，不用再翻日志找「卡在哪」。
- **失败证据**：视口截图（内嵌）、页面诊断文本（JS 报错 / 4xx / 请求失败）、Playwright trace（`E2E_TRACE=1` 时）。
- **Environment**：base_url ×3、headless、Python / Playwright 版本、是否续上历史 —— 每份报告自证环境。
- **Categories**：失败自动归到「对方拦截 / 脚本问题 / 断言不符 / 环境或数据问题」四类。

依赖与工具（**只认环境变量，仓库内不写任何本机路径**）：

```
allure-pytest==2.16.2            # 主依赖，必须 ≥2.16（2.9.x 与 pytest 9 不兼容，K-10 坑①）
JAVA_HOME   = $JAVA_HOME         # JRE 8+ 根目录（该目录下应有 bin/java）
ALLURE_BIN  = $ALLURE_BIN        # allure 可执行文件；不设则回落到 PATH 上的 allure
```

**请确保本机已配置这两个环境变量**，否则 `scripts/allure_report.py` 会直接报错退出
（报错里给出的是「该配什么」，不是某个人的磁盘路径）：

```bash
# Windows：用户级、永久生效。已在运行的终端读不到新值，必须重开。
setx JAVA_HOME  "<你的 JRE 目录>"
setx ALLURE_BIN "<你的 allure 可执行文件>"

# macOS / Linux：写进 ~/.bashrc 或 ~/.zshrc
export JAVA_HOME="/path/to/jre"
export ALLURE_BIN="/path/to/allure/bin/allure"
```

获取方式（两者都不必翻墙，见 `AGENTS.md` 第 9~11 条）：

- **JRE 8+** → Adoptium 镜像 `mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jre/x64/windows/`；
- **Allure CLI** → Maven 中央仓库
  `repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/<版本>/`（提供 `.sha256`）。

下载这类二进制资产请走 `tools/smart_download.py`（多源测速 + 按体积自适应超时 + 哈希校验），
别用单一公共代理硬等 —— `gh-proxy.com` 实测会「HTTP 200 但只给 1.58 MB」。

## 十二、登录态自检（e2e 轨的前置闸门）

`suites/e2e/tests/test_merged_login_state.py`，1 条，`-m e2e` 轨现在共 **13 条**。

它解决的是一个真实的信息缺口：那 12 条搜索用例断言的是「填表 → 提交 → 出结果」，
**匿名态同样能过**（2026-11-01 之前匿名跑就是绿的）。所以「12 passed」只证明
「有 12 次搜索拿到了结果」，不证明「登录态下也能搜」——两份报告长得一模一样。

这条用例把「当时到底登录没登录」从假设变成报告里的一行字：

```
【三站判定】
✅ 酒店 / 主站（www）      判据证据：…永永99999…
✅ 机票（flights）
✅ 火车票（trains）
【登录态文件】主站：ctrip_state.json｜cookies=23…｜采集于 2.1 小时前
【共享会话】重建 page 次数：0
```

设计取舍：

- **判失败，不判 xfail**。能跑到这里说明 state 文件在；此时判「未登录」只有两种可能：
  cookie 过期（要重新扫码）或合并逻辑坏了（代码缺陷）。转 xfail 就是一片黄色，
  而「黄色没人看」是本项目反复踩过的坑。
- **不抄域名**，`_site_url()` 走 `settings.hotel_base_url() / flight_base_url() / base_url()`。
  另抄一份的后果是自检与用例各指一个站，自检说「已登录」而用例打的是别处 —— 比没有自检更糟。
- **先判 WAF 再判登录**。被拦时拿不到 header 也拿不到用户名，任何登录判定都不成立；
  把「被拦」误读成「掉登录」会导致完全错误的处置。
- 文件名排在 `test_search_chain.py` 之前，pytest 按文件名顺序执行 ⇒ 登录态不成立时
  第一眼看到的就是它，不用等 300 秒后从某条搜索的红里猜原因。
