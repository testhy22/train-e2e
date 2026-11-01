"""统一日志出口：控制台一行、文件一行（带模块与行号），并按大小轮转。

为什么单独一个文件（2026-11-01 用户要求「四位一体闭环」）：
  · 之前排查靠 `print` + shell 重定向，日志散在 `reports/logs/*.log`，没有级别、
    没有行号，出问题时无法回答「这行是谁打的」；
  · 而失败取证（截图 / DOM / 4xx 请求）已经在 `conftest.py` 里，两边互不相通。

设计上刻意的三条：

1. **文件用 RotatingFileHandler**（10MB × 5 份）。单文件无限增长会在跑几十轮之后
   把盘写满，而 `reports/` 本身还有清理器（`utils/cleaner.py`）按时间清旧文件，
   两者互补：轮转防单次跑爆，清理防长期堆积。
2. **控制台只到 INFO，文件到 DEBUG**。控制台是给人看的，页面里的噪音不该刷屏；
   文件是给事后查的，越细越好。
3. **`redact()` 在写盘之前做一次脱敏**。日志会被贴进 issue / 提交记录，
   而 `cticket`、`Cookie`、手机号这些一进仓库就是事故。

⚠️ 本模块**不**在 import 时创文件：路径来自 `config.settings`，而 settings 会建目录。
   `get_logger()` 第一次调用时才落盘，测试里可以随便 import 它而不产生副作用。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings

LOGGER_NAME = "ctrip_e2e"

# 文件格式：用户要求 `[时间] [级别] [模块:行号] - 信息`
FILE_FORMAT = "[%(asctime)s] [%(levelname)s] [%(module)s:%(lineno)d] - %(message)s"
FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"
CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
CONSOLE_DATEFMT = "%H:%M:%S"

_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

# 脱敏：命中即替换。宁可多打几个 ***，也不要把凭据写进日志文件。
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 会话凭据（携程的 cticket / 通用 token / bearer）
    (re.compile(r"(cticket=)[^;\s\"']+", re.I), r"\1***"),
    (re.compile(r"(\b(?:token|access_token|refresh_token|authorization)\s*[=:]\s*)[^\s;,}\"']+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
    # 整段 Cookie 头
    (re.compile(r"(cookie\s*[:=]\s*)[^\n]+", re.I), r"\1***"),
    # 手机号 / 身份证（数据文件里误写就会被这里兜住，不指望每次都记得查）
    (re.compile(r"\b1[3-9]\d{9}\b"), "1**********"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "***身份证***"),
)


def redact(text: object) -> str:
    """把凭据类内容打码。任何要落进日志的字符串都先过它。"""
    out = str(text)
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


def log_file_path() -> Path:
    """本次会话的日志文件路径（`reports/logs/run_YYYYmmdd_HHMMSS.log`）。"""
    return settings.REPORTS_DIR / "logs" / f"run_{_session_stamp()}.log"


_STAMP: str | None = None


def _session_stamp() -> str:
    """同一个进程内固定一个时间戳 —— 否则每次调用都会得到新的文件名。"""
    global _STAMP
    if _STAMP is None:
        _STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _STAMP


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """取（并首次装配）统一 logger。重复调用不会叠加 handler。"""
    logger = logging.getLogger(name)
    if logger.handlers:                      # 已装配过
        return logger

    logger.setLevel(logging.DEBUG)
    # 不向 root 冒泡：否则 pytest 的 logging 插件会把每条再打一遍，控制台翻倍。
    logger.propagate = False

    log_dir = settings.REPORTS_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_dir / f"run_{_session_stamp()}.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=FILE_DATEFMT))
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=CONSOLE_DATEFMT))
    logger.addHandler(console)

    return logger


def logger_info(message: object) -> None:
    """打一条 INFO（自带脱敏）。

    `stacklevel=2` 是关键：不加的话文件里每一行都写 `[logger:114]`，
    因为记录点是这个包装函数本身。加了之后 `模块:行号` 指向**真正调用方**，
    这才是用户要的「一眼看出是哪段代码打的」。
    """
    get_logger().info(redact(message), stacklevel=2)


def logger_warning(message: object) -> None:
    get_logger().warning(redact(message), stacklevel=2)


def logger_error(message: object) -> None:
    get_logger().error(redact(message), stacklevel=2)


def logger_debug(message: object) -> None:
    get_logger().debug(redact(message), stacklevel=2)
