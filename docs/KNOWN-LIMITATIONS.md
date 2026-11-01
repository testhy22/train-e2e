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
| 保留的契约测试 | `test_list_page_renders_with_full_params`（`suites/hotels/tests/test_hotel_search.py`）。断言「full 参数 + 匿名直开 list 能出卡片」。**本条原先写的是 `test_login_wall_is_global`（断言直开必被拦），实测一跑就红**——按 `reports/probe_www_hotels_06.json` 的事实替换：登录墙挂在「参数完整性 + 会话状态」，不是「列表页不可访问」。2026-11-01 核对时本行仍引用旧名，已按代码实际内容更正 |
| 销号条件 | `test_login_wall_is_global` 连续 3 天在 CI 中失败（即不再被接管），且能稳定取到酒店卡片 ≥1 条 |
| 二期方向 | 酒店级别、关键词字段；儿童年龄控件；有合法登录态的独立轨道（需另立决策，不在本期） |
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
| **范围（重要）** | **只作用于 trains 子域，不是整站。** `www.ctrip.com` 的酒店模块在 headless 下正常出结果：2026-11-01 三份 junit 逐条一致（`00:43` headless、`01:21` headless、`01:43` headed 均 3 passed）。曾把这条写成「无头模式整站被拦」，是错的，已更正 |
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
| 与匿名轨的隔离 | 三条硬隔离：scope 独立（`ui_auth`，不并入 ui / ui_hotels 的 ratio 序列）；context 独立（匿名用例仍匿名，K-04 的「提交路径落 passport」实验条件不被改）；缺文件时 `skip("known-limit: AUTH-01 …")` 而非 fail（不进分母，不让 CI 永久红） |
| 凭据边界 | state 是**明文会话 cookie**，等价账号凭据。`config/auth/` 已进 `.gitignore`；不进 CI、不进 secret；不因被拦而换账号重试。谁要把 state 放进 CI secret，等于同时推翻 K-04「CI 无法安全持有凭据」的判断，必须重新裁决 |
| 已实测的坑① | 首页底部「周周享低价 / 领券更划算」浮层带一张**营销二维码**（2026-11-01 截图证据），会让「二维码可见」类断言假绿。`utils/overlay.py` 已在 `BasePage.goto` 后自动关它，`OVERLAY_AUTOCLOSE=0` 可关 |
| 已实测的坑② | 按窗口标题「微信」判断 PC 微信会**误命中**：同一次 `EnumWindows` 里 Chrome 窗口标题也含「微信」。`utils/wechat.py` 改用类名 `Qt51514QWindowIcon` 作主判据，标题只做弱兜底 |
| 未完成 | `test_submit_then_list_renders`（`suites/hotels`）仍是 `skip`。它有登录态后**可能**可以启用，但「带 state 跑酒店提交路径是否还落 passport」这次没验证，别默认它能绿 |
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
