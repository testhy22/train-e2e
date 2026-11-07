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

## K-01 日历控件唤不起 —— **已修复（2026-11-01）**

| 项 | 内容 |
|---|---|
| 现象 | 日期输入框点击后，`.widget-calendar-depart-wrap` 面板不再出现；面板出现时带 `.widget-calendar-mask` 遮罩，会拦住搜索按钮点击 |
| 取证 | 2026-10-31，`tools/probes/probe8.py` / `probe_interact.py`，截图见 `docs/locator-evidence/cal-attempt1.png`、`cal-open.png` |
| 已试无效 | 点日期文字、点 `.date` 容器、JS `focus()`、JS 派发 `click`、`hover` 后点击 |
| **根因（2026-11-01 查实）** | 不是「控件唤不起」，是三个**我们自己的**定位器/时序缺陷，各有实测证据：<br>① 日期块点的是 `#label-departDate`，该 `<input>` 带 `assist-ib-dom`，Playwright 判为不可见（`force=True` 也点不动）⇒ 实际点的是 `get_by_text(depart_date)` 兜底，而日历没打开时页面上**不存在**这个日期文本，于是 15s 超时；<br>② 城市面板选完后 `div.widget-city-mask` 没关（1440×900 全覆盖），后续所有点击被 `intercepts pointer events` 拦掉；<br>③ 城市项按 `get_by_text(city, exact=True)` 全局匹配，会命中页面底部「热门路线」的同名城市，或命中 `li`「北京北」内层的 `span`（文本恰为「北京」）⇒ 表单没真正选中却让 `input_value` 看起来正常，这就是旧记录里「回填成北京北」的来历 |
| 修法 | `pages/train_search_page.py`：日期改点 `div.search-date div.date`；城市面板限定在 `div.<field>-station-picker-wrap ul.widget-search-bd` 内、按 `div.cont` **完整文本**匹配 `li`；选完点遮罩关闭；`PlaywrightTimeout` 统一转 `WidgetUnavailable`。日历就绪锚点用 `ul.widget-calendar-hd`（容器 `.widget-calendar-depart-wrap` 的边界框宽 0，拿它判可见永远等不到） |
| 验证 | 2026-11-01 本机：有头 `HEADED=1 HUMAN=0` → **5 passed**（含解封后的 `test_calendar_widget_opens`）；无头 → 5 xfailed 于 `waf_block`（K-05 已知，无头下整站被拦，未走到控件）。只读探测：`reports/probe_dom{1,2,3,4,5}_2026-11-01.py` |
| 销号条件 | **已满足，正式销号（2026-11-01）**：本机 `test_train_search` 连续 3 次通过（单跑 hz-nj 1 次 + 整轮 2 次全绿）；CI run [41288945562](https://github.com/testhy22/train-e2e/actions/runs/41288945562) `ui` 5 passed、门禁 ratio 0.0% |
| 备选方案 | 走 URL 预置 `dDate=YYYY-MM-DD` 直连结果页。**未启用**（已不需要）：⚠️[未查实] 现有证据只是「UI 操作后 URL 出现 dDate」，不是「手工构造 URL 直达生效」 |

## K-02 预售期上限 15 天（含当日）

| 项 | 内容 |
|---|---|
| 现象 | 用例数据只能取 `T+3 … T+15`，越界由 `utils/data_loader.py` 直接 `ValueError` |
| 依据 | 12306 官方口径 15 天（2026-10 核实） |
| 为什么不是缺陷 | 这是业务约束，不是系统限制 |
| 销号条件 | 官方口径变化时改 `config/settings.py` 的 `MAX_PRESALE_DAYS`，并同步本条 |

## K-04 匿名态酒店列表被登录墙接管（结果渲染不可验证）

| 项 | 内容 |
|---|---|
| 现象 | 首页填完搜索条件点「搜索」，浏览器落到 `passport.ctrip.com/user/login?backurl=…`；**直接打开** `hotels.ctrip.com/hotels/list?...` 同样被接管 |
| 取证 | 2026-11-01，`reports/probe_www_hotels_03.json`（提交后落点）、`reports/probe_www_hotels_04.json`（直开 list URL 亦被拦） |
| 为什么不绕过 | 登录 / 复用 cookie 属于拿用户凭据绕开频率与身份限制，与 D 决策（零反检测、不复用 cookie）冲突，且 CI 无法安全持有凭据 |
| 影响 | 一期**不断言结果列表渲染**，改断言「提交时真实发出的 `/hotels/list` 参数」（日期/晚数/房间人数/城市），这是页面自己生成的证据；`test_result_list_renders` 标 `skip("known-limit: K-04 …")`，不进分母 |
| 保留的契约测试 | `test_list_page_renders_with_full_params`（`suites/hotels/tests/test_hotel_search.py`）。断言「full 参数 + 匿名直开 list 能出卡片」。**本条原先写的是 `test_login_wall_is_global`（断言直开必被拦），实测一跑就红**——按 `reports/probe_www_hotels_06.json` 的事实替换：登录墙挂在「参数完整性 + 会话状态」，不是「列表页不可访问」。2026-11-01 核对时本行仍引用旧名，已按代码实际内容更新 |
| 登录态对照（2026-11-01 17:56 实测） | 带上 `config/auth/ctrip_state.json` 走**同一条首页提交路径**：落 `hotels.ctrip.com/hotels/list?...`、无 passport、`[class*='hotel-card']` 13 张、页面文案「找到9382家酒店」。取证 `tools/probes/probe_hotel_auth_search.py` → `reports/probe_hotel_auth/20261101-175636/`（`reports/` 未跟踪）。**这条不销本条的号**：K-04 说的是「匿名态」，匿名态今天仍然落墙，实验条件没变。它证明的是「登录态下这条路能走通」，用例落在独立轨道 `suites/auth/tests/test_hotel_login_search.py`（scope `ui_auth`），不影响 `ui_hotels` 的 ratio 序列 |
| 重新搜索路径（2026-11-01 18:00 实测） | 在结果页想换条件重搜，**不是**改顶部那个搜索框（它回填上次条件，改城市要先清空），而是点左侧竖排导航「酒店」：`div[class*='lsn_first_nav_wrap']`（46×42 at(15,68)，text=「酒店」）→ 同页跳转回 `https://www.ctrip.com/`（不开新标签）→ `#hotels-destination` 表单回到 1 个，可重填重搜。取证 `tools/probes/probe_hotel_re_search.py` → `reports/probe_hotel_research/20261101-180004/`。**注意别用 `a:has-text('酒店')`**：第一版探针这么写，命中的是正文里一个 56×16 的文字链接（bbox y=3568），落到 `hotels/homepage`（海外酒店首页，表单不在） |
| 销号条件 | 匿名态（不带 state）从首页提交不再落 passport，且能稳定取到卡片 ≥1 条，在 CI 里连续 3 次观察到。**原条件写的是「`test_login_wall_is_global` 连续 3 天失败」，而该用例早已被 `test_list_page_renders_with_full_params` 替换，条件本身不可判定** —— 2026-11-01 按现存用例重写，不保留一个指不到任何代码的销号条件 |
| 二期方向 | 酒店级别、关键词字段；儿童年龄控件（有合法登录态的独立轨道已于 2026-11-01 建成，见 K-07） |
| 相关现场 | 2026-11-01 有头运行曾停在本登录墙并超时，见 K-06。**该画面不作为超时归因** |

## K-03 blocked 不留 trace

| 项 | 内容 |
|---|---|
| 现象 | `blocked` 走 xfail，不属于 failure，`--tracing=retain-on-failure` 不会产出 `trace.zip` |
| 现在留什么 | 视口截图 + 全页截图 + URL + title，写进报告 section |
| 为什么不改 | 改成 `--tracing=on` 会让每次运行都录完整 trace（体积与耗时翻倍）；把 blocked 当失败又违背「blocked 是一等结果」 |
| 销号条件 | 出现「只有 trace 才能定位」的拦截问题，且复现成本高于全量录制 |

## K-05 trains 在无头模式下整站被 WAF 拦（432）

| 项 | 内容 |
|---|---|
| 现象 | `trains.ctrip.com` 用 headless 打开时返回 **HTTP 432**，正文只有 17 字符 `whaleguard block`，标题为空；同一分钟改成有头则是 **200**、正文 953 字符、标题正常 |
| 取证 | 2026-11-01 01:38 与 14:2x 两次独立对照。第二次实测：`headless=False status=200 正文=953字符 耗时=1.0s` / `headless=True status=432 正文=17字符 耗时=2.7s`。累计 headless 7/7 被拦、headful 5/5 正常 |
| 复验 | 2026-11-02 00:07，e2e 轨单变量对照（同登录态，仅切 `HEADED`）：`-k "trains or flights"`（3 火车 + 5 机票）headless → **8 xfailed**（`waf_block` whaleguard，38s）；`HEADED=1 HUMAN=0` → **8 passed / 0 xfail**（194s，log `reports/logs/e2e_chain_headed_tf.log`）。**范围修正**：`flights.ctrip.com` 无头同样被拦、有头正常 —— 本条范围应读作「trains **与 flights** 子域」，此前「只作用于 trains」的说法不完整 |
| **复验（往返轨道，2026-11-02 22:36）** | 单变量对照，同一提交、同一登录态，只切 `HEADED`：**`HEADED=0` → 9 xfailed / 0 passed，12.23s**，9 条逐条 `-rx` 原因均为 `blocked: waf_block（whaleguard）`；**`HEADED=1 HUMAN=0` → 9 passed，107.84s**。12.23s / 9 条 ≈ 1.4s 一条，全部在**打开页面那一刻**就被判拦，**没有任何一条走到 DOM 断言**（真走到的话单条最少 8~10s，光 `leg_texts` 的两个 `expect` 等待窗就不止这个数）。⇒ 排除「视口太小导致元素没渲染」这种解释：DOM 根本没拿到，不是拿到了看不见 |
| **复验（三组无头二进制对照，2026-11-07 11:49）** | 目的：把 `HEADED` 一键改动的两件事拆开 —— ①「有没有真实显示设备」②「用哪个二进制」（无头走 `chromium_headless_shell`，有头走完整 `chromium`）。旧结论从未分离过这两者。探针 `tools/probes/probe_headless_binary.py`（只读，各组只发 1 次 GET，同 args / locale / 时区，**只换 channel**）：<br>**A 默认（headless shell）= 432、body 168、whaleguard=True**；<br>**B `channel="chromium"`（完整 chromium + 新版无头）= 432、168、True**；<br>**C `channel="chrome"`（系统真实 Chrome，`%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`）= 432、168、True**。<br>三组 UA 全部含 **`HeadlessChrome/156`**（C 组亦然：`--headless=new` 下 Chrome 自己把 UA 改成 HeadlessChrome）。取证 `reports/probe/headless_binary_20261107-114940.json`。⇒ **「换二进制 / 换 channel」不能解 432**；且 432 是**文档请求本身**的状态码（不是 JS 起来之后才拦），判定发生在 HTTP 层，与渲染路径无关 |
| 为什么不改 UA / 视口 / stealth 去绕 | ① **不是「二进制 / 渲染路径」问题**（2026-11-07 三组对照，见上一行）；② 但**不能**沿用旧版那句「而不是 UA 字符串」—— 有头/无头切换同时改了 `navigator.userAgent`（无头恒含 `HeadlessChrome`），旧表述把两个**未分离**的变量当成已排除，2026-11-07 更正；在「不伪装身份」红线下无法再分离（要分离就得改 UA），故「UA 是否判据」标 **⚠️[未查实]**；③ 项目硬约定不伪装身份（`TODO.md` 附录第 2 条）；④ CI 已有正解 —— `.github/workflows/ci.yml:75-101` 装 `xvfb` 并提供 `HEADED=1 HUMAN=0`，用**虚拟屏**让 Chromium 以有头模式跑，**这条轨道在 CI 里不是无头的**，所以「无头跑不了 ⇒ CI 里没价值」这个推论在本工程不成立 |
| 范围（重要） | **只作用于 trains 子域，不是整站。** `www.ctrip.com` 的酒店模块在 headless 下正常出结果：2026-11-01 三份 junit 逐条一致（`00:43` headless、`01:21` headless、`01:43` headed 均 3 passed）。曾把这条写成「无头模式整站被拦」，是错的，已更正 |
| 为什么不是缺陷 | 站点对无头浏览器的识别，与本工程代码无关。**不通过改 UA / 注入 stealth 脚本 / 随机化启动参数去绕** —— 那是把自己变成反检测工具 |
| 影响 | ① CI 的 trains job 改 `xvfb-run -a` + `HEADED=1`（虚拟屏让 Chromium 能以有头模式启动，不改身份、不改断言）；② hotels job **维持 headless**，不为此付有头的成本；③ 本地调试用 `scripts/run-headed.sh` |
| 不做什么 | 不把 `config/settings.py` 的默认 `HEADLESS` 改掉 —— 默认无头是 CI 的正确行为，只为「让窗口可见」改默认值会把本地需求带进流水线 |
| 销号条件 | trains 在 headless 下连续 3 次返回 200 且正文 ≥500 字符；届时把 CI 的 `xvfb-run` 撤掉，改回 `python -m pytest` |

## K-06 有头跑酒店曾停在 passport 登录墙并超时（未稳定复现）

| 项 | 内容 |
|---|---|
| 现象 | 2026-11-01 约 14:57，`scripts/run-headed.sh suites/hotels -m hotels`（`HEADED=1 HUMAN=1`）跑到约 **180s** 被 pytest-timeout 终止，**junit 未生成**；当时窗口停在 `passport.ctrip.com/user/login?backurl=…`，backurl 里可见 `city=32`（对应广州那条用例） |
| 取证 | 当场截图（窗口 = passport 登录页）；同参数随后两次完整跑绿：**95.2s / 106s**（最慢单条 38.64s），junit 正常生成、3 passed / 1 skipped / 1 xfailed |
| 复查结论 | 同一提交、同一组参数、同一台机器，随后 2 次全绿 ⇒ **非稳定复现**（1 红 / 2 绿） |
| 根因 | ⚠️**[未查实]**。请勿把「停在登录墙」写成超时原因：那只是窗口当时的可见状态，不是因果证据。反证是 K-04 已说明匿名态酒店列表本来就会被 passport 接管，而 headless 下三次运行同样落在登录墙仍全绿 ⇒「落在登录墙」本身不足以解释超时 |
| 复现时才做的对照 | ① `--durations=10` 定位慢在哪一步；② 去掉 `permissions: []` 再跑一次（排除 geolocation 预拒绝相关）。命令见 PR #1 §6。**未拿到这两组对照前不下结论、不写「已修复」** |
| 为什么暂不归档为「对方页面行为」 | 没定位就不给归因。若最终证实是站点侧偶发（登录墙重定向叠加慢加载），再按对方页面行为归档 |
| 销号条件 | 复现 ≥1 次且拿到上面两组对照、定位到具体环节并修掉；或连续 20 次跑绿证明不可复现，改为「已观察、暂不可复现」并保留记录（不删本条） |

## K-07 登录态轨道（`auth`）—— 会话复用可用，但「登录功能本身」未验证

| 项 | 内容 |
|---|---|
| 起因 | 2026-11-01 裁决变更：「立即推进微信快捷登录」的自动化。README 此前写的是「登录态一律不碰」，本条登记这次变更的边界，不是把旧结论悄悄改掉 |
| 已实现 | `scripts/setup_auth.py`（人工扫码一次，存 `config/auth/ctrip_state.json`）+ `suites/auth`（3 条用例，marker `auth`、scope `ui_auth`）+ `utils/auth.py`（采集与验证共用同一份判据，避免两处漂移） |
| 能证明什么 | 「这份会话还能用」：加载 state 后访问 www.ctrip.com，顶部「注册」消失、头像区可见。这是**唯一**有画面证据的判据 |
| **不能**证明什么 | ① 输账号密码 / 图形验证码那条路径一次都没跑过（只走了微信快捷登录）；② 手机扫码确认路径在取证后没有再做端到端验证（本轮走的是 PC 快捷登录，确认动作在微信客户端） |
| 已取证（2026-11-01 补，原先的 `⚠️[未查实]` 全部清掉） | 登录入口：`a.icon-new-wechat`（文本「微信」）。**协议勾选是前置闸门**：不勾 `input#checkboxAgreementInput` 就点微信，只弹 `div[data-testid="agreementTip"]`「请先阅读并勾选协议」，URL 停在 passport，看起来像「点了没反应」。授权页 `open.weixin.qq.com/connect/qrconnect`，两种形态：快捷登录面板 `div.js_quick_login`（178×179，含头像昵称 + `button.js_quick_login_btn`），或二维码 `img.js_qrcode_img`（160×160）。**该页没有「允许」按钮** —— 确认框在微信客户端的 Qt 窗口里（`EnumChildWindows` 返回 0，`uiautomation` 6 轮 `GetChildren()` 也是 0，只能按品牌绿 `#07C160` 像素定位后点击，实测按钮簇 189×37） |
| 已实测：必须用系统 Chrome | 同一台机器、同一 URL、同一套代码，只换浏览器二进制：系统 Chrome `156.0.8078.79` 快捷登录 0.1s 出现；Playwright Chromium `156.0.8078.4` 永远 `display:none`。差异在网络层（微信页面 POST 六个回环端口 `/api/check-login`，Playwright 里 `14013` 报 `ERR_CONNECTION_CLOSED`，而 `curl -k` 直连返回 200），**不是指纹** —— 所以没有引入任何 stealth / UA 伪装 |
| 已实测：两站不互通 | www 登录后用同一份 state 打开 `trains.ctrip.com` 仍判未登录 ⇒ 各采一份（`--entry trains` → `ctrip_state_trains.json`）。误用对方那份不会报错，只会稳定判「未登录」，详见 README 第 5 条 |
| 已实测：跨站合并可三站同登录（2026-11-01 19:5x 补） | 用户要求「只登录一次跑完酒店/机票/火车票、每个功能不重启浏览器」，`shared_context` 因此改用 `utils.auth.merged_storage_state()`：www 打底 + 补 trains 那份里的 `trains.ctrip.com` 专属 cookie。探针 `tools/probes/probe_merged_auth.py` 一次启动浏览器对照三种策略，**A/B/C 全部 3/3**（www / flights / trains 顶部均显示用户名 `永永99999…`），实现选 A（语义最稳：主会话不被静默替换）。**同时修正上条的方向性**：`trains` 那份里的 20 个 `.ctrip.com` cookie 也能让 www / flights 判登录（同一账号），所以「不互通」只对「www 那份 → trains 站」这个单方向成立 |
| 合并后的验证 | `HEADED=1 pytest suites/e2e -m e2e` → **12 passed / 325.14s**；日志 `reports/logs/run_20261101_200211.log` 只有 **1 条**「共享会话登录态」，全程无 page 重建（`SharedSession.rebuilds` 未触发）。另用生产函数单独验三站：www / flights / trains 均 `登录=True` |
| 与匿名轨的隔离 | 三条硬隔离：scope 独立（`ui_auth`，不并入 ui / ui_hotels 的 ratio 序列）；context 独立（匿名用例仍匿名，K-04 的「提交路径落 passport」实验条件不被改）；缺文件时 `skip("known-limit: AUTH-01 …")` 而非 fail（不进分母，不让 CI 永久红） |
| 凭据边界 | state 是**明文会话 cookie**，等价账号凭据。`config/auth/` 已进 `.gitignore`；不进 CI、不进 secret；不因被拦而换账号重试。谁要把 state 放进 CI secret，等于同时推翻 K-04「CI 无法安全持有凭据」的判断，必须重新裁决 |
| 已实测的坑① | 首页底部「周周享低价 / 领券更划算」浮层带一张**营销二维码**（2026-11-01 截图证据），会让「二维码可见」类断言假绿。`utils/overlay.py` 已在 `BasePage.goto` 后自动关它，`OVERLAY_AUTOCLOSE=0` 可关 |
| 已实测的坑② | 按窗口标题「微信」判断 PC 微信会**误命中**：同一次 `EnumWindows` 里 Chrome 窗口标题也含「微信」。`utils/wechat.py` 改用类名 `Qt51514QWindowIcon` 作主判据，标题只做弱兜底 |
| 已实测的坑③（2026-11-01 18:0x 补） | 结果页顶部的搜索框**不是**「重新搜索」的正路：它回填上一次条件，改城市要先清空。用户实际路径是点左侧竖排导航「酒店」回 `www.ctrip.com` 首页，那里才有完整五段表单。本项已落成用例 `test_re_search_from_result_page_via_left_nav`（`suites/auth/tests/test_hotel_login_search.py`），实测 2 passed / 29.7s |
| 已补上的验证（2026-11-01 替换原先的「未完成」行） | 原行写的是「`test_submit_then_list_renders` 仍是 skip，带 state 跑酒店提交路径是否还落 passport 这次没验证，别默认它能绿」。**已实测：不落 passport，出 13 张卡片**（`reports/probe_hotel_auth/20261101-175636/`）。但它**仍然不打开** `suites/hotels` 那条 skip —— 那条跑在匿名轨道（scope `ui_hotels`），给它挂 state 等于把「匿名能拿到什么」的序列换成「今天扫没扫码」。登录态的酒店搜索另立用例，见下一行 |
| 新增用例（2026-11-01） | `suites/auth/tests/test_hotel_login_search.py`，2 条 × 3 组数据（共用 `data/hotels/search_cases.yaml`，不另抄一套数字）：`test_login_search_lands_on_result_list`（首页填表 → 提交 → 落 list → 卡片≥1 → 参数与输入一致）、`test_re_search_from_result_page_via_left_nav`（结果页点左侧「酒店」回首页 → 改入住日与晚数 → 再搜仍出卡片）。scope 走 `ui_auth`，CI 无 state 时按 AUTH-01 skip，不进分母 |
| 销号条件 | 不需要销号：这是新增能力，不是限制。真要退场，删 `suites/auth` + `scripts/setup_auth.py` + `config/settings.py` 的「登录态」段即可，其余代码不依赖它 |

---

## K-08 真实退出会作废本机登录态（退出用例因此默认不真点）

| 项 | 内容 |
|---|---|
| 起因 | 2026-11-01 补「退出」形成鉴权闭环时发现：点一次「退出登录」，凭据就被消耗掉，这不是用例写错，是站点行为 |
| 实测证据（工具） | `tools/probes/probe_logout_effect.py`：阶段 A 加载 state → 确认已登录 → 点退出 → 判定未登录；阶段 B 用**同一份 state 文件**新开 context 重放 → 判定「未登录」 |
| 实测证据（cookie） | 退出前 ctrip 域登录相关：`AHeadUserInfo`、`DUID`、`IsNonUser`、`cticket`、`login_type`、`login_uid`、`suid`；退出后只剩 `GUID`、`_RGUID`、`_abtest_userid`、`suid` |
| 关键澄清 | **文件本身没被改写**（脚本从不写回 state），但文件里的 `cticket` 已作废。「母版只读所以副本用完即弃、母版安全」这个推理漏了一环：**cookie 的有效性不在文件里，在服务端**。所以「每次退出都开新副本」并不能保护母版 |
| 退出机制（实测） | 退出入口是普通链接：`a[class*='logout']`，`href=https://passport.ctrip.com/user/logout`，`onclick` 为空。整套动作就是导航到该 URL，由**服务端 302 + Set-Cookie** 清除。退出后 75 条请求里只有这一条带注销语义 |
| 推论：mock 不可行 | 任何「拦截注销请求、返回假响应」的 mock 都测不出退出：浏览器收不到 Set-Cookie，cookie 一条不少，页面仍是登录态。要让它变绿只能自己伪造 Set-Cookie，那验证的是我们写的假响应，不是携程的退出 |
| 浮层是懒渲染 | 悬停**前**扫全文档，与「退出」相关的节点数 = **0**，`body.innerText` 也不含「退出」；悬停后才出现。所以顺序只能是「先 hover，再找元素」，反过来会稳定超时并被误判成「站点没有退出功能」 |
| 落地做法 | ① `test_account_menu_exposes_logout_entry`（默认跑）：只 hover 展开、断言入口可见 + `href` 指向注销端点，**不点击**，可反复跑、可进 CI；② `test_real_logout_revokes_session`（`AUTH_REAL_LOGOUT=1` 才跑）：真实退出，跑完把 state 改名成 `*.consumed` —— 不改名的话文件看着还在，下次登录用例会红在「未检测到已登录态」，而没人会想到是它干的 |
| 每跑一次真退出 | 要重采一次：`python scripts/setup_auth.py --entry www`（或 `--entry trains`）。这一步无法自动化进 CI：需要本机登录的 PC 微信 + 有头桌面 |

---

## K-09 日历的起始月份跟着「已选日期」走（同屏只渲染两个月，必须会翻月）

| 项 | 内容 |
|---|---|
| 现象 | 先选远期（如 `2026-12-01`），再选近期（如 `2026-11-08`）时，日历重开后只渲染 `2026年12月 + 2027年1月`，`11月` 那个块**不存在**。旧实现按「年月标题」找块找不到就静默返回，提交时用的还是上一组的日期 |
| 取证时间 | 2026-11-01 18:4x–18:5x（`tools/probes/probe_flights_calpage.py`、`probe_cal_others.py`、`probe_hotel_calnav.py`、`probe_flights_calnav2.py`） |
| 实测证据（机票） | 重开日历可见 `[(2026,12),(2027,1)]`；补翻月后 `select_date(2026-11-08)` → input `'2026-11-08'`，日历已收（`reports/probe_flights_calpage/20261101-185341/`） |
| 实测证据（酒店） | 同样的「先远后近」：修复前 `Locator.click` 超时 15 s；修复后 → 表单变 `入住 11月8日`（`reports/probe_cal_others/20261101-185638/`） |
| 为什么 e2e 之前是绿的 | `suites/e2e` 那 3 组酒店 / 4 组机票的日期**恰好**都落在「当前可见两个月」内，等于靠运气。换一组数据、或调换执行顺序就会红 —— 第五轮机票第 4 组正是这么红的 |
| 顺带查出②：`Escape` 关不掉面板 | 机票 `div.calendar-modal` 按完 Esc 仍可见（探针 39 三次对照），酒店 `.c-calendar-month__days` 同理；两边 `close_calendar` 已统一改成「点面板外空白 + 断言隐藏」。酒店原来写的是 `Esc + to_have_count(0)`，面板开着时必抛断言，等于把「关不掉」报成「用例失败」 |
| 顺带查出③：翻月箭头没有语义 class | 机票是两个同名 `span.next-ico / span.prev-ico`，**只有一个可见**，用 `.first` 会拿到隐藏的那个（点击超时）；酒店是 `span.c-calendar-icon-prev / -next`，各一个且都可见。两边都按「取可见的那个」处理 |
| 落地做法（机票） | `FlightSearchPage.select_date()`：开日历 → 读可见月份 → 按方向点箭头直到目标月出现 → 按「年+月」定位月份块 → 块内点纯数字日格 → 点外地空白收面板 → **回读 input 必须等于目标日期**，不等就抛错并说明原因 |
| 落地做法（酒店） | `HotelSearchPage._goto_month()` + `_pick_day()`，同一套「先翻月再点」；`close_calendar()` 改为点空白 + 断言 |
| 日期置灰 | 日格 `span.date-d` 的 class 恒为 `date-d`，状态全挂在父节点 `div.date-day` 上。代码里对父 class 里的 `disab / not-allowed / forbid / invalid` 做防御性报错，但**真正判「能不能选」的是点完回读 input**。⚠️[未查实] 过去/不可售日期到底带哪个 token —— 取证当天是月初 1 号，本月没有过去日期可当样本 |
| 未覆盖 | 火车票的 `TrainSearchPage._goto_month` 只支持**向后**翻（`›`，最多 3 次），且月份标签不带年份。本期火车票数据都在当月，未触发；要跨年/往回翻得另做（探针未取证） |
| 销号条件 | 不需要销号：这是「按站点真实行为实现」，不是限制。若站点改成「日历总是从今天起渲染」，翻月循环会自然不再执行 |


## K-10 报告链路上的三个坑（Allure 接入，2026-11-01 实测）

| 项 | 内容 |
|---|---|
| 坑①：插件版本与 pytest 9 不兼容 | PyPI simple 索引**不保证排序**：列表尾部是 `allure-pytest 2.9.45`，实际稳定最新为 `2.16.2`。装 2.9.45 后 125 条用例在 **setup 阶段全部 ERROR**，报错在 `allure_pytest/listener.py:79` 的 `_test_fixtures(item)` —— 它读的 `item.session._fixturemanager` 在 pytest 9 已换 API。换 2.16.2 后实测 **125 passed**。口径已固化：查版本用 `scripts/check_latest.py`（SemVer 排序 + 排除预发布），见 `AGENTS.md` 第 3 条 |
| 坑②：多文件报告用 `file://` 打开是 500 | 浏览器打开 `reports/allure-report/index.html` 显示 **「500 Failed to fetch」**，不是报告坏了：多文件报告的数据靠 XHR 读 `data/*.json`，`file://` 下被安全策略拒绝。修法：`--single-file` 出 `reports/allure-report-single/index.html`（3.9 MB，数据内嵌，双击可用）。**但单文件不落 `history/`**，只留它会失去趋势 ⇒ 两份都要出，`scripts/allure_report.py` 已固化为默认行为 |
| 坑③：Behaviors 树只认三层 | 只有 epic/feature 而没有 `story` 的用例**不会进 Behaviors 树**：首轮 125 条里只有 2 条出现在该页。已在 `conftest._allure_labels` 里给每条用例补默认 story（e2e 保留自己的 `@allure.story`，避免多出一层）。注意 `widgets/behaviors.json` 的 `statistic.total` 显示 2 是它自己的直接子节点计数，**要看 `data/behaviors.json`**（实测 1 epic → 2 feature → 9 story）才算数 |
| 未覆盖 | Graphs 里的「趋势」需要至少两次 generate 才有两根柱子；本次只跑了两次、第二次已续上 history（日志 `历史续接：是`），但**还没做连续 3 次以上的趋势验证**。⚠️[未查实] Allure 2.46 在单文件模式下是否也渲染趋势图 |
| 与门禁的关系 | Allure 报告本身**不进 CI 门禁**：门禁仍读 junit（`scope` 写在 `user_properties`），报告只解决「人怎么看」。改报告链不会影响 `docs/BLOCKED-GATE.md` 的判定 |

## K-11 火车票城市联想面板会「点 A 填成 B」（2026-11-01 实测）

| 项 | 内容 |
|---|---|
| 现场 | `test_chain_trains[hz-nj]` 抛 `WidgetUnavailable: arrive 城市输入后值为 '南京南'，期望 '南京'`。同一条用例在 21:47、22:05、22:06 三轮**连续失败**（站点慢的时候稳定复现），20:02 那轮（站点快）是绿的 |
| 不是什么 | ① 不是选择器错：探针 `tools/probes/probe_train_city_panel.py` 取证面板是 1 个 `ul.widget-search-bd`，`li[0]` 的 `div.cont` 全文就是「南京」，`li[1]` 是「南京南」（`<span class=tblue>南京</span>南`），点 `li[0]` 得到「南京」——孤立环境 2/2 正确。② 与新增的登录态自检用例无关：单独跑 `-k trains` 同样复现 |
| 推断的机制 | ⚠️[未查实] 联想面板在输入停止后**仍会继续刷新/重排**，从「匹配读到 li[0]=南京」到「鼠标点下去」之间列表变了，于是点成了「南京南」。证据只到「旧代码点了精确匹配项却填出另一个确实存在于面板里的值」这一步，重排是唯一能解释它的候选，**没有抓到重排瞬间的 DOM 快照** |
| 已做的修复 | 判据从「点到了精确匹配项」改成「**点完读回输入框等于目标城市**」：不对就清空重来，最多 3 轮（`CITY_PICK_RETRY`）。失败时异常消息带上点选那一刻的面板快照 `_pick_trace`，避免下次只剩「值不对」三个字没法定位 |
| 修复的验证强度 | 重试路径**单独验过**：`python tools/probes/probe_train_city_panel.py --retry` 把第一次点选替换成「故意点南京南」，其余走生产代码与真实 DOM ⇒ 第 1 次填错、第 2 次纠正、最终「南京」。但**自然竞态在修复后的那轮运行里没有发生**（日志无「第 N 次点选才生效」），所以「真实竞态下重试一定能救回来」仍属 ⚠️[未查实] |
| 为什么不再往下挖 | 抓重排瞬间的快照需要在点击前后高频轮询 DOM，会拖慢每条用例；而「点完读回核对」无论重排机制是什么都能兜住。**判据改对了，机制可以在下次复现时再补证据** |

## K-12 清理器删旧产物极慢，让全量 pytest 在 sessionstart 静默卡住（2026-11-02 实测）

| 项 | 内容 |
|---|---|
| 现象 | 本地跑 `pytest tools/tests`（全量数据层门禁）时，日志停在 `[cleaner] 开始清理 …（阈值 24 小时）` 之后**一行进度都没有**，6~7 分钟不出结果。三次独立运行都卡在同一处，看起来像死锁或无限循环 |
| 量化（临时目录基准） | `tools/probes/probe_cleaner_bench.py`，自建 200 个 48h 前的 256B 文件：**扫描 0.02s（0.12 ms/文件），实删 49.83s（249 ms/文件，约 4 个/秒）**。放大到 2000 个：300s 超时被 `timeout` 杀掉，期间只删掉 1379 个（4.6 个/秒），速率与 200 批量一致 |
| 外推 | 2026-11-02 实测 `reports/` 里 24h 口径的过期文件 **2383 个** ⇒ 约 **594s（≈10 分钟）**。同一目录 6845 个文件在 72h 口径下「扫描 + 判定」只用 **3s**（日志：`扫描 6845 个文件，没有超过阈值的旧产物`）⇒ **瓶颈 100% 在 `unlink`，不在遍历/stat**。数值为外推，标 `[推算]`，不作归因证据 |
| 为什么「看起来像卡死」 | `clean_old_reports()` 只在开始、结束各打一行日志，`for path in target.rglob()` 循环内**无任何进度输出**。10 分钟里那两行之间是纯空白，从外部无法区分「在慢慢删」和「挂死了」。这是本次误判的直接原因 |
| 根因 | ⚠️[未查实] 单文件 `unlink` 249ms 偏高，但没定位到具体来源（杀软实时扫描 / G: 盘文件系统 / 单文件元数据同步，三个都是候选）。已排除：遍历与 `stat`（0.12 ms/文件）、`PermissionError` 跳过分支（基准里 `skipped=0`，200 个全删成功） |
| 临时规避（已验证） | ① `E2E_CLEAN_HOURS=72` —— 把阈值抬到 72h，本地实测 6845 个文件「无需清理」3s 返回，全量门禁随即正常跑完；② `E2E_CLEAN_DRY_RUN=1` —— 只统计不删。**不要用 `E2E_CLEAN_DISABLE=1` 代替**：它会连带跳过被它自己测的 4 条 `test_cleaner` 用例，那 4 条必红（2026-11-02 踩过） |
| 为什么不在本 PR 修 | 与本轮的往返轨道无因果关系，混进来会让「业务修复」和「基础设施重构」缠在一起。已登记为 `TODO.md` 第 5 条（编号稳定，不重编号） |
| 影响面 | 只影响**本地**全量跑（`reports/` 攒够 24h 以上旧产物时）。CI 每次都是干净工作区，没有这个问题 |
| 建议修法（未实施） | ① 循环内每 N 个文件打一次进度（先解决「看着像卡死」）；② 删不动时**降级为按目录整体 `shutil.rmtree`**（少一次 `unlink` 系统调用不解决根本，需实测）；③ 把清理挪到会话**结束**或独立脚本，别挡在 `pytest_sessionstart` 上 |

## K-13 单程直达车次列表整站返回「共 0 车次」（2026-11-03 00:27 起，疑似被限流）

| 项 | 内容 |
|---|---|
| 现象 | 单程轨道 3 条全部红，失败点是「结果里没有任何车次号」。看报告：条目只有 **5 条**、点到点 **0** 条；截图里页面显示「广州-深圳 单程 2026-11-13（共0车次）」+「未找到符合条件的车次」，下方仍有「中转方案推荐」区 |
| 时间线 | 00:19（`/tmp/all2.log`，改走 UI 后的全量跑）同一用例 `gz-sz-T+10-2026-11-13` 取到 **521 条**（点到点 516 / 覆盖率 100%）；00:27 起同一条只剩 **5 条**。中间只隔了 8 分钟，代码与数据都没动 |
| 证据链（排除本仓库代码） | ① **匿名 context 与登录态合并 context 都是 5 条** ⇒ 与登录态无关；② **三组不同区间/日期全空**：广州→深圳 11-13、广州→深圳 11-14、上海→北京 11-10 ⇒ 与单条数据无关；③ 页面正常渲染、表单回填正确、URL 的 `dDate` 正确、「未找到符合条件的车次」提示出现 ⇒ 不是选择器/交互错；④ **同期往返（`ticketType=1`）与中转（`ticketType=2`）仍能取到 193~205 张卡** ⇒ 不是整站不可用 |
| 00:43 复核（范围扩大） | 单点两次请求（登录态、间隔 20 秒）：单程 `ticketType=0` 仍是 5 卡 /「共0车次」；**往返 `ticketType=1` 已降到 0 卡** —— 00:22 同页还能取到 193~205 卡。⇒ 不只是单程，整个 list 页都在返回空。⚠️[未查实]：页面正文命中了「验证/拦截/安全」字样，但这个匹配**过宽**（页脚也可能出现"安全"二字），**不能据此断定是风控页**，只能确定"结果列表为空" |
| 判定 | **站点侧状态**（用户 2026-11-03 00:31 判断「估计是被限制了」，与上述证据一致）。不是 K-01/K-11 那类控件问题，也不是本轮「改走 UI」引入的回归 —— 迁移前后的差别只有 fixture（`page`→`shared_page`），而匿名态同样空 |
| 已做的处置 | ① **不改选择器、不放宽阈值**（放宽等于把站点降级伪装成通过）；② 在单程用例里加了一条**显式归因断言**：`declared_result_count == 0` 且页面含「未找到符合条件的车次」时直接 `pytest.fail`，消息写明「直达列表为空、不是选择器问题、同期往返/中转正常」，避免下次又把排查方向引到选取器上；③ 本轮不再重试（已连测 2 轮：单跑 + 三组矩阵探针，全空） |
| 复核方法 | `HEADED=1 python tools/probes/probe_degrade.py` —— 跑一张「匿名/登录态 × 单程/往返/中转」的矩阵表（`共N车次 / .list-item 数 / 含车次号数 / 未找到提示`），产物 `reports/probe/degrade_*.json`。恢复后直接重跑单程轨道即可 |
| 影响面 | 00:27 只影响**单程**；00:43 复核后**往返也空**（中转未复核）。三条轨道的数据断言目前都不可信，交互链路仍可验。若长期不恢复，属「外部服务不可用」，应记 blocked 而不是改断言 |

## K-14 火车票日期格「点了 A 落到 B」—— **已定位并修复（2026-11-07）**

| 项 | 内容 |
|---|---|
| 现象 | 目标 11-14 点完变成 11-17；目标 11-15 点完也是 11-17。同一个错误值出现在两次不同的运行里，而两次的目标日不同 |
| 复现条件 | 4 次「三模块串联」运行里出现 **2 次**（另 1 次是 K-02 的「预约」格，性质不同）。**单跑火车票用例 0 次复现**（27.56s 通过），定向探针（酒店首页 → 火车票）也复现不出 |
| 已排除 | ① 不是数据问题：同一 `cd-cq` 用例单独跑通过；② 不是「预约」格：日志里格子文本是 `'14'` / `'15'`（带票价），不是 `预约`；③ 不是选择器找不到格：找不到会抛「日历里找不到 X 日」，日志同一条路径往上还有「点 2026年 11月 14 日 → '14'」 |
| 根因（**实测确认**） | **`_close_calendar()` 里的 `mask.click(force=True)` 把刚选好的日期改掉了**。遮罩是整屏浮层、日历面板压在它上面，`force=True` 跳过命中校验 ⇒ 这一下实际点在**面板内部**。探针 `tools/probes/probe_close_calendar.py` 扫同一页面 4 个滚动位置，结果：`scrollY=0` 时遮罩中心 (760,368) 命中 `li[2026年 11月] ← H3`（月份标题，日期不变）；`scrollY=200` 时同一坐标命中 **`li[10] ← STRONG`**（10 号格子），点击后日期 **11-07 → 11-10**。即「点哪儿」完全由页面滚动位置决定 —— 失败那几次正好落在 17 号格上，于是 11-14/11-15/11-20 全变成 11-17。另一条独立证据：三次失败的报错文本都是旧格式（不带 `点击前 …`），而带那半句的是 `select_date` 内的第一道回读 ⇒ 点击后第一次回读**拿到的是目标值**，错值产生于关闭日历之后 |
| 处置（2026-11-07） | ① **`_close_calendar` 不再 force 点遮罩中心**：先给它 800ms 自己关；关不掉就按 `elementFromPoint` 在**遮罩上、面板外**挑一个点用**普通点击**关；再不行按 Esc；三招都失败才抛 `WidgetUnavailable`。② **取消「点后不生效就重点一次」**（用户裁决）：该分支触发 **0 次**，没有任何一次运行证明它有效，却留下「对同一个靶子连击」的机器特征。③ 点日期之前先 `human.move_neutral()`（消掉 hover 触发重绘）＋ `_hit_test()` 确认该坐标最上层就是目标格，不是就**先不点**、重解析（最多 3 轮，最多点 1 次）；④ 回读失败直接抛 `WidgetUnavailable`，报错带「点击前值 / 点击后值 / 点的月日 / 是否发生在关日历之后」 |
| 复发的判读 | 报错形如 `出发日期点选后为 '2026-11-17'，期望 '2026-11-20'（点击前 …, 点的是 2026年 11月 20 日）`：**点击前 = 点击后** ⇒ 点击完全没被接收（看 `_hit_test` 日志里命中了什么）；**点击后是第三个值且报错来自关日历之后** ⇒ 又有别的东西在改日期，查 `_close_calendar` 的三步兜底日志 |
| 影响面 | 只影响火车票的选日。三模块串联（e2e 轨）会因此整条红；单模块轨道偶发。**不放宽断言**：日期没选对就搜索，等于验的是别的数据 |
