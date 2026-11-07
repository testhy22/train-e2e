"""只读探针 50：验证「关日历」不会再改日期（K-14 的修复验证）。

背景：K-14 定位到 `_close_calendar()` 的 `mask.click(force=True)` —— 遮罩铺满整屏、
日历面板居中压在它上面，`force=True` 跳过命中校验 ⇒ 这一点实际点在**面板内部的日期格**，
把刚选好的日期改成别的天（实测 11-14/11-15/11-20 都被改成 11-17）。

本探针专门踩那条分支：**打开日历后不选日期，直接调 `_close_calendar()`**。
  · 旧实现：force 点遮罩中心 → 命中面板 → 日期被改成某一天（值发生变化）；
  · 新实现：先等它自己关（不选日期时一般不会自己关）→ 在遮罩上、面板外找点 → 普通点击。
断言两件事：① 遮罩关掉了；② 出发日期输入框的值**没变**。

不发搜索请求。用法：HEADED=1 python tools/probes/probe_close_calendar.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.train_search_page import TrainSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_close_calendar" / datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            no_viewport=True,
            storage_state=str(settings.AUTH_STATE_FILE),
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            train = TrainSearchPage(page)
            train.open()
            page.wait_for_timeout(1200)

            box = page.locator(train.DATE_INPUT["depart"])
            before = box.input_value().strip()
            facts["before"] = before
            log(f"打开日历时 出发日期 = {before!r}")

            # 打开日历（只打开，不选日期）
            train.human.click(train._date_block("depart"))
            mask = page.locator(train.CALENDAR_MASK).first
            mask.wait_for(state="visible", timeout=settings.EXPECT_TIMEOUT_MS)
            log("日历已打开，遮罩可见")

            # 走被测分支
            train._close_calendar("depart")
            page.wait_for_timeout(300)

            after = box.input_value().strip()
            facts["after"] = after
            facts["mask_visible"] = mask.is_visible()
            facts["date_unchanged"] = after == before
            facts["ok"] = (not facts["mask_visible"]) and facts["date_unchanged"]
            page.screenshot(path=str(OUT / "after_close.png"))
            log(
                f"关日历后 出发日期 = {after!r}｜遮罩仍在={facts['mask_visible']}｜"
                f"日期未被改动={facts['date_unchanged']} -> {'OK' if facts['ok'] else 'FAIL'}"
            )

            # ---- 对照实验：复刻**旧实现**（force 点遮罩中心），扫几个滚动位置 ----
            # 为什么要扫：面板位置随页面滚动变化，遮罩中心压在面板的**哪一块**也跟着变。
            # e2e 轨道里 `human.browse()` 会先滚 1–2 次，所以失败现场的面板位置与
            # 「刚打开首页」不是同一个 —— 只测一个位置会得出「force 无害」的错结论。
            sweep = []
            for step, dy in enumerate((0, 200, 200, 200)):
                if dy:
                    page.mouse.wheel(0, dy)
                    page.wait_for_timeout(300)
                # 上一轮的 force 点击可能把面板留在打开态（它就点不关），先清干净
                if mask.is_visible():
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(250)
                if mask.is_visible():
                    pt = train._point_outside_panel("depart")
                    if pt:
                        page.mouse.click(*pt)
                        page.wait_for_timeout(250)
                train.human.click(train._date_block("depart"))
                try:
                    mask.wait_for(state="visible", timeout=3000)
                except Exception:  # noqa: BLE001 —— 面板没开就跳过这一档
                    sweep.append({"step": step, "error": "日历未打开"})
                    continue
                old_before = box.input_value().strip()
                mb = mask.bounding_box() or {}
                center = [
                    int(mb.get("x", 0) + mb.get("width", 0) / 2),
                    int(mb.get("y", 0) + mb.get("height", 0) / 2),
                ]
                hit = page.evaluate(
                    """([x, y]) => {
                        const el = document.elementFromPoint(x, y);
                        if (!el) return 'null';
                        const li = el.closest('li');
                        const own = el.tagName + '.' + String(el.className || '').slice(0, 26);
                        if (!li) return own;
                        const txt = (li.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 14);
                        return 'li[' + txt + '] ← ' + own;
                    }""",
                    center,
                )
                mask.click(force=True, timeout=5000)
                page.wait_for_timeout(400)
                old_after = box.input_value().strip()
                row = {
                    "step": step,
                    "scroll_y": page.evaluate("() => window.scrollY"),
                    "mask_center": center,
                    "hit_at_center": hit,
                    "before": old_before,
                    "after": old_after,
                    "date_changed": old_after != old_before,
                    "mask_visible": mask.is_visible(),
                }
                sweep.append(row)
                log(
                    f"[对照 {step}] scrollY={row['scroll_y']} 中心={center} 命中 {hit}｜"
                    f"{old_before} → {old_after}｜日期被改动={row['date_changed']}"
                )
            facts["old_impl_sweep"] = sweep
        finally:
            context.close()
            browser.close()

    (OUT / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"落盘：{OUT}")
    return 0 if facts.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
