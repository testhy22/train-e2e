"""只读探测 31：酒店「房间及住客」弹层的计数结构（共享会话崩出来的真问题）。

**为什么现在探它**：e2e 串联轨道第一轮就红了，红得很有价值 ——

    第 1 条（广州 1间2成人）通过
    第 2 条（北京 2间3成人）提交参数变成 adult=4
    第 3 条（上海 1间1成人）提交参数变成 crn=2 adult=4（完整继承上一条）

根因不是选择器错，是 `HotelSearchPage.set_rooms()` 的既有前提被打破了：
它按「页面默认 1 间 1 位」写死「只加不减」（`delta = target - 1`，见该方法的
注释与 `ValueError("set_rooms 只支持增加")`）。单条用例一个干净 context 时这个前提成立；
共享会话连续搜索时，站点把上一次的房间住客**留在表单里**，于是从 2间3位 再加 1 位
就得到 4 位。

要修成「按当前值增减」，就得先知道两件事，所以本探针：
  1. 弹层里当前值显示在哪个节点（tag/class/文本形态）
  2. 减号按钮的 selector 与「减到下限」时的表现（是否置灰/是否还能点）

跑法：
    python tools/probes/probe_hotel_room_popup.py
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
from pages.hotel_search_page import HotelSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_hotel_rooms" / datetime.now().strftime("%Y%m%d-%H%M%S")

_POPUP_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('div, span, input, button, li')) {
    const cls = (el.className || '').toString();
    if (!/room|guest|person|count|num|plus|minus|stepper|add|reduce/i.test(cls)) continue;
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: cls.slice(0, 110),
      text: (el.innerText || el.value || '').replace(/\s+/g, ' ').slice(0, 40),
      value: el.value !== undefined ? el.value : '',
      disabled: el.disabled === true,
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    log("=" * 70)
    log(f"落盘：{OUT}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state) if state.exists() else None,
            permissions=[],
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            hotel = HotelSearchPage(page)
            hotel.open()
            log(f"[0] 首页摘要：{hotel.form_text()[:160]}")

            # 点开弹层
            hotel.human.click(hotel.form.locator(hotel.ROOM_GUEST).first)
            page.wait_for_timeout(800)
            nodes = page.evaluate(_POPUP_JS)
            log(f"[1] 弹层相关节点 {len(nodes)} 个：")
            for n in nodes[:30]:
                log(f"    <{n['tag']}> {n['w']}×{n['h']} at({n['x']},{n['y']}) "
                    f"text={n['text']!r} value={n['value']!r} disabled={n['disabled']}")
                log(f"        cls={n['cls']!r}")
            (OUT / "popup_nodes.json").write_text(
                json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.screenshot(path=str(OUT / "01_popup.png"))

            # 点一次「成人」加号，看哪个节点数字变了
            plus = page.locator(hotel.PLUS)
            log(f"[2] plus 数量={plus.count()}")
            before = page.evaluate(_POPUP_JS)
            hotel.human.click(plus.nth(1))
            page.wait_for_timeout(600)
            after = page.evaluate(_POPUP_JS)
            changed = []
            for b, a in zip(before, after):
                if b["text"] != a["text"] or b["value"] != a["value"]:
                    changed.append({"cls": b["cls"], "before": b["text"] or b["value"],
                                    "after": a["text"] or a["value"]})
            log(f"[3] 点「成人 +」后变化的节点 {len(changed)} 个：")
            for c in changed[:10]:
                log(f"    {c['cls']!r}: {c['before']!r} → {c['after']!r}")

            # 连点减号，看是否能减回 1、以及下限时的表现
            minus = page.locator("[class*='minus']")
            log(f"[4] minus 数量={minus.count()}")
            for i in range(3):
                try:
                    hotel.human.click(minus.nth(1))
                    page.wait_for_timeout(400)
                    cur = page.evaluate(_POPUP_JS)
                    vals = [n["text"] for n in cur if n["cls"].lower().count("num") or "count" in n["cls"].lower()]
                    log(f"    第 {i + 1} 次点减号后：{vals[:6]}")
                except Exception as exc:
                    log(f"    第 {i + 1} 次点减号失败：{exc}")

            page.screenshot(path=str(OUT / "02_after_minus.png"))
            log(f"[5] 表单摘要：{hotel.form_text()[:160]}")
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
