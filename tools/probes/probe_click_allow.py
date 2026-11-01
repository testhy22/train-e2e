"""只读探测 21：靠像素定位微信确认框里的绿色「允许」按钮并点击。

**为什么走到这一步**：确认框是微信客户端的 Qt 顶层窗口（`Qt51514QWindowIcon`,
468×334），`EnumChildWindows` 返回 0，UIA 连续 6 轮 `GetChildren()` 也全是 0 ——
这个版本的微信没有对 UIA 暴露控件树。剩下唯一可行的是**精确坐标点击**。

不做"按比例估坐标"（换个 DPI/字体就点偏，可能点到「拒绝」）：
    - 用 PIL 截该窗口的图；
    - 按微信品牌绿 (#07C160) 找像素簇；
    - 窗口内只有「允许」是绿的，所以簇中心就是按钮中心；
    - 打印簇的包围盒，确认是单簇、大小合理，再点。

本脚本会真的点击一次「允许」（用户 2026-11-01 明确要求）。点错了最坏结果是
授权被拒、需要重来一次，不会产生其他副作用。

跑法：
    python tools/probes/probe_click_allow.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在任何截图/取窗口坐标之前声明 DPI 感知，否则 GetWindowRect 与 ImageGrab
# 会分别在"逻辑像素/物理像素"两套坐标系里，坐标整体偏移（高分屏下尤其明显）。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PIL import ImageGrab  # noqa: E402
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

OUT = settings.REPORTS_DIR / "probe_click_allow" / datetime.now().strftime("%Y%m%d-%H%M%S")
u = ctypes.windll.user32

WECHAT_GREEN = (7, 193, 96)  # #07C160


def log(msg: str) -> None:
    print(msg, flush=True)


def qt_windows() -> list[dict]:
    rows: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if not c.value.startswith("Qt"):
            return True
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rows.append({"hwnd": int(h), "class": c.value,
                     "x": r.left, "y": r.top,
                     "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    u.EnumWindows(cb, 0)
    return rows


def green_bbox(img) -> tuple[tuple[int, int, int, int] | None, int]:
    """返回绿色像素的包围盒与数量。窗口内只有「允许」是绿的。"""
    w, h = img.size
    px = img.load()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            try:
                r, g, b = px[x, y][:3]
            except Exception:
                continue
            if g > 140 and r < 110 and b < 140 and (g - r) > 60 and (g - b) > 40:
                xs.append(x)
                ys.append(y)
    if len(xs) < 30:
        return None, len(xs)
    return (min(xs), min(ys), max(xs), max(ys)), len(xs)


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

            for _ in range(60):
                if popup.locator(WECHAT_FAST_PANEL).count():
                    box = popup.locator(WECHAT_FAST_PANEL).first.bounding_box()
                    if box and box["width"] > 40:
                        break
                popup.wait_for_timeout(500)

            before = {w["hwnd"] for w in qt_windows()}
            log("[2] 点「微信快捷登录」")
            popup.locator(WECHAT_FAST_BTN).first.click(timeout=8000)

            log("[3] 等微信确认窗口（最多 15s）")
            target = None
            deadline = time.time() + 15
            while time.time() < deadline:
                new_qt = [w for w in qt_windows()
                          if w["hwnd"] not in before and w["w"] > 250 and w["h"] > 150]
                if new_qt:
                    target = new_qt[0]
                    break
                popup.wait_for_timeout(800)
            if target is None:
                log("✗ 没等到确认窗口")
                return 0
            log(f"    窗口：{target}")

            popup.wait_for_timeout(800)  # 让窗口把内容画完再截

            log("[4] 截图并找绿色按钮")
            bbox = (target["x"], target["y"], target["x"] + target["w"], target["y"] + target["h"])
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            shot = OUT / "01_confirm_window.png"
            img.save(shot)
            log(f"    截图：{shot}  size={img.size}")

            box, count = green_bbox(img)
            log(f"    绿色像素：{count} 个，包围盒={box}")
            if box is None:
                log("    ✗ 没找到足够绿色像素 —— 可能窗口还没画完，先看图")
                return 0

            bx0, by0, bx1, by1 = box
            bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
            log(f"    绿块尺寸：{bw}×{bh}")
            # 合理性检查：按钮应是一块扁长条，太小说明是噪点/图标，太大说明混了别的东西
            if not (60 <= bw <= target["w"] and 15 <= bh <= 90):
                log(f"    ⚠️ 绿块尺寸不像按钮（期望宽 60+、高 15~90），先看图再决定")

            cx = target["x"] + (bx0 + bx1) // 2
            cy = target["y"] + (by0 + by1) // 2
            log(f"[5] 目标点：({cx},{cy})  窗口内相对：({(bx0 + bx1) // 2},{(by0 + by1) // 2})")

            log("[6] 置前并点击")
            try:
                u.SetForegroundWindow(target["hwnd"])
            except Exception as exc:
                log(f"    置前失败（不致命）：{exc}")
            time.sleep(0.4)
            u.SetCursorPos(cx, cy)
            time.sleep(0.15)
            u.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(0.08)
            u.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            log("    已点击")

            log("[7] 观察 20s：popup 是否关闭 / 页面是否登录")
            started = time.time()
            closed_at = None
            while time.time() - started < 20:
                if popup.is_closed():
                    closed_at = time.time() - started
                    log(f"    ✓ popup 于 {closed_at:.1f}s 关闭")
                    break
                popup.wait_for_timeout(1000)
            if closed_at is None:
                log("    popup 仍开着，再截一张确认框看状态")
                try:
                    ImageGrab.grab(bbox=bbox, all_screens=True).save(OUT / "02_after_click.png")
                except Exception:
                    pass

            # 主页面复核
            try:
                page.goto(clean_url("https://www.ctrip.com/"), wait_until="domcontentloaded")
                dismiss_promo_overlays(page, tag="复核")
            except Exception as exc:
                log(f"    复核导航失败：{exc}")
            probe_page(page, OUT, "03_home_after")

            from utils.auth import is_logged_in
            ok, ev = is_logged_in(page)
            log(f"    主页面登录判定：{ok} / {ev}")

            (OUT / "result.json").write_text(
                json.dumps({"window": target, "green_count": count, "green_box": box,
                            "click": [cx, cy], "popup_closed_at": closed_at,
                            "logged_in": ok, "evidence": ev},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            log("\n" + "=" * 70)
            log(f"结论：点击=({cx},{cy})  popup关闭={closed_at}  主页面登录={ok}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
