"""顶部账号区：打开账户菜单、退出登录。

为什么单独建一个页面对象而不是塞进 BasePage：
    BasePage 只放「任何页面都会用到」的四件事（有界等待 / 风控识别 / 失败证据 / 导航）。
    账户菜单是**顶级导航的组件级交互**，只有登录轨道的用例会碰，
    放进基类会让每条用例都背一段永远用不到的 DOM 知识。

关于「退出」的三条实测事实（2026-11-01，tools/probes/probe_logout_menu.py）

    1. **浮层是懒渲染，不是 display:none。** 悬停前扫全文档，与「退出」相关的
       节点数为 **0**，`document.body.innerText` 也不含「退出」二字。
       悬停后才出现。所以顺序只能是「先 hover，再找元素」——
       写成「先等元素出现、再 hover」会稳定超时，然后被误判成「站点没有退出功能」。

    2. **精确节点**：`a.tl_list_item_Kah3M.tl_logout_knRFU`，文本「退出登录」，
       尺寸 212×40，内含 `div.tl_title_sGgZK` 承载文案。类名带构建哈希，
       因此用片段匹配。

    3. **容器三层**：
           div[class*='header_dropdown_panel']
             └ div[class*='header_account_center']
                 └ div[class*='info_list']
                     └ a[class*='logout']   ← 目标
       选择器必须限定在这个子树里。理由不是洁癖：本项目在酒店搜索上
       踩过「全局 exact 文本匹配命中内层 span 与页面底部同名文案」的坑
       （K-01 的第三个根因），退出这种**有副作用**的动作更不能靠巧合命中。

关于「Cookie 清除」（用户方案里的主方案）：见 `clear_auth_cookies` 的说明 ——
它是清理工具，不是退出功能的测试。
"""

from __future__ import annotations

import re

from playwright.sync_api import BrowserContext, Page

from utils.auth import is_logged_in

# 用户区（触发浮层的锚点）。www 与 trains 同构，均由
# tools/probes/probe_header_user.py / probe_trains_header.py 取证。
USER_ZONE_CANDIDATES: tuple[str, ...] = (
    "span[class*='header_login_title_name']",
    "span[class*='header_login']",
)

# 浮层容器。限定范围用，不直接点。
MENU_PANEL = "div[class*='header_dropdown_panel']"

# 退出入口。片段匹配，避开类名哈希。
LOGOUT_ENTRY = "a[class*='logout']"

# 退出后应回到的未登录形态。实测顶部是**两个独立按钮**「登录」「注册」，
# 不是连写的「登录/注册」—— 所以不能用 `get_by_text("登录/注册")` 去等。
LOGGED_OUT_MARK = "注册"


class LogoutUnavailable(RuntimeError):
    """账户菜单打不开或退出入口不可点。属于**已知限制**，不是断言失败。

    单独一个异常类型的原因：调用方要能区分「页面结构变了 / 浮层没展开」
    与「退出真的失败了（点了但仍是登录态）」。前者是我们对站点认知过时，
    转 skip 并留下记录；后者是产品缺陷，必须红。
    """


class HeaderAccount:
    """顶部账号区交互。只做两件事：展开菜单、退出。"""

    def __init__(self, page: Page) -> None:
        self.page = page

    # ------------------------------------------------------------ 展开菜单

    def open_menu(self, timeout_ms: int = 5000) -> None:
        """把鼠标移到用户名上，等浮层出现。

        用 `mouse.move()` 而不是 `locator.hover()`：
        `hover()` 走 actionability 检查 + 单次鼠标事件，实测在「菜单由
        pointerenter 触发、且内部还有一层 transition」的组件上，常出现
        「hover 返回了，但菜单还没画出来就往下走」。`mouse.move` 是物理位移，
        与真人一致，配合下面的轮询等待才稳。

        失败抛 LogoutUnavailable 而不是 TimeoutError —— 决定权交给调用方
        （是 skip 还是 fail），这一层不替它决定。
        """
        zone = None
        for selector in USER_ZONE_CANDIDATES:
            locator = self.page.locator(selector).first
            try:
                if locator.count() and locator.is_visible():
                    zone = locator
                    break
            except Exception:
                continue
        if zone is None:
            raise LogoutUnavailable("找不到可见的用户名区域（顶部账号区结构可能已改版）")

        box = zone.bounding_box()
        if not box:
            raise LogoutUnavailable("用户区没有可点击的边界框（宽度或高度为 0）")
        self.page.mouse.move(
            box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        )

        # 轮询等浮层真正出现。不硬 sleep：菜单是懒渲染（悬停前节点数为 0），
        # 只能靠「目标节点出现」判定，用上限 + 轮询，和等元素出现是同一类等待。
        deadline_ms = timeout_ms
        waited = 0
        while waited < deadline_ms:
            if self.logout_entry().count():
                return
            self.page.wait_for_timeout(200)
            waited += 200
        raise LogoutUnavailable(
            f"悬停 {timeout_ms}ms 后账户浮层仍未出现（{MENU_PANEL} 里没有 {LOGOUT_ENTRY}）"
        )

    def logout_entry(self):
        """退出入口，**限定在浮层子树内**。

        不写成 `page.get_by_text("退出登录", exact=True).first`：
        实测 DOM 里同时存在 `a.tl_logout_knRFU` 与内层 `div.tl_title_sGgZK`
        两个节点都带「退出登录」文本，全局取第一个是碰运气。
        限定容器后语义明确：我们点的是「浮层里的退出链接」。
        """
        return self.page.locator(f"{MENU_PANEL} {LOGOUT_ENTRY}")

    # ------------------------------------------------------------ 退出

    def logout(self, settle_timeout_ms: int = 15000) -> str:
        """点「退出登录」，等页面回到未登录态。返回判定证据。

        不做「点完立刻断言」：退出会触发一次跳转/重绘，顶部区域要重新渲染。
        用 is_logged_in 轮询，判据与登录态校验**同一份代码**，
        避免出现「登录判定说已登录、退出判定说未登录」这种自相矛盾。
        """
        self.open_menu()
        entry = self.logout_entry().first
        before_url = self.page.url

        # 点之前先记下「点得到」这件事：浮层遮挡时 click 会抛「被拦截」，
        # 那属于结构问题（转 LogoutUnavailable），不是退出失败。
        try:
            entry.click(timeout=5000)
        except Exception as exc:
            raise LogoutUnavailable(f"退出入口点不动（可能被浮层遮挡）：{type(exc).__name__}: {exc}") from exc

        waited = 0
        evidence = ""
        while waited < settle_timeout_ms:
            logged, evidence = is_logged_in(self.page)
            if not logged:
                return f"url {before_url} → {self.page.url}；判定：{evidence}"
            self.page.wait_for_timeout(500)
            waited += 500

        raise AssertionError(
            f"点了「退出登录」但 {settle_timeout_ms}ms 后仍是登录态（{evidence}）；"
            f"当前 url={self.page.url}"
        )


def clear_auth_cookies(context: BrowserContext, page: Page | None = None) -> int:
    """清掉 context 里的携程 cookie，返回清掉的条数。

    **这是清理工具，不是「退出功能」的测试。** 说清楚原因，免得被当成主方案用：

        清 cookie 之后页面显示未登录，是**必然结果** —— 我们自己把凭据扔了，
        浏览器当然不再带着它请求。这条路径永远绿，它检验的是
        Playwright 的 clear_cookies 好不好使，而不是携程的退出接口有没有
        把服务端会话注销掉。把它写成主断言，等于把「没测」记成「通过」。

    它的正确位置有两个：
        1. 用例收尾清理，避免同一 context 里的后续用例被登录态污染；
        2. 作为对照——「UI 退出后 cookie 里到底还有没有 cticket」，
           这个问题只有清空前后对比才能回答。

    只清 ctrip 域的 cookie：百度统计之类与登录态无关，一起清会让
    「退出后页面是否正常」这种观察失真。
    """
    removed = 0
    for cookie in context.cookies():
        if "ctrip" not in (cookie.get("domain") or ""):
            continue
        try:
            context.clear_cookies(name=cookie["name"], domain=cookie["domain"])
            removed += 1
        except Exception:
            # 老版本 Playwright 的 clear_cookies 不接受过滤参数，退化为全清
            context.clear_cookies()
            removed = len(context.cookies())
            break

    if page is not None:
        with_page = page
        try:
            with_page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
    return removed


def session_cookie_names(context: BrowserContext) -> list[str]:
    """ctrip 域 cookie 里名字带登录语义的那些。取证用，不做断言依据。"""
    pattern = re.compile(r"ticket|login|user|member|uid", re.IGNORECASE)
    return sorted(
        {
            c["name"]
            for c in context.cookies()
            if "ctrip" in (c.get("domain") or "") and pattern.search(c["name"] or "")
        }
    )
