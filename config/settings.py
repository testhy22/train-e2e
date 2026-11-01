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


def auth_state_path() -> Path:
    """登录态文件路径。

    只返回路径，**不判断存在性** —— 存在性判定归 conftest 的 auth_state 夹具，
    那里要负责「缺文件时 skip 而不是 fail」（否则 CI 永久红）。
    """
    return AUTH_STATE_FILE


# ---------------------------------------------------------------- 浏览器

HEADLESS = os.getenv("HEADED") != "1"
VIEWPORT = {"width": 1920, "height": 1080}
LOCALE = "zh-CN"
TIMEZONE_ID = "Asia/Shanghai"

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
