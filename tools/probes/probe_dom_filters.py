"""只读探针：三站各跑一次真实搜索，dump 结果页上「筛选类容器」的实测结构。

用途：为 utils/discover.HINTS 选定 selector。**selector 必须以本探针输出为准，
禁止按「看着像」猜**（与酒店 city_id 同一条铁律）。

输出：reports/probe/dom_filters_<site>.txt，每个命中容器一段
（class + 前若干子项的文本），人读后挑 selector 落码。

只读：不改页面、不发额外请求（搜索本身是探针主体）；头less 与否均可，
但火车/机票建议 HEADED=1（K-05）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.case_data import (  # noqa: E402
    load_flight_cases,
    load_hotel_search_cases,
    load_train_smoke_cases,
)
from utils.search_flow import search_flight, search_hotel, search_train  # noqa: E402

# class 名命中这些关键词的元素视为「筛选类容器」候选（第一轮探针用）。
# 第二轮改为种子选择器：对少量容器打印两层级结构，条目级 selector 以此为准。
KEYWORDS = re.compile(r"(filter|sort|screen|condition|select[_-]?bar|list[_-]?filter)")

# 第一轮探针（全量 class 扫描）实测得到的候选容器，本轮按站点下钻。
SEEDS: dict[str, list[str]] = {
    "trains": [".filter-list", ".sort-left"],
    "hotels": [".filter-main-items", ".trip-filter-list-main", ".filter-container"],
    "flights": [".filterbar-v2", ".sortbar-v2"],
}
MAX_SNIP = 1400

OUT_DIR = Path("reports/probes")


def dump(page, site: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"dom_filters_{site}.txt"
    seeds = SEEDS.get(site, [])
    lines = [f"url: {page.url}", ""]
    for seed in seeds:
        lines.append(f"===== seed: {seed}")
        try:
            outline = page.evaluate(
                """(sel) => {
                    const root = document.querySelector(sel);
                    if (!root) return '(not found)';
                    const outline = [];
                    for (const child of root.children) {
                        const cls = String(child.className || '');
                        const tag = child.tagName.toLowerCase();
                        const text = (child.innerText || '').replace(/\\s+/g, ' ').slice(0, 80);
                        outline.push(`  <${tag} class="${cls}"> ${text}`);
                        for (const gc of Array.from(child.children).slice(0, 15)) {
                            const gcls = String(gc.className || '');
                            const gtag = gc.tagName.toLowerCase();
                            const gtext = (gc.innerText || '').replace(/\\s+/g, ' ').slice(0, 60);
                            outline.push(`      <${gtag} class="${gcls}"> ${gtext}`);
                        }
                    }
                    return outline.slice(0, 120).join('\\n');
                }""",
                seed,
            )
        except Exception as exc:  # noqa: BLE001
            outline = f"(evaluate failed: {exc})"
        lines.append(outline or "")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"[probe] {site} seeds={seeds} -> {target}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(storage_state="config/auth/ctrip_state.json")
        page = context.new_page()

        out = search_hotel(page, load_hotel_search_cases()[0])
        print(f"hotel landed={out.landed} cards={out.cards}")
        if out.landed:
            dump(page, "hotels")

        out = search_train(page, load_train_smoke_cases()[0])
        print(f"train landed={out.landed} cards={out.cards}")
        if out.landed:
            dump(page, "trains")

        out = search_flight(page, load_flight_cases()[0])
        print(f"flight landed={out.landed} cards={out.cards}")
        if out.landed:
            dump(page, "flights")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
