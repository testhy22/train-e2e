"""只读探测 12：PC 微信已登录时，授权页的「微信快捷登录」按钮在哪、点了会怎样。

**背景（2026-11-01 用户截图 + 实测）**：
    本机 PC 微信处于登录状态时，`open.weixin.qq.com/connect/qrconnect` 打开后
    **不是二维码**，而是「使用微信快捷登录」+ 用户头像 + 绿色按钮「微信快捷登录」。
    探针 11 里那个 `div.js_web_qrcode_tips_fast`（text=使用微信快捷登录）当时
    `visible=False` —— 说明它是**异步出现的**，等 4 秒不够。

本脚本要回答四个问题：
    1. 「微信快捷登录」按钮的真实选择器（tag / class / 文本层级）；
    2. 它从 popup 打开到可见要多久（决定脚本该等多少秒）；
    3. 点它之后 popup 会不会自己关、主页面怎么变成已登录；
    4. 若等不到它，页面上还剩什么（二维码？还是白屏）。

**它不点「登录」之外任何东西，不填账号，不保存 state。**

跑法：
    python tools/probes/probe_login_fast.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import (  # noqa: E402
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from config import settings  # noqa: E402
from scripts.setup_auth import (  # noqa: E402
    AGREE_INPUT,
    WECHAT_AUTH_HOST,
    WECHAT_ENTRY_CANDIDATES,
    clean_url,
    install_dirty_url_guard,
    probe_page,
)
from utils.auth import is_logged_in  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_login_fast" / datetime.now().strftime("%Y%m%d-%H%M%S")
FAST_TEXT = "微信快捷登录"

# 全量扫「快捷登录 / fast」相关节点。限定 children<=3，避免把整页父容器都收进来。
_COLLECT_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const text = (el.innerText || '').trim();
    const cls = (el.className || '').toString();
    const id = el.id || '';
    if (!(text.includes('快捷登录') || /fast/i.test(cls) || /fast/i.test(id))) continue;
    if (el.children.length > 3) continue;
    const r = el.getBoundingClientRect();
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: cls.slice(0, 120),
      id,
      text: text.slice(0, 80),
      visible: vis(el),
      w: Math.round(r.width),
      h: Math.round(r.height),
      tagPath: (() => {
        const p = [];
        let n = el;
        for (let i = 0; i < 4 && n; i++) {
          p.push(n.tagName.toLowerCase() + (n.className ? '.' + String(n.className).split(' ')[0] : ''));
          n = n.parentElement;
        }
        return p.join(' < ');
      })(),
    });
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def poll_fast_login(page: Page, timeout_s: int = 30) -> tuple[str | None, float, dict]:
    """轮询等「微信快捷登录」出现，返回 (选择器, 等待秒数, 候选快照)。"""
    started = time.time()
    snapshot: dict = {}
    while time.time() - started < timeout_s:
        elapsed = time.time() - started
        try:
            snapshot = {"t": round(elapsed, 1), "nodes": page.evaluate(_COLLECT_JS)}
        except Exception as exc:
            snapshot = {"t": round(elapsed, 1), "error": str(exc)}

        # 优先用 Playwright 语义定位：按文本精确匹配，且必须可见
        try:
            by_text = page.get_by_text(FAST_TEXT, exact=True)
            for i in range(by_text.count()):
                one = by_text.nth(i)
                if one.is_visible():
                    return f"text={FAST_TEXT} (第{i}个)", elapsed, snapshot
        except Exception:
            pass

        page.wait_for_timeout(1000)
    return None, time.time() - started, snapshot


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("=" * 70)
    log(f"落盘：{OUT}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
        )
        install_dirty_url_guard(context)
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

            log("[1] 打开 www 并点「登录」")
            page.goto(clean_url("https://www.ctrip.com/"), wait_until="domcontentloaded")
            dismiss_promo_overlays(page, tag="首页")
            page.get_by_text("登录", exact=True).first.click(timeout=10000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeout:
                pass
            log(f"    {page.url[:100]}")

            log("[2] 勾选协议")
            try:
                page.locator(AGREE_INPUT).check(timeout=4000, force=True)
                log(f"    is_checked={page.locator(AGREE_INPUT).is_checked()}")
            except Exception as exc:
                log(f"    ⚠️ 勾选失败：{type(exc).__name__}: {exc}")

            log("[3] 点微信入口，捕获 popup")
            popup = None
            for selector in WECHAT_ENTRY_CANDIDATES:
                try:
                    loc = page.locator(selector).first
                    if loc.count() == 0 or not loc.is_visible():
                        continue
                    with context.expect_page(timeout=12000) as info:
                        loc.click(timeout=8000)
                    popup = info.value
                    log(f"    ✓ {selector} → {popup.url[:100]}")
                    break
                except PlaywrightTimeout:
                    log(f"    {selector} 未捕获 popup")
                    continue
                except Exception as exc:
                    log(f"    {selector} 异常：{type(exc).__name__}")
                    continue

            if popup is None:
                log("✗ 没拿到 popup，落盘 context")
                for i, one in enumerate(context.pages):
                    probe_page(one, OUT / f"no_popup__page{i}", "page")
                return 0

            log(f"    授权域命中：{WECHAT_AUTH_HOST in popup.url}")
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeout:
                pass
            probe_page(popup, OUT, "01_popup_initial")

            log(f"\n[4] 轮询等「{FAST_TEXT}」出现（上限 30s）")
            selector, waited, snap = poll_fast_login(popup, 30)
            (OUT / "02_fast_login_poll.json").write_text(
                json.dumps({"selector": selector, "waited_s": round(waited, 1), "snapshot": snap},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            for node in (snap.get("nodes") or [])[:15]:
                log(
                    f"    t={snap.get('t')}s <{node['tag']}> vis={node['visible']} "
                    f"{node['w']}×{node['h']} cls={node['cls'][:50]!r} text={node['text'][:24]!r}"
                )
                log(f"        路径: {node['tagPath']}")
            probe_page(popup, OUT, "03_popup_after_wait")

            if selector is None:
                log(f"✗ 30s 内未见「{FAST_TEXT}」—— 可能本机微信未登录，只有二维码")
                probe_page(popup, OUT, "04_no_fast_login")
                return 0

            log(f"✓ 命中：{selector}（等待 {waited:.1f}s）")

            log("\n[5] 点「微信快捷登录」")
            clicked = False
            try:
                popup.get_by_text(FAST_TEXT, exact=True).first.click(timeout=8000)
                clicked = True
                log("    ✓ 已点击")
            except Exception as exc:
                log(f"    ✗ 点击失败：{type(exc).__name__}: {exc}")
            probe_page(popup, OUT, "05_after_click")

            log("\n[6] 观察 40s：popup 是否关闭 / 主页面是否登录")
            started = time.time()
            closed_at: float | None = None
            logged_at: float | None = None
            evidence = ""
            while time.time() - started < 40:
                now = time.time() - started
                try:
                    if closed_at is None and popup.is_closed():
                        closed_at = now
                        log(f"    popup 于 {now:.1f}s 关闭")
                except Exception:
                    pass
                ok, ev = is_logged_in(page)
                evidence = ev
                if ok and logged_at is None:
                    logged_at = now
                    log(f"    主页面判定已登录（{now:.1f}s）：{ev}")
                    break
                page.wait_for_timeout(1000)

            if logged_at is None:
                log(f"    40s 内主页面仍未判为登录，最后判据：{evidence}")
                try:
                    page.goto(clean_url("https://www.ctrip.com/"), wait_until="domcontentloaded")
                    dismiss_promo_overlays(page, tag="复核")
                    ok, ev = is_logged_in(page)
                    log(f"    回 www 复核：{ok} / {ev}")
                    logged_at = 0.0 if ok else None
                except Exception as exc:
                    log(f"    复核失败：{exc}")

            probe_page(page, OUT, "06_home_after_flow")

            log("\n" + "=" * 70)
            log(f"结论：点击成功={clicked}  popup关闭={closed_at}  主页面登录={logged_at is not None}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
