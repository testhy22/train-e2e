"""登录态判据的唯一事实源。

`scripts/setup_auth.py`（采集）和 `suites/auth`（验证）必须用**同一套判据**。
两边各写一份的后果很具体：脚本判定「登录成功，state 已保存」，用例却判定「没登录」，
然后排查方向会变成怀疑 state 文件写入、怀疑 context 隔离、怀疑站点，而不是那条判据本身。

判据顺序按「反证强于正证」排：
  1. 顶部「注册」可见 ⇒ **一定**未登录。
     依据：2026-11-01 用户截图，未登录态顶部导航同时出现「登录」「注册」；
     登录成功后「注册」消失。这是本项目唯一有画面证据的判据。
  2. 头像 / 账户区元素可见 ⇒ 已登录。⚠️[未查实]：下面的类名候选一次都没实测过，
     携程构建后 class 可能带哈希。命中不上不要紧，还有第 3 条兜底。
  3. 页面出现「退出」 ⇒ 已登录。同样是语义兜底。
三条都不命中 ⇒ 判未登录，并把「没命中任何判据」原样返回 —— 不猜、不假设成功。

新增判据时只改这个文件：改一处，采集和验证同时生效。
"""

from __future__ import annotations

from collections import Counter

from playwright.sync_api import Page

# 未登录态独有的文本。用 exact=True 匹配，避免命中「注册领券包」这类营销文案
# （首页中部那条「免费注册领券包」横幅是真实存在的，2026-11-01 截图可见）。
LOGGED_OUT_TEXT = "注册"

# 已登录态的语义兜底。
LOGGED_IN_TEXT = "退出"

# 头像 / 账户区候选。**全部 ⚠️[未查实]**，按稳定性排序：结构语义 > 类名片段。
AVATAR_CANDIDATES: tuple[str, ...] = (
    "img[class*='avatar']",
    "[class*='avatar'] img",
    "[class*='user-info'] img",
    "[class*='head-img']",
    "[class*='userCenter']",
)

# passport 登录墙的域特征。K-04 / K-06 两次现场都落在 passport.ctrip.com。
LOGIN_WALL_HOST = "passport.ctrip.com"


def is_logged_in(page: Page) -> tuple[bool, str]:
    """当前页是否已登录，返回 (结果, 证据)。

    证据是给报告和诊断用的原文，不要简化成布尔值 —— 判错时要能一眼看出
    是哪一条判据给的结果。
    """
    try:
        if page.get_by_text(LOGGED_OUT_TEXT, exact=True).first.is_visible():
            return False, f"顶部「{LOGGED_OUT_TEXT}」仍可见 ⇒ 未登录"
    except Exception:
        pass

    for selector in AVATAR_CANDIDATES:
        try:
            if page.locator(selector).first.is_visible():
                return True, f"头像区可见：{selector}"
        except Exception:
            continue

    try:
        if page.get_by_text(LOGGED_IN_TEXT, exact=False).count() > 0:
            return True, f"页面出现「{LOGGED_IN_TEXT}」"
    except Exception:
        pass

    return False, "未命中任何登录判据（三条都没验到）"


def looks_like_login_wall(url: str) -> bool:
    """当前 URL 是否在 passport 登录墙域。只用于写报告，不用来改断言。"""
    return LOGIN_WALL_HOST in (url or "")


def state_summary(data: dict) -> str:
    """读回 storage_state 并统计，回答一个具体问题：这份 state 大概能用多久。

    会话级 cookie（expires=-1）占比高时给出警告，但**不据此判失败**——
    这是站点下发的生命周期，不是脚本缺陷，改脚本也绕不过去。
    """
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    if not cookies:
        return "cookies: 0（空 state —— 登录没成功就被存下来了，这份文件不可用）"

    domains = Counter(c.get("domain", "?") for c in cookies)
    session_only = sum(1 for c in cookies if c.get("expires") in (-1, 0))
    ratio = session_only / len(cookies) * 100

    lines = [
        f"cookies: {len(cookies)} 条，origins: {len(origins)} 组",
        "按域：" + "、".join(f"{d}×{n}" for d, n in domains.most_common(6)),
        f"会话级（expires=-1）：{session_only} 条 = {ratio:.0f}%",
    ]
    if ratio > 50:
        lines.append(
            "⚠️ 会话级 cookie 占比过半：下次起进程可能仍要重新扫码。"
            "这是站点下发的 cookie 生命周期，不要靠改脚本来绕。"
        )
    return "\n".join(lines)
