"""检测本机 PC 微信是否在运行，必要时弹一个系统提示。

实测证据（2026-11-01，本机 EnumWindows，可见窗口 12 个）：
    微信 4.x 主窗口 → 标题「微信」，类名 `Qt51514QWindowIcon`，可见。
    3.x 旧版类名 `WeChatMainWndForPC` ⚠️[未查实]，按候选处理。

⚠️ 这个模块**证明不了**「微信已登录」，措辞必须卡死在这里：
    窗口存在只说明客户端在跑；微信未登录时同样有主窗口（里面显示二维码）。
    所以对外只能说「未检测到正在运行的微信客户端」，
    写成「未登录 PC 微信」就是给出了查不到的结论 —— 那是编。
    要真判登录态，得读窗口里的二维码/登录提示（UIA 或 OCR），
    成本远高于这条提醒的价值，本期不做。

用途只有一个：`scripts/setup_auth.py` 动手前提醒一句
「如果你打算用 PC 微信扫码，先把微信打开并登录好」。
用手机扫网页二维码的人根本不受影响，可用 --no-wechat-check 跳过。

绝不放进 conftest：系统级 MessageBox 会**阻塞到有人点它**，
CI 里没人点，流水线会一路卡到 job 超时。本模块自己也有一层
`can_show_dialog()` 防护（CI / 非 tty / 非 Windows 一律只打印不弹窗），
两层都在，是因为这类卡死的代价是整条流水线，不是一条用例。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys
from dataclasses import dataclass

# 类名是**主判据**，标题只做辅助。
# 为什么标题不能当主判据：同一次枚举里 Chrome 的窗口标题是
# 「携程微信扫码登录自动化 - DeepSeek - ego」，也含「微信」两个字。
# 只按标题匹配会把它当成微信，然后告诉用户「微信已就绪」——错得还很自信。
WECHAT_WINDOW_CLASSES: tuple[str, ...] = (
    "Qt51514QWindowIcon",  # 4.x 实测命中
    "WeChatMainWndForPC",  # 3.x ⚠️[未查实]
    "WeChat",
)

WECHAT_WINDOW_TITLES: tuple[str, ...] = ("微信", "WeChat")

# MessageBoxW 的 flags。用 MB_OKCANCEL 而不是 MB_OK：
# 给用户一个「我不想继续」的出口，比只能点确定更诚实。
MB_OKCANCEL = 0x00000001
MB_ICONINFORMATION = 0x00000040
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
IDOK = 1


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str


def _enum_visible_windows() -> list[WindowInfo]:
    """枚举所有可见顶层窗口。非 Windows 返回空列表。"""
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    found: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            found.append(
                WindowInfo(hwnd=int(hwnd), title=title, class_name=class_buf.value)
            )
        except Exception:
            # 单个窗口取不到属性不该中断整轮枚举
            pass
        return True

    user32.EnumWindows(_callback, 0)
    return found


def detect_wechat() -> tuple[bool, str]:
    """返回 (是否检测到微信客户端, 证据)。

    证据是给人看的，要能直接回答「凭什么这么判」——
    写进 setup_auth 的输出，也方便下次类名变了能一眼看出枚举到了什么。
    """
    windows = _enum_visible_windows()
    if not windows:
        return False, "无法枚举窗口（非 Windows，或无桌面会话）"

    for window in windows:
        if window.class_name in WECHAT_WINDOW_CLASSES:
            return True, (
                f"命中类名 {window.class_name}（窗口标题「{window.title}」）"
            )

    # 标题兜底：类名列表过期时至少还能看出「像是有个微信窗口」，
    # 但明确标注它不如类名可靠，不能当成已登录的依据。
    by_title = [
        w for w in windows
        if w.title in WECHAT_WINDOW_TITLES and not w.class_name.startswith("Chrome_WidgetWin")
    ]
    if by_title:
        return True, (
            f"类名未命中，但标题精确等于「{by_title[0].title}」的窗口存在"
            f"（类名 {by_title[0].class_name}）—— 判据较弱，仅供参考"
        )

    classes = sorted({w.class_name for w in windows})[:8]
    return False, f"枚举了 {len(windows)} 个可见窗口，没有微信类名；样本类名：{classes}"


def can_show_dialog() -> tuple[bool, str]:
    """能不能安全弹一个阻塞式系统对话框。返回 (可以, 原因)。

    CI 里弹窗 = 没人点 = 卡到超时。所以三个条件缺一不可，
    而且任何一个不满足都只降级为「打印提示」，不影响流程继续。
    """
    if sys.platform != "win32":
        return False, "非 Windows 平台"
    if os.getenv("CI", "").strip():
        return False, "检测到 CI 环境变量"
    if not sys.stdin or not sys.stdin.isatty():
        return False, "stdin 不是交互终端（可能被重定向或由调度器启动）"
    return True, "交互式桌面会话"


def prompt_user_login(detail: str) -> bool:
    """弹系统提示，用户点「确定」返回 True，点「取消」返回 False。

    只在 can_show_dialog() 为真时调用 —— 这里不做二次判断，
    因为调用方需要知道「到底弹没弹」，隐藏这个事实会让日志说谎。
    """
    text = (
        "未检测到正在运行的 PC 微信客户端。\n\n"
        f"判据：{detail}\n\n"
        "如果你打算用「PC 微信扫码 / 一键授权」，请先打开并登录微信，再点「确定」继续。\n"
        "如果你用手机扫网页上的二维码，这一步与你无关，直接点「确定」即可。"
    )
    caption = "携程登录态采集 · 启动前检查"

    user32 = ctypes.windll.user32
    flags = MB_OKCANCEL | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
    try:
        return user32.MessageBoxW(None, text, caption, flags) == IDOK
    except Exception:
        # 弹不出来不能变成「流程失败」——降级为打印，让调用方继续
        print(f"  ⚠️ 系统弹窗失败，降级为文字提示：\n{text}")
        return True
