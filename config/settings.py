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
BROWSER_UI_ARGS = (
    "--no-first-run",                     # 首次运行引导
    "--no-default-browser-check",         # 默认浏览器询问
    "--disable-save-password-bubble",     # 保存密码气泡（挡住页面右下）
    "--disable-search-engine-choice-screen",  # 搜索引擎选择页（会整页劫持）
    # 翻译条：截图里的「英语 / 中文（简体）」就是它。TranslateUI 是旧名，两个都列。
    "--disable-features=Translate,TranslateUI",
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


def ensure_dirs() -> None:
    """建立证据目录。conftest 与 CI 都调用它，避免各自拼路径。"""
    for d in (SCREENSHOT_DIR, TRACE_DIR, HTML_REPORT.parent):
        d.mkdir(parents=True, exist_ok=True)
