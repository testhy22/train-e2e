"""火车票站登录态轨道：验证 ctrip_state_trains.json 能让 trains.ctrip.com 进登录态。

为什么单独一条轨道（2026-11-01 实测）：
    www 站登录后，用**同一份 state** 打开 trains.ctrip.com，顶部仍是「登录 / 注册」
    → 判定未登录。两站登录态不互通，所以各自采集、各自验证：

        入口 www    → config/auth/ctrip_state.json
        入口 trains → config/auth/ctrip_state_trains.json

    共用一份文件还有个更隐蔽的代价：`setup_auth.py --entry trains` 会**覆盖**
    www 那份，而覆盖不会报错，只会在下次跑 www 登录用例时莫名判「未登录」。

为什么无头下不能直接断言（K-05）：
    trains 站无头会被 WAF 拦，整页只剩一行 `whaleguard block`，body 约 17 字符。
    此时 `is_logged_in` 返回「未命中任何判据」—— 这个结论和「state 失效」
    长得一模一样，直接断言会把它记成代码缺陷。
    所以统一走 `blocked_guard()`：`open_and_check` 识别到拦截信号抛
    BlockedByAntiBot，guard 转成 `xfail("blocked: ...")` 计入 blocked，
    而不是 fail。这是「对方拒绝访问」，属于外部事实，不该算进代码质量。
"""

from __future__ import annotations

import json

import pytest

from utils.auth import is_logged_in, looks_like_login_wall, state_summary

pytestmark = [pytest.mark.auth]


def test_train_state_file_is_usable(auth_state_trains, request):
    """结构检查：有 cookie，且至少一条属于 trains 域。

    这条不启浏览器，放在最前面。理由与 www 轨道同：文件都读不出来时，
    后面的浏览器用例失败会像站点问题，实际是我们的准备物坏了。

    断言 trains 域而不是只看「有没有 ctrip 域」：
    www 那份 state 同样含 `.ctrip.com` 的父域 cookie，用父域判断会让
    「拿错文件」也通过 —— 那正好是要在这里拦住的一类错误。
    """
    data = json.loads(auth_state_trains.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    summary = state_summary(data)
    request.node.add_report_section("call", "state 快照（trains）", summary)

    assert cookies, f"state 文件里没有 cookie，这份登录态不可用：\n{summary}"

    trains_cookies = [c for c in cookies if "trains" in (c.get("domain") or "")]
    assert trains_cookies, (
        "state 文件里没有任何 trains 域 cookie —— 这多半是 www 那份被复制过来的，"
        f"或者保存时页面已经掉了登录：\n{summary}"
    )


def test_train_homepage_shows_logged_in_account(auth_trains_base_page, request, blocked_guard):
    """加载 trains 的 state 后访问火车票首页，断言已进入登录态。

    无头模式会在此处被 WAF 拦，由 blocked_guard 转 xfail（K-05）；
    有头模式（HEADED=1）才是这条用例的有效执行形态。
    """
    with blocked_guard():
        auth_trains_base_page.open_and_check("/")

    page = auth_trains_base_page.page
    logged, evidence = is_logged_in(page)
    shots = auth_trains_base_page.capture("auth__train_homepage_logged_in")

    request.node.add_report_section(
        "call",
        "登录态证据（trains）",
        f"url：{page.url}\n"
        f"title：{page.title()}\n"
        f"判定：{evidence}\n"
        f"是否落在登录墙：{looks_like_login_wall(page.url)}\n"
        f"营销浮层：{auth_trains_base_page.overlay_log or '未发现'}\n"
        f"截图：{', '.join(str(p) for p in shots) or '(截图失败)'}",
    )

    # 先排除落登录墙：落在墙上时「注册」也可能不可见，
    # 只判 logged 会得出「未命中任何判据」这种含糊结论。
    assert not looks_like_login_wall(page.url), (
        f"落在 passport 登录墙，说明 state 已失效或未被加载：{page.url}"
    )
    assert logged, (
        f"未检测到已登录态（{evidence}）。"
        "先确认是不是拿错了文件（www 那份在 trains 站不生效），"
        "再检查这份 state 是不是超过一周没重扫。"
    )
