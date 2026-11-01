"""只读探测 20：用 UIAutomation 读微信确认框的控件树，并点「允许」。

**已知（探针 19）**：点「微信快捷登录」后 2 秒，出现独立顶层窗口
    `[Qt51514QWindowIcon] 374×267 title='微信'`
它就是「携程旅行网 申请使用 / 你的昵称、头像 / 允许 拒绝」那个确认框。
Qt 自绘，`EnumChildWindows` 返回 0，所以拿不到 Win32 按钮句柄。

本探针回答最后一个问题：**Qt 窗口是否对 UIAutomation 暴露控件树？**
    - 暴露 → 能 `Button(Name='允许').Click()`，全流程可无人值守
    - 不暴露 → 只剩坐标点击或人工，必须如实记录，不做脆弱的猜测

判定标准：找到 ControlType=Button 且 Name 含「允许」的控件，且它的
BoundingRectangle 落在那个 374×267 窗口内。

跑法：
    python tools/probes/probe_uia_allow.py
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

import uiautomation as auto  # noqa: E402
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

OUT = settings.REPORTS_DIR / "probe_uia" / datetime.now().strftime("%Y%m%d-%H%M%S")
u = ctypes.windll.user32


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
                     "w": r.right - r.left, "h": r.bottom - r.top,
                     "x": r.left, "y": r.top})
        return True

    u.EnumWindows(cb, 0)
    return rows


def walk(ctrl, depth: int, max_depth: int, out: list) -> None:
    if depth > max_depth:
        return
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for c in children:
        try:
            rect = c.BoundingRectangle
            out.append({
                "depth": depth,
                "type": c.ControlTypeName,
                "name": c.Name,
                "cls": c.ClassName,
                "x": rect.left, "y": rect.top,
                "w": rect.right - rect.left, "h": rect.bottom - rect.top,
            })
        except Exception:
            continue
        walk(c, depth + 1, max_depth, out)


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

            before = {w["hwnd"] for w in qt_windows()}
            log("点「微信快捷登录」，等确认框")
            popup.locator(WECHAT_FAST_BTN).first.click(timeout=8000)
            popup.wait_for_timeout(3500)
            probe_page(popup, OUT, "01_after_click")

            target = None
            deadline = time.time() + 12
            while time.time() < deadline:
                now = qt_windows()
                new_qt = [w for w in now if w["hwnd"] not in before]
                big = [w for w in new_qt if w["w"] > 200 and w["h"] > 150]
                if big:
                    target = big[0]
                    break
                popup.wait_for_timeout(1000)

            if target is None:
                log("✗ 12s 内没等到新的微信确认窗口")
                return 0
            log(f"✓ 确认窗口：{target}")

            log("\n[UIA] 多轮唤醒 Qt Accessibility 后再读树")
            ctrl = auto.ControlFromHandle(target["hwnd"])
            nodes: list[dict] = []
            if ctrl is None:
                log("    ControlFromHandle 返回 None")
            else:
                # Qt 的 accessibility gate 冷启动是关的：第一次 GetChildren 只是"敲门"，
                # 要给 Qt 一点时间把 mmui 子树物化出来，再读才有内容。
                # 只读一轮就断言"Qt 不暴露 UIA"，是把"还没醒"误判成"不存在"。
                for round_no in range(1, 7):
                    try:
                        kids = ctrl.GetChildren()
                    except Exception as exc:
                        log(f"    第{round_no}轮 GetChildren 异常：{type(exc).__name__}: {exc}")
                        time.sleep(0.6)
                        continue
                    log(f"    第{round_no}轮 GetChildren → {len(kids)} 个子控件")
                    if kids:
                        nodes = []
                        walk(ctrl, 0, 8, nodes)
                        log(f"    控件节点数：{len(nodes)}")
                        break
                    time.sleep(0.6)
                for n in nodes[:40]:
                    log(f"    {'  ' * n['depth']}[{n['type']}] name={n['name']!r} "
                        f"cls={n['cls']!r} {n['w']}×{n['h']} at({n['x']},{n['y']})")

            (OUT / "uia_nodes.json").write_text(
                json.dumps({"window": target, "nodes": nodes}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            log("\n[判定] 找可点的「允许」")
            allow = [n for n in nodes if "允许" in (n["name"] or "")]
            if not allow:
                log("    ✗ UIA 树里没有含「允许」的控件")
                log("      → Qt 窗口未对 UIA 暴露按钮，只能坐标点击或人工")
                return 0

            for n in allow:
                log(f"    候选：[{n['type']}] name={n['name']!r} {n['w']}×{n['h']} at({n['x']},{n['y']})")

            log("\n[点击] 用 UIA 定位后点击")
            clicked = False
            try:
                btn_ctrl = auto.ControlFromPoint(
                    allow[0]["x"] + allow[0]["w"] // 2,
                    allow[0]["y"] + allow[0]["h"] // 2,
                )
                if btn_ctrl is not None:
                    log(f"    点到的控件：[{btn_ctrl.ControlTypeName}] {btn_ctrl.Name!r}")
                target_ctrl = auto.ButtonControl(searchFromControl=ctrl, Name="允许")
                if target_ctrl.Exists(2):
                    target_ctrl.Click()
                    clicked = True
                    log("    ✓ 已 UIA 点击「允许」")
            except Exception as exc:
                log(f"    UIA 点击失败：{type(exc).__name__}: {exc}")

            if not clicked:
                log("    → 回退：按控件矩形中心做一次鼠标点击")
                try:
                    cx = allow[0]["x"] + allow[0]["w"] // 2
                    cy = allow[0]["y"] + allow[0]["h"] // 2
                    u.SetCursorPos(cx, cy)
                    u.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                    u.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                    clicked = True
                    log(f"    ✓ 已点击坐标 ({cx},{cy})")
                except Exception as exc:
                    log(f"    坐标点击失败：{exc}")

            if clicked:
                log("\n[观察] 15s，看 popup 是否关闭")
                started = time.time()
                while time.time() - started < 15:
                    if popup.is_closed():
                        log(f"    popup 于 {time.time() - started:.1f}s 关闭 ⇒ 授权成功")
                        break
                    popup.wait_for_timeout(1000)
                probe_page(popup, OUT, "02_popup_after_allow") if not popup.is_closed() else None
                probe_page(page, OUT, "03_home")

            log("\n" + "=" * 70)
            log(f"结论：UIA 可读={len(nodes) > 0}  找到允许={bool(allow)}  点击={clicked}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
