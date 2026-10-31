"""唯一的配置出口。

规则：除本文件外，任何地方不得出现硬编码的 URL、路径或超时数值。

关于 BASE_URL：默认指向真实站点，可用环境变量覆盖（本地调试、换目标站都只改这一处）。
本工程不使用任何反检测手段：不伪装 UA、不改自动化标记、不做规避识别的随机节奏。
被风控拦下是一等结果（blocked → xfail），不是要被消灭的障碍。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 目标站点

# 默认站点。覆盖方式：BASE_URL=https://... python -m pytest
DEFAULT_BASE_URL = "https://trains.ctrip.com"

# ---------------------------------------------------------------- 业务规则上限

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


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


def base_url() -> str:
    """返回目标站点根地址。环境变量优先，否则用 DEFAULT_BASE_URL。"""
    url = (os.getenv("BASE_URL", "").strip() or DEFAULT_BASE_URL).strip()
    if not url.startswith(("http://", "https://", "file://")):
        raise ConfigError(f"BASE_URL 必须以 http:// https:// file:// 开头，收到 {url!r}")
    return url.rstrip("/")


def ensure_dirs() -> None:
    """建立证据目录。conftest 与 CI 都调用它，避免各自拼路径。"""
    for d in (SCREENSHOT_DIR, TRACE_DIR, HTML_REPORT.parent):
        d.mkdir(parents=True, exist_ok=True)
