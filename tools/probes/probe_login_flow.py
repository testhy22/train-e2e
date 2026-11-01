"""只读探测 11：登录页「勾选协议 → 点微信图标」两步之后，站点到底做了什么。

**这不是测试用例，是取证脚本。** 它不填账号、不扫码、不写 state，
只在每一步之后把 DOM + 截图落盘。

为什么需要这一步（2026-11-01 实测发现）：
  直接点 `a.icon-new-wechat`，页面**不会**切到二维码，而是冒出
  「请先阅读并勾选协议」（`div[data-testid="agreementTip"]`）——
  即协议勾选框是一道前置闸。旧脚本没勾就点，于是拿到的是
  「点了没反应」的假结论。

本脚本要回答三个具体问题：
  1. 勾选协议用什么选择器最稳（input 本体 vs label vs 外层 div）；
  2. 勾选之后点微信图标，是**同页切面板**还是**开 popup**；
  3. 二维码出现在哪里（img？跨域 iframe？canvas？）——
     这决定 suites/auth 里该断言什么。

跑法（有头，需联网）：
    python tools/probes/probe_login_flow.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import (  # noqa: E402
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from config import settings  # noqa: E402
from scripts.setup_auth import clean_url, install_dirty_url_guard, probe_page  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_login" / datetime.now().strftime("%Y%m%d-%H%M%S")

# 实测坐实的选择器（来源：reports/probe_auth/agent-run-20261101-1705/07_login_timeout.html）
AGREE_INPUT = "input#checkboxAgreementInput"
AGREE_WRAP = "div[data-testid='checkboxAgreement']"
AGREE_LABEL = "label[for='checkboxAgreementInput']"
AGREE_TIP = "div[data-testid='agreementTip']"
WECHAT_ICON = "a.icon-new-wechat"
SCAN_TAB = "div.login-code a"


def log(msg: str) -> None:
    print(msg, flush=True)


def qr_fingerprint(page: Page) -> dict:
    """二维码可能藏的地方全查一遍：img / iframe / canvas / 含 qr 字样的类名。"""
    return page.evaluate(
        """() => {
          const vis = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
          };
          const imgs = Array.from(document.querySelectorAll('img')).map(el => ({
            src: (el.getAttribute('src') || '').slice(0, 160),
            cls: (el.className || '').toString().slice(0, 80),
            w: Math.round(el.getBoundingClientRect().width),
            h: Math.round(el.getBoundingClientRect().height),
            visible: vis(el),
          }));
          const iframes = Array.from(document.querySelectorAll('iframe')).map(el => ({
            src: (el.getAttribute('src') || '').slice(0, 200),
            id: el.id || '',
            visible: vis(el),
          }));
          const canvases = Array.from(document.querySelectorAll('canvas')).map(el => ({
            cls: (el.className || '').toString().slice(0, 80),
            w: el.width, h: el.height, visible: vis(el),
          }));
          const qrNodes = Array.from(document.querySelectorAll(
              "[class*='qrcode'], [class*='qr-'], [id*='qrcode'], [class*='ercode'], [class*='scan']"
            )).slice(0, 20).map(el => ({
              tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().slice(0, 100),
              id: el.id || '',
              visible: vis(el),
              text: (el.innerText || '').trim().slice(0, 60),
            }));
          const box = document.querySelector('.lg_loginbox');
          return {
            url: location.href,
            loginbox_class: box ? box.className : '(未找到 .lg_loginbox)',
            img_count: imgs.length, imgs,
            iframe_count: iframes.length, iframes,
            canvas_count: canvases.length, canvases,
            qr_nodes: qrNodes,
          };
        }"""
    )


def dump_state(page: Page, tag: str, label: str) -> dict:
    """落盘 + 打印一行摘要。"""
    probe_page(page, OUT, tag)
    fp = qr_fingerprint(page)
    (OUT / f"{tag}__qr.json").write_text(
        __import__("json").dumps(fp, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tips = page.locator(AGREE_TIP)
    tip_visible = tips.count() > 0 and tips.first.is_visible()
    log(
        f"  [{tag}] {label}\n"
        f"      loginbox={fp['loginbox_class']!r}  img={fp['img_count']}  "
        f"iframe={fp['iframe_count']}  canvas={fp['canvas_count']}  "
        f"协议提示={'可见' if tip_visible else '不可见'}"
    )
    for one in fp["imgs"]:
        if one["visible"]:
            log(f"      img 可见: {one['w']}x{one['h']} cls={one['cls']!r} src={one['src'][:90]}")
    for one in fp["iframes"]:
        log(f"      iframe: visible={one['visible']} src={one['src'][:120]}")
    for one in fp["qr_nodes"][:8]:
        log(f"      qr候选: <{one['tag']}> vis={one['visible']} cls={one['cls'][:60]!r} text={one['text'][:30]!r}")
    return fp


def try_check_agreement(page: Page) -> str:
    """三种点法依次试，返回哪种成功。input 本体常被视觉隐藏，所以不只用 check()。"""
    attempts = (
        ("input.check()", lambda: page.locator(AGREE_INPUT).check(timeout=4000)),
        ("input.check(force)", lambda: page.locator(AGREE_INPUT).check(timeout=4000, force=True)),
        ("label.click()", lambda: page.locator(AGREE_LABEL).first.click(timeout=4000)),
        ("wrap.click()", lambda: page.locator(AGREE_WRAP).first.click(timeout=4000)),
    )
    for name, action in attempts:
        try:
            action()
        except Exception as exc:
            log(f"    {name} 失败：{type(exc).__name__}")
            continue
        checked = page.locator(AGREE_INPUT).is_checked()
        log(f"    {name} → is_checked={checked}")
        if checked:
            return name
    return "(全部失败)"


def open_login_page(context: BrowserContext) -> Page:
    page = context.new_page()
    page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    log("[1] 打开 www 首页")
    page.goto(clean_url("https://www.ctrip.com/"), wait_until="domcontentloaded")
    dismiss_promo_overlays(page, tag="首页")
    log("[2] 点顶部「登录」")
    page.get_by_text("登录", exact=True).first.click(timeout=10000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeout:
        pass
    log(f"    URL：{page.url}")
    return page


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("=" * 70)
    log(f"落盘目录：{OUT}")
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
            page = open_login_page(context)
            dump_state(page, "01_login_page", "刚到登录页")

            log("\n[3] 勾选协议（这一步是旧脚本漏掉的闸门）")
            which = try_check_agreement(page)
            page.wait_for_timeout(1200)
            dump_state(page, "02_after_agree", f"勾选协议后（{which}）")

            log("\n[4] 点微信图标 a.icon-new-wechat")
            popup: Page | None = None
            try:
                with context.expect_page(timeout=6000) as info:
                    page.locator(WECHAT_ICON).first.click(timeout=8000)
                popup = info.value
                log(f"    ✓ 捕获 popup：{popup.url}")
                popup.wait_for_load_state("domcontentloaded")
            except PlaywrightTimeout:
                log("    未捕获 popup（按同页处理）")
            except Exception as exc:
                log(f"    点击异常：{type(exc).__name__}: {exc}")

            target = popup or page
            target.wait_for_timeout(4000)
            fp = dump_state(target, "03_after_wechat", "点微信后")

            # 若还是账号密码面板，回到「扫码登录」竖条再试一次
            has_qr = any(i["visible"] and i["w"] > 60 for i in fp["imgs"]) or fp["iframe_count"] > 0
            if not has_qr:
                log("\n[5] 微信图标未产生二维码，改点右侧「扫码登录」竖条")
                try:
                    page.locator(SCAN_TAB).first.click(timeout=6000)
                    page.wait_for_timeout(3500)
                    fp = dump_state(page, "04_after_scan_tab", "点扫码登录竖条后")
                    has_qr = any(
                        i["visible"] and i["w"] > 60 for i in fp["imgs"]
                    ) or fp["iframe_count"] > 0
                except Exception as exc:
                    log(f"    扫码竖条点击失败：{type(exc).__name__}: {exc}")

            log("\n" + "=" * 70)
            log(f"结论：{'✓ 出现疑似二维码区域' if has_qr else '✗ 两条路径都没出现二维码'}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
