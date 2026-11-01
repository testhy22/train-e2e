"""只读探测 18：「允许 / 拒绝」这个确认框，是网页 DOM 还是微信客户端的原生窗口？

**为什么必须查清（2026-11-01 用户截图）**：
    点「微信快捷登录」后出现的确认卡片内容是
        「携程旅行网 申请使用 / 你的昵称、头像 / [头像] Hy*#$@ / 允许 拒绝」
    它**没有 Windows 标题栏**，浮在网页内容中间。
    两种可能，处置方式完全不同：
        A. 网页 DOM（open.weixin.qq.com 自己渲染）→ Playwright 能点，全流程可无人值守
        B. 微信客户端的无边框原生窗口 → Playwright 点不到，必须人点或上 UIA

    此前 `ALLOW_BUTTON_CANDIDATES = ()`（"实测无允许按钮"）是在**二维码页**上得出的，
    不适用于快捷登录之后的状态 —— 这次要单独取证。

同时用 EnumWindows 枚举顶层窗口做旁证：若 B 成立，点击后会多出一个微信类名窗口。

跑法：
    python tools/probes/probe_allow_button.py
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

OUT = settings.REPORTS_DIR / "probe_allow" / datetime.now().strftime("%Y%m%d-%H%M%S")

# 页面上所有含「允许 / 拒绝」的节点，连祖先链一起带出来
_FIND_ALLOW_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const t = (el.innerText || '').trim();
    if (!(t === '允许' || t === '拒绝' || (t.length <= 6 && (t.includes('允许') || t.includes('拒绝'))))) continue;
    const r = el.getBoundingClientRect();
    const path = [];
    let n = el;
    for (let i = 0; i < 5 && n; i++) {
      path.push(n.tagName.toLowerCase() + (n.className ? '.' + String(n.className).split(' ')[0] : ''));
      n = n.parentElement;
    }
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 120),
      role: el.getAttribute('role') || '',
      text: t.slice(0, 30),
      visible: vis(el),
      w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.left), y: Math.round(r.top),
      path: path.join(' < '),
    });
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def enum_windows() -> list[dict]:
    u = ctypes.windll.user32
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
        rows.append({"class": c.value, "title": b.value,
                     "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    u.EnumWindows(cb, 0)
    return rows


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

            log("[1] 走到快捷登录")
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

            # 等快捷登录面板
            appeared = False
            for _ in range(60):
                if popup.locator(WECHAT_FAST_PANEL).count():
                    box = popup.locator(WECHAT_FAST_PANEL).first.bounding_box()
                    if box and box["width"] > 40:
                        appeared = True
                        break
                popup.wait_for_timeout(500)
            log(f"    快捷登录面板：{appeared}")
            if not appeared:
                log("✗ 没等到快捷登录面板，本次无法取证")
                return 0

            before_windows = enum_windows()
            log(f"\n[2] 点击前顶层窗口 {len(before_windows)} 个")

            log("[3] 点「微信快捷登录」")
            popup.locator(WECHAT_FAST_BTN).first.click(timeout=8000)
            log("    已点击，等 4s 让确认框出现")
            popup.wait_for_timeout(4000)
            probe_page(popup, OUT, "01_after_click")

            log("\n[4] 查 popup 内的「允许/拒绝」DOM")
            nodes = []
            try:
                nodes = popup.evaluate(_FIND_ALLOW_JS)
            except Exception as exc:
                log(f"    evaluate 失败：{exc}")
            for n in nodes[:15]:
                log(f"    <{n['tag']}> vis={n['visible']} {n['w']}×{n['h']} at({n['x']},{n['y']}) "
                    f"text={n['text']!r} cls={n['cls'][:50]!r}")
                log(f"        {n['path']}")
            if not nodes:
                log("    （DOM 里没有任何「允许/拒绝」节点）")

            log("\n[5] Playwright 语义定位再确认一次")
            for how, maker in (
                ("text=允许 exact", lambda: popup.get_by_text("允许", exact=True)),
                ("role=button 允许", lambda: popup.get_by_role("button", name="允许")),
                ("button:has-text", lambda: popup.locator("button:has-text('允许')")),
            ):
                try:
                    loc = maker()
                    cnt = loc.count()
                    visible = any(loc.nth(i).is_visible() for i in range(min(cnt, 3)))
                    log(f"    {how}: count={cnt} 可见={visible}")
                except Exception as exc:
                    log(f"    {how}: 异常 {type(exc).__name__}")

            log("\n[6] 顶层窗口对比（若多出微信窗口 ⇒ 是原生框）")
            after = enum_windows()
            before_set = {(w["class"], w["title"]) for w in before_windows}
            new_ones = [w for w in after if (w["class"], w["title"]) not in before_set]
            for w in new_ones:
                log(f"    + [{w['class']}] {w['w']}×{w['h']} title={w['title']!r}")
            if not new_ones:
                log("    （没有新的顶层窗口 ⇒ 确认框不是独立原生窗口）")

            log("\n[7] 若 DOM 里有可见「允许」，点它")
            clicked = False
            try:
                btn = popup.get_by_text("允许", exact=True)
                for i in range(btn.count()):
                    one = btn.nth(i)
                    if one.is_visible():
                        one.click(timeout=6000)
                        clicked = True
                        log("    ✓ 已点击 DOM 中的「允许」")
                        break
            except Exception as exc:
                log(f"    点击失败：{type(exc).__name__}: {exc}")
            if not clicked:
                log("    ✗ DOM 中没有可点的「允许」—— 确认为浏览器外部窗口，需人工或 UIA")

            (OUT / "result.json").write_text(
                json.dumps({"nodes": nodes, "new_windows": new_ones, "clicked": clicked},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if clicked:
                log("\n[8] 观察 40s")
                started = time.time()
                while time.time() - started < 40:
                    if popup.is_closed():
                        log(f"    popup 于 {time.time() - started:.1f}s 关闭")
                        break
                    popup.wait_for_timeout(1000)
                probe_page(page, OUT, "02_home_after_allow")

            log("\n" + "=" * 70)
            log(f"结论：DOM 有允许={clicked}  新顶层窗口={len(new_ones)}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
