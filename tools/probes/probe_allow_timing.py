"""只读探测 19：点「微信快捷登录」后，微信确认框到底什么时候、以什么形式出现。

探针 18 的结论有缺口：点击后只等 4s 就枚举窗口，可能弹框还没出来。
本探针把观察窗拉到 16 秒，每 2 秒枚举一次顶层窗口 + 微信进程的所有可见子窗口，
回答三件事：
    1. 有没有**新的顶层窗口**出现（有 ⇒ 独立窗口，可用窗口消息/UIA 操作）；
    2. 微信主窗口（Qt51514QWindowIcon）尺寸/标题是否变化（Qt 弹层常是主窗口内绘制）；
    3. 时间轴：点击后第几秒出现。

跑法：
    python tools/probes/probe_allow_timing.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import (  # noqa: E402
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from config import settings  # noqa: E402
from scripts.setup_auth import (  # noqa: E402
    AGREE_INPUT,
    WECHAT_ENTRY_CANDIDATES,
    WECHAT_FAST_BTN,
    WECHAT_FAST_PANEL,
    clean_url,
    probe_page,
)
from utils.overlay import dismiss_promo_overlays  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_allow_timing" / datetime.now().strftime("%Y%m%d-%H%M%S")

u = ctypes.windll.user32


def enum_top() -> list[dict]:
    rows: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rows.append({"hwnd": int(h), "class": c.value, "title": b.value,
                     "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    u.EnumWindows(cb, 0)
    return rows


def enum_children(parent: int) -> list[dict]:
    rows: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rows.append({"hwnd": int(h), "class": c.value, "title": b.value,
                     "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    u.EnumChildWindows(parent, cb, 0)
    return rows


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("=" * 70)
    log(f"落盘：{OUT}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=["local-network-access"],
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

            page.goto(clean_url("https://www.ctrip.com/"), wait_until="domcontentloaded")
            dismiss_promo_overlays(page, tag="首页")
            page.get_by_text("登录", exact=True).first.click(timeout=10000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeout:
                pass
            page.locator(AGREE_INPUT).check(timeout=4000, force=True)

            popup = None
            for selector in WECHAT_ENTRY_CANDIDATES:
                try:
                    loc = page.locator(selector).first
                    if loc.count() == 0 or not loc.is_visible():
                        continue
                    with context.expect_page(timeout=12000) as info:
                        loc.click(timeout=8000)
                    popup = info.value
                    break
                except PlaywrightTimeout:
                    continue
                except Exception:
                    continue
            if popup is None:
                log("✗ 没拿到 popup")
                return 0

            for _ in range(60):
                if popup.locator(WECHAT_FAST_PANEL).count():
                    box = popup.locator(WECHAT_FAST_PANEL).first.bounding_box()
                    if box and box["width"] > 40:
                        break
                popup.wait_for_timeout(500)

            wechat_hwnds = [w["hwnd"] for w in enum_top() if w["class"].startswith("Qt")]
            log(f"微信类窗口：{[w for w in enum_top() if w['class'].startswith('Qt')]}")
            log(f"微信子窗口数（点击前）：{sum(len(enum_children(h)) for h in wechat_hwnds)}")

            before = {(w["class"], w["title"], w["w"], w["h"]) for w in enum_top()}
            log("\n点击「微信快捷登录」，随后每 2s 采样一次，共 16s")
            popup.locator(WECHAT_FAST_BTN).first.click(timeout=8000)

            timeline: list[dict] = []
            for tick in range(1, 9):
                popup.wait_for_timeout(2000)
                tops = enum_top()
                now = {(w["class"], w["title"], w["w"], w["h"]) for w in tops}
                added = [w for w in tops
                         if (w["class"], w["title"], w["w"], w["h"]) not in before]
                child_count = sum(len(enum_children(h)) for h in wechat_hwnds
                                  if u.IsWindow(h))
                timeline.append({"t": tick * 2, "new_top": added,
                                 "wechat_children": child_count})
                log(f"  t={tick * 2}s  新增顶层窗口={len(added)}  微信子窗口={child_count}")
                for w in added:
                    log(f"      + [{w['class']}] {w['w']}×{w['h']} title={w['title']!r}")

            probe_page(popup, OUT, "popup_after_16s")
            (OUT / "timeline.json").write_text(
                json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            log("\n" + "=" * 70)
            total_new = sum(len(t["new_top"]) for t in timeline)
            log(f"结论：16s 内新增顶层窗口累计 {total_new} 个")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
