"""冒烟探测：只回答一个问题 —— 目标站点今天能不能用浏览器打开。

不填表、不点击、不绕过任何拦截。被拦就 xfail 并留下证据。
这是整个工程的第一道体检：它红了，功能用例的结果没有解释价值。
"""

from __future__ import annotations

import pytest

from pages.base_page import BasePage

pytestmark = pytest.mark.smoke


def test_homepage_reachable(base_page: BasePage, blocked_guard, request: pytest.FixtureRequest):
    """打开首页 → 体检 → 记录 title / URL / 正文长度。"""
    with blocked_guard():
        base_page.goto("/")
        base_page.assert_not_blocked()

        page = base_page.page
        body_len = len(base_page.body_text(timeout=3000))
        request.node.add_report_section(
            "call",
            "冒烟信息",
            f"url：{page.url}\ntitle：{page.title()}\n正文长度：{body_len} 字符",
        )

    # 打开成功但页面是空的，说明拿到了壳没拿到内容，这属于失败而不是通过。
    assert body_len > 200, f"页面正文只有 {body_len} 字符，疑似空壳或未渲染完成"
