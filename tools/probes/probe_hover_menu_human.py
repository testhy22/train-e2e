"""只读探针：HUMAN=1 下多步移动悬停仍能触发账户菜单（2026-11-02，B 档回归）。

背景：`HeaderAccount.open_menu` 从单步 `mouse.move` 改为 `human.move_to_xy`
（8–16 步位移）。懒渲染菜单靠 pointerenter 触发 —— 本探针验证多步位移**仍能
触发**浮层、退出入口节点真的出现。

**不点退出**：真退出会作废本机登录态（AUTH-04，实测重放同一份 state 判未登录），
本探针只悬停 + 读节点数，然后把鼠标移开。

跑法：`HEADED=1 HUMAN=1 python tools/probes/probe_hover_menu_human.py`
退出码：0 = 菜单触发且有退出节点；1 = 悬停后无节点（回归失败）；2 = 缺登录态。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright

from config import settings
from pages.header_account import HeaderAccount


def main() -> int:
    state = settings.auth_state_path("www")
    if not state.exists():
        print(f"SKIP: 登录态不存在 {state}")
        return 2
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=str(state))
        page = context.new_page()
        page.goto(settings.hotel_base_url(), wait_until="domcontentloaded")
        page.wait_for_load_state("load", timeout=settings.NAV_TIMEOUT_MS)
        page.wait_for_timeout(3000)  # header JS 水合（K-08 同源问题：一次性判定会误判）

        acct = HeaderAccount(page)
        acct.open_menu()
        count = acct.logout_entry().count()
        print(f"MENU_TRIGGERED logout_entry_nodes={count}")

        # 只读收尾：移开鼠标让浮层收起，绝不点退出（AUTH-04）。
        page.mouse.move(5, 400)
        page.wait_for_timeout(800)
        browser.close()
        return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
