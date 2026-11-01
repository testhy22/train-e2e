"""唯一的配置出口。

规则：除本文件外，任何地方不得出现硬编码的 URL、路径或超时数值。

关于 BASE_URL：默认指向真实站点，可用环境变量覆盖（本地调试、换目标站都只改这一处）。
本工程不使用任何反检测手段：不伪装 UA、不改自动化标记、不按「哪里被拦」调参重试。
被风控拦下是一等结果（blocked → xfail），不是要被消灭的障碍。

唯一的例外是有明确边界的：`HUMAN` 拟人节奏（见文件末「拟人节奏」段与 utils/humanize.py）。
它只改**时间**不改**身份**，用途是让人在有头模式下看得清操作过程；它**不是**规避手段，
也不允许因为被拦而调慢重试。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 目标站点

# 默认站点。覆盖方式：BASE_URL=https://... python -m pytest
DEFAULT_BASE_URL = "https://trains.ctrip.com"

# ---------------------------------------------------------------- 业务规则上限

# 酒店搜索轨道的站点根（D1 裁决 2026-11-01）。
# 选 www.ctrip.com 而不是 hotels.ctrip.com 的唯一理由：需求里的「酒店级别」字段
# 只在首页酒店模块存在。实测（reports/probe_www_hotels_01.json）：
#   hotels.ctrip.com → 只有 目的地/入住/退房/人数/搜索，无「酒店级别」「关键词」
#   www.ctrip.com    → 五段齐全（目的地/入住/退房/房间及住客/酒店级别/关键词）
HOTELS_BASE_URL = "https://www.ctrip.com"

# 机票搜索轨道的站点根（2026-11-01 新增，e2e 串联轨道第三站）。
# 选 flights.ctrip.com 的「国内机票」入口，不是 www 首页的机票模块：
# 实测（probe_flights_home.py）该页五段齐全（单程/往返/多程 + 出发地/目的地/
# 出发日期/乘客类型 + 搜索），并且提交后落 `/online/list/oneway-<三字码>-<三字码>?depdate=…`，
# 这个 URL 本身就是可断言的外部证据。
FLIGHTS_BASE_URL = "https://flights.ctrip.com"

# 铁路客票预售期（天，含当日）。
# 2026-10 核实，来源：12306 官方口径（全国铁路统一预售期 15 天；第三方宣称的
# 30/90 天预约均为营销说法，实际不可售）。预售期若调整，只改这一处。
MAX_PRESALE_DAYS = 15

# ---------------------------------------------------------------- 证据目录

REPORTS_DIR = PROJECT_ROOT / "reports"
SCREENSHOT_DIR = REPORTS_DIR / "screenshots"
TRACE_DIR = REPORTS_DIR / "trace"
HTML_REPORT = REPORTS_DIR / "html" / "report.html"

# ---------------------------------------------------------------- 超时

DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "15000"))
EXPECT_TIMEOUT_MS = int(os.getenv("EXPECT_TIMEOUT_MS", "5000"))
NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "30000"))

# ---------------------------------------------------------------- 登录态
#
# 2026-11-01 用户裁决（「立即推进微信快捷登录」）后新增。这一节成立的前提是三条边界
# 同时守住，缺一条它就退化成「拿凭据换通过率」，与文件头的 D 决策冲突：
#   1. 凭据只由**真人扫码**产生。工程不代填账号密码、不模拟扫码、不碰验证码。
#   2. 凭据绝不入库、绝不进 CI。config/auth/ 在 .gitignore 里；CI 没有这个文件时
#      auth 轨道固定 skip，不靠 secret 注入（K-04 已判过「CI 无法安全持有凭据」）。
#   3. 不因被拦而换账号、换 state 重试。被拦是结果，不是要消灭的障碍。
# 边界与缺口记录见 docs/KNOWN-LIMITATIONS.md 的 K-07。

# 登录态文件。文件存在 ≠ 已登录；内容是否还有效只有真跑一次才知道。
AUTH_STATE_FILE = PROJECT_ROOT / "config" / "auth" / "ctrip_state.json"
AUTH_STATE_DIR = AUTH_STATE_FILE.parent

# 「建议重扫」阈值（天）。超阈值只告警、不阻断：mtime 只能说明「存了多久」，
# 说明不了「还有没有效」，拿它判失效会造出假失败。
AUTH_STATE_TTL_DAYS = int(os.getenv("AUTH_STATE_TTL_DAYS", "7"))

# 是否给**所有**用例的 context 挂 storage_state。默认 False，原因具体：
# 酒店轨道现有断言依赖「匿名态」这个实验条件——提交路径落 passport（K-04）、
# 落点写进报告（test_hotel_search.py:84-90）。全局挂上登录态会把实验条件改掉，
# 报告里的 login_wall= 从 True 变 False，读报告的人会以为登录墙放开了。
# 要全局生效，显式设 AUTH_GLOBAL=1，并自己承担上面两个语义变化。
AUTH_GLOBAL = os.getenv("AUTH_GLOBAL", "").strip() == "1"

# 营销浮层自动关闭开关。2026-11-01 截图证据：www.ctrip.com 首页底部
# 「周周享低价 领券更划算」浮层带一张**营销二维码**，它既挡底部点击，
# 又会让「二维码可见」类断言假绿。默认开；OVERLAY_AUTOCLOSE=0 关掉。
OVERLAY_AUTOCLOSE = os.getenv("OVERLAY_AUTOCLOSE", "1").strip() != "0"


def auth_state_path(entry: str = "www") -> Path:
    """登录态文件路径。按入口站分文件，**不同站各存一份**。

    为什么要分（2026-11-01 实测）：www.ctrip.com 登录后，用同一份 state 打开
    trains.ctrip.com 仍判未登录 —— 两站登录态不互通。若两站共用一份文件，
    跑 `--entry trains` 会把刚采好的 www 会话**覆盖掉**，而且覆盖后不会报错，
    只会在下次跑 auth 用例时莫名其妙地判「未登录」。

    www 沿用 `ctrip_state.json` 原名：现有文件、README 与历史报告都指向它，
    为分文件而改名会让旧记录全部指向不存在的路径。
    """
    if entry == "www":
        return AUTH_STATE_FILE
    return AUTH_STATE_DIR / f"ctrip_state_{entry}.json"


# ---------------------------------------------------------------- 浏览器

HEADLESS = os.getenv("HEADED") != "1"
VIEWPORT = {"width": 1920, "height": 1080}
LOCALE = "zh-CN"
TIMEZONE_ID = "Asia/Shanghai"

# 物理窗口最大化（2026-11-01 用户追加要求）。
#
# 为什么需要单独一个开关，而不是直接把 viewport 删掉：
# 最大化是「有头」才存在的概念。headless 下 `--start-maximized` 不生效，
# 而 `no_viewport=True` 会让页面退到默认 1280×720 —— 比原来固定的 1920×1080 更小，
# 本站的「筛选条件默认折叠/平铺」正好在 1280 与 1920 之间切换（用户追加需求里的坑 1），
# 所以无头必须继续用固定视口，不能跟着一起改。
#
# 关闭方式：MAXIMIZE_WINDOW=0（比如想在固定 1920×1080 下复现某次失败）。
START_MAXIMIZED = os.getenv("MAXIMIZE_WINDOW", "1").strip() != "0"


def window_maximized() -> bool:
    """是否走「物理最大化 + 视口自适应」。

    只有「有头 + 开关未关」才成立。CI 的 trains job 走 `xvfb-run -a` + `HEADED=1`，
    那种情况下最大化作用于虚拟屏幕（xvfb 默认 1280×1024），语义仍然是
    「视口跟随窗口」，不是「写死 1920」。要在 CI 里固定尺寸就设 MAXIMIZE_WINDOW=0。
    """
    return (not HEADLESS) and START_MAXIMIZED


def context_view_args() -> dict:
    """context 的视口参数：最大化时 `no_viewport=True`，无头时固定 VIEWPORT。

    为什么不能两个都给：`viewport` 一旦存在，Playwright 就用它覆盖窗口内容区，
    `--start-maximized` 就白做了 —— 窗口最大、页面仍按 1920 排版。
    反过来，无头时给 `no_viewport` 会让页面退到 1280×720，比 1920 更小，
    而本站筛选区的折叠/平铺正好在这两个宽度之间切换。
    conftest 与探针共用这一个函数，避免各自判断导致尺寸不一致。
    """
    return {"no_viewport": True} if window_maximized() else {"viewport": VIEWPORT}

# 浏览器**自带 UI** 的关闭项。2026-11-01 用户在截图里指出两处弹层会挡住页面：
#   1. 「www.ctrip.com 想要获取您的位置」原生权限气泡 —— 挡住右上角账号区，
#      恰好压在 hover 用户名那个位置；
#   2. 「英语 / 中文（简体）→ Google Translate」翻译气泡 —— 同样压在右上角。
#
# 两者的处理方式不同，别混为一谈：
#   · 权限气泡由 `permissions=[]` 处理（实测四项探针全为 denied，见下方注释）；
#   · 翻译气泡是 Chrome **内置功能**，不受 permissions 影响，只能靠启动参数关。
#
# 这组参数**不是**反检测：不碰 UA、不碰 navigator.webdriver、不注入脚本、
# 不改指纹。它们只关掉浏览器自己的浮层 —— 与 `permissions=[]` 同一性质
# （都是「让浏览器别插嘴」，而不是「让站点认不出我们」）。
# 项目红线见 pages/base_page.py 文件头，这条边界不能跨。
# 为什么**不**用「持久化 profile + 改 Preferences」来关翻译（2026-11-01 评估结论）：
#   提法是 `launch_persistent_context` + 改写 `user_data_dir/Default/Preferences` 的
#   `translate.enabled=false`。它确实比启动参数彻底，但代价不可接受，三条：
#     1. 非持久化 context 的 profile 是**临时目录**，启动后即销毁 —— 没有可改的
#        Preferences。要用这条路就必须换成持久化 context；
#     2. 持久化 profile 会保留 cookie / localStorage，于是**匿名轨道不再匿名**。
#        `ui` / `ui_hotels` 两条 ratio 序列与 K-04（匿名提交必落 passport）的
#        实验条件当场失效，而这两条是本工程最有信息量的账本；
#     3. `storage_state` 在持久化 context 里只能用 `add_cookies()` 注入，
#        **丢掉 origins（localStorage）**，而我们的 state 里正好有 origins。
#   结论：启动参数 + `--lang` 已能关掉这个气泡（2026-11-01 截图确认），零副作用。
#   要改成持久化 profile 属于换实验条件，必须重新裁决，不能顺手替换。
BROWSER_UI_ARGS = (
    "--no-first-run",                     # 首次运行引导
    "--no-default-browser-check",         # 默认浏览器询问
    "--disable-save-password-bubble",     # 保存密码气泡（挡住页面右下）
    "--disable-search-engine-choice-screen",  # 搜索引擎选择页（会整页劫持）
    # 翻译条：截图里的「英语 / 中文（简体）」就是它。TranslateUI 是旧名，两个都列。
    "--disable-features=Translate,TranslateUI",
    # 2026-11-01 追加：官方文档里 `--disable-translate` 已是 --disable-features=Translate 的
    # 旧别名，但旧版二进制仍认它，且代价为零 —— 作为第二道保险保留。
    "--disable-translate",
    # 浏览器 UI 语言显式钉成 zh-CN。context 的 locale 只管页面 `Accept-Language` 与
    # `navigator.language`，管不到浏览器**自己**的界面语言；界面语言是 en 时，
    # 访问中文站点更容易弹翻译气泡（用户追加需求里的「多语言误触发」）。
    "--lang=zh-CN",
    # 物理最大化。放在这里而不是只放在 conftest，是为了让 `tools/probes/*.py`
    # 用同一份参数启动浏览器 —— 探针窗口与用例窗口尺寸不一致时，
    # 「探针里能点、用例里点不到」这类问题会浪费一整轮排查。
    # 无头模式下 Chrome 会忽略它，无害。
    "--start-maximized",
)

# 实测记录（2026-11-01，/tmp 探针跑 navigator.permissions.query + 三组对照）：
#   permissions=[]                    → geolocation/notifications/camera/microphone 全 denied
#   permissions=["local-network-access"] → 同样全 denied（Playwright 语义是**替换**，不是追加）
#   不传 permissions                  → geolocation=denied，其余三项 = prompt
# 所以「位置权限气泡」只在**不传 permissions** 时才会弹。conftest 与 setup_auth
# 两处都必须显式传，任何一处漏了就会看到那个气泡。

# 失败即证据：只在失败时保留 trace。理由见 PROJECT-PLAN 5.D2——
# on-first-retry 需要重试才会产出 trace，而本项目不引入重试插件。
TRACE_MODE = "retain-on-failure"

# ---------------------------------------------------------------- 输入节奏

# 单字符输入延迟（毫秒）。这是为了触发页面的联想下拉，不是为了规避检测。
TYPE_DELAY_MS = 80
# 单次输入整串的总时长上限（秒），防止长城市名把用例拖慢。
TYPE_MAX_TOTAL_S = 3.0

# ---------------------------------------------------------------- 拟人节奏

# 开关三态（解析逻辑在 utils/humanize.human_enabled）：
#   HUMAN=1 强制开 / HUMAN=0 强制关 / 未设 ⇒ 跟随 HEADED（有头开、无头关）
# 为什么默认跟随 HEADED：无头跑没有观众，慢只拖长反馈周期；CI 因此永远不会意外变慢。
HUMAN_RAW = os.getenv("HUMAN", "").strip()

# 固定随机种子 ⇒ 每次跑同样的停顿序列，便于复现时序问题。0 / 空 表示每次随机。
HUMAN_SEED = int(os.getenv("HUMAN_SEED", "0") or 0) or None


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


def _resolve_url(raw: str, env_name: str) -> str:
    if not raw.startswith(("http://", "https://", "file://")):
        raise ConfigError(f"{env_name} 必须以 http:// https:// file:// 开头，收到 {raw!r}")
    return raw.rstrip("/")


def base_url() -> str:
    """返回目标站点根地址。环境变量优先，否则用 DEFAULT_BASE_URL。"""
    return _resolve_url((os.getenv("BASE_URL", "").strip() or DEFAULT_BASE_URL).strip(), "BASE_URL")


def hotel_base_url() -> str:
    """酒店轨道的站点根。环境变量 HOTEL_BASE_URL 优先，否则用 HOTELS_BASE_URL。"""
    return _resolve_url((os.getenv("HOTEL_BASE_URL", "").strip() or HOTELS_BASE_URL).strip(), "HOTEL_BASE_URL")


def flight_base_url() -> str:
    """机票轨道的站点根。环境变量 FLIGHT_BASE_URL 优先。"""
    return _resolve_url(
        (os.getenv("FLIGHT_BASE_URL", "").strip() or FLIGHTS_BASE_URL).strip(), "FLIGHT_BASE_URL"
    )


def ensure_dirs() -> None:
    """建立证据目录。conftest 与 CI 都调用它，避免各自拼路径。"""
    for d in (SCREENSHOT_DIR, TRACE_DIR, HTML_REPORT.parent):
        d.mkdir(parents=True, exist_ok=True)
