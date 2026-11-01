"""风控（WAF）判定的唯一入口。

**为什么单独一个模块，而不是留在 conftest 里**（2026-11-01 实测踩到）：

    用例原本写 `from conftest import check_waf_block`。只跑一个测试根时没问题；
    一旦把 `tools/tests` 与 `suites/...` 放在同一次运行里收集，`conftest` 这个名字
    会被**先被导入的那个** conftest 占据 —— 实测报错：
        ImportError: cannot import name 'check_waf_block' from 'conftest'
        (...\\tools\\tests\\conftest.py)
    也就是说，import 解析取决于收集顺序，这是隐性的顺序耦合。
    把判据搬到 `utils/` 之后，import 目标唯一，与收集顺序无关。

判据本身**不在这里重写**：仍然复用 `BasePage.detect_block()`。两套判据的后果很具体：
守卫说「是 WAF」、用例说「断言失败」，然后没人知道该信哪个。
"""

from __future__ import annotations

from playwright.sync_api import Page

from pages.base_page import BasePage, BlockSignal


def check_waf_block(page: Page) -> BlockSignal | None:
    """命中风控返回信号，未命中返回 None。

    与「登录墙」不是一回事，别混：
      · `passport.ctrip.com` = 要求登录，属业务门槛（`looks_like_login_wall`）；
      · 风控域 / 滑块 / `whaleguard` = 对方拒绝访问，属 blocked（本函数 + BLOCK_HOST_PATTERNS）。
    """
    return BasePage(page).detect_block()
