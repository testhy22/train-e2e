"""登录态判据的唯一事实源。

`scripts/setup_auth.py`（采集）和 `suites/auth`（验证）必须用**同一套判据**。
两边各写一份的后果很具体：脚本判定「登录成功，state 已保存」，用例却判定「没登录」，
然后排查方向会变成怀疑 state 文件写入、怀疑 context 隔离、怀疑站点，而不是那条判据本身。

判据顺序按「反证强于正证」排：
  1. 顶部「注册」或「登录」可见 ⇒ **一定**未登录。
     依据：2026-11-01 用户截图，未登录态顶部导航同时出现「登录」「注册」；
     登录成功后这两个都消失。
  2. 用户名容器可见且文本形态像账号 ⇒ 已登录（**实测最可靠的一条正证**）。
     登录态首页顶部是 `span[class*='header_login_title_name']` 里的纯文本用户名，
     既没有头像 img，也没有「退出」，所以只认头像的老判据会全部落空。
  3. 头像 / 账户区 img 可见 ⇒ 已登录。类名候选命中率一般（携程构建后 class 带哈希）。
  4. 页面出现「退出」 ⇒ 已登录。语义兜底（该站要悬停才出现，命中率低）。
  5. 「登录」「注册」都不可见，且「我的订单」可见 ⇒ 已登录。
     兜底条：前四条都没命中时才用。「我的订单」在这里不是为了证明登录，
     而是证明**页面真的加载出来了** —— 否则空白页/风控页也会因为"没有登录按钮"
     被误判成已登录。
五条都不命中 ⇒ 判未登录，并把「没命中任何判据」原样返回 —— 不猜、不假设成功。

**调用时机也算判据的一部分**（2026-11-01 实测）：首页 `domcontentloaded` 之后
header 才被 JS 换成用户名，这段时间里「注册」还在。页面刚打开就调 `is_logged_in`
会把已登录判成未登录 —— 要判登录，先调 `wait_logged_in`（有界等 header 水合）。

新增判据时只改这个文件：改一处，采集和验证同时生效。
"""

from __future__ import annotations

from collections import Counter

from playwright.sync_api import Page

# 未登录态独有的文本（任一可见即未登录）。用 exact=True 匹配，避免命中
# 「注册领券包」「扫码登录」这类营销/入口文案（2026-11-01 截图均可见）。
LOGGED_OUT_TEXTS: tuple[str, ...] = ("注册", "登录")

# 已登录态的语义兜底。
LOGGED_IN_TEXT = "退出"

# 页面已加载的旁证：未登录、已登录都有，所以它单独不能证明什么，
# 只能配合「登录/注册都不可见」一起用，用来排除空白页与风控页。
PAGE_READY_TEXT = "我的订单"

# 头像 / 账户区候选。按稳定性排序：结构语义 > 类名片段。
AVATAR_CANDIDATES: tuple[str, ...] = (
    "img[class*='avatar']",
    "[class*='avatar'] img",
    "[class*='user-info'] img",
    "[class*='head-img']",
    "[class*='userCenter']",
)

# 用户名容器（2026-11-01 实测取证，不是猜的）。
# 登录态首页顶部账号区的真实结构：
#   li.tl_home_header_item_JJ5DU
#     └ div.tl_nfes_home_header_login_wrapper_siwkn
#         └ button.tl_nfes_home_header_login_IUsnp
#             └ div.tl_nfes_home_header_login_title_5neWJ
#                 └ span.tl_nfes_home_header_login_title_name_fDEXu  ← 用户名文本
# 类名带构建哈希后缀（_fDEXu 这类），所以只做前缀匹配，不写死全名。
# 未登录时这个 span 根本不存在，button 里直接是「登录」两个字。
USER_NAME_CANDIDATES: tuple[str, ...] = (
    "span[class*='header_login_title_name']",
    "[class*='header_login_title_name']",
)

# 账号容器里可能出现但不是用户名的文案。命中即认为"还没登录"。
# 用集合而不是单值：站点可能把两者拼成「登录/注册」渲染在同一个节点里。
LOGIN_ENTRY_WORDS: tuple[str, ...] = ("登录", "注册", "立即登录", "请登录")

# passport 登录墙的域特征。K-04 / K-06 两次现场都落在 passport.ctrip.com。
LOGIN_WALL_HOST = "passport.ctrip.com"


def _user_name_text(page: Page) -> str | None:
    """顶部账号容器里的用户名文本；不是登录态就返回 None。

    三重校验（缺任何一条都会误判）：
      · 可见 —— 隐藏的模板节点也带同样 class；
      · 长度 2~20 —— 过滤空文本、纯图标、整块容器文本；
      · 不含「登录/注册」—— 未登录时同一容器里躺的是这两个词。
    """
    for selector in USER_NAME_CANDIDATES:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0 or not locator.is_visible():
                continue
            text = (locator.inner_text() or "").strip()
            if not text or not (2 <= len(text) <= 20):
                continue
            if any(word in text for word in LOGIN_ENTRY_WORDS):
                continue
            return text
        except Exception:
            continue
    return None


def is_logged_in(page: Page) -> tuple[bool, str]:
    """当前页是否已登录，返回 (结果, 证据)。

    证据是给报告和诊断用的原文，不要简化成布尔值 —— 判错时要能一眼看出
    是哪一条判据给的结果。
    """
    try:
        for text in LOGGED_OUT_TEXTS:
            if page.get_by_text(text, exact=True).first.is_visible():
                return False, f"顶部「{text}」仍可见 ⇒ 未登录"
    except Exception:
        pass

    # 第 3 条：用户名容器（实测最可靠的一条正证）。
    # 放在头像之前 —— 头像 img 在这个站点的登录态里其实并不存在，是纯文本用户名。
    name = _user_name_text(page)
    if name:
        return True, f"顶部用户名容器可见：{name!r}"

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

    # 第 4 条：登录入口全消失 + 页面确实加载出来了。
    # 实测依据见文件头 —— 登录态顶部只有纯文本账号名，前三条都抓不到。
    try:
        if page.get_by_text(PAGE_READY_TEXT, exact=False).count() > 0:
            return True, f"「登录/注册」均不可见且「{PAGE_READY_TEXT}」在场 ⇒ 已登录"
    except Exception:
        pass

    return False, "未命中任何登录判据（四条都没验到）"


def looks_like_login_wall(url: str) -> bool:
    """当前 URL 是否在 passport 登录墙域。只用于写报告，不用来改断言。"""
    return LOGIN_WALL_HOST in (url or "")


def wait_logged_in(page: Page, timeout_ms: int = 8000) -> tuple[bool, str]:
    """有界等待登录判据成立后再返回 (结果, 证据)。

    **为什么需要它（2026-11-01 18:0x 实测踩到，不是预防性设计）**：
    www 首页是 SPA —— `domcontentloaded` 时 header 先渲染成「登录 / 注册」，
    随后才被 JS 换成用户名。`is_logged_in` 在这个窗口里做一次性判定，
    会把**已登录**的会话判成未登录。现场：
      `test_re_search_from_result_page_via_left_nav[gz-1n1r2a]` 偶发红，
      失败截图右上角确实是「登录 注册」，而同一 session 里紧挨着的前后两条
      用同一份 state 的用例都通过 —— 所以不是 state 失效，是判定抢跑。

    等待目标选「用户名容器出现」，不选「注册消失」：header 还没渲染时「注册」
    本来就不在，用消失当就绪条件会让等待立刻返回，等于没等。
    """
    budget = max(1, timeout_ms // max(1, len(USER_NAME_CANDIDATES)))
    for selector in USER_NAME_CANDIDATES:
        try:
            page.locator(selector).first.wait_for(state="visible", timeout=budget)
        except Exception:
            continue
        return is_logged_in(page)
    return is_logged_in(page)


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
