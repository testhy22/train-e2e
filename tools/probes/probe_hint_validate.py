"""hint 定稿验证探针（第三轮，决定性）：三站各跑一次真实搜索，
然后用 utils/discover.HINTS 的**真实提取器**在结果页上实跑，
打印每个 hint 的 found / not_present / collect_errors。

与 probe_dom_filters.py 的分工：
- probe_dom_filters.py：结构勘察（容器/条目长什么样），供挑 selector；
- 本脚本：定稿验证 —— HINTS 里写的提取器在真实页面上能不能拿到值。
  站点改版后先重跑本脚本，哪个 hint 失效一目了然。

输出：reports/probes/hint_validate_<site>.txt（快照归档）。
只读：采集器本身不点击、不滚动、不等待。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import discover  # noqa: E402
from utils.case_data import (  # noqa: E402
    load_flight_cases,
    load_hotel_search_cases,
    load_train_smoke_cases,
)
from utils.search_flow import search_flight, search_hotel, search_train  # noqa: E402

OUT_DIR = Path("reports/probes")


def validate(page, site: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"hint_validate_{site}.txt"
    lines = [f"url: {discover.sanitize_url(page.url)}", ""]
    for hint in discover.HINTS.get(site, []):
        dim = hint["dimension"]
        t0 = time.perf_counter()
        try:
            raw = discover._extract(page, hint)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if raw is None:
                lines.append(f"[{dim}] not_present（{elapsed_ms:.0f}ms）")
            elif not isinstance(raw, list):
                lines.append(f"[{dim}] SHAPE MISMATCH: {type(raw).__name__}（{elapsed_ms:.0f}ms）")
            else:
                vals = [v for v in (str(x).strip() for x in raw) if v]
                lines.append(
                    f"[{dim}] {len(vals)} 值（{elapsed_ms:.0f}ms）: {vals[:12]}"
                    + ("…" if len(vals) > 12 else "")
                )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"[{dim}] EXC {type(exc).__name__}: {exc}")
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"[validate] {site} -> {target}")
    print("\n".join(lines))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(storage_state="config/auth/ctrip_state.json")
        page = context.new_page()

        out = search_hotel(page, load_hotel_search_cases()[0])
        print(f"hotel landed={out.landed} cards={out.cards}")
        if out.landed:
            validate(page, "hotels")

        out = search_train(page, load_train_smoke_cases()[0])
        print(f"train landed={out.landed} cards={out.cards}")
        if out.landed:
            validate(page, "trains")

        out = search_flight(page, load_flight_cases()[0])
        print(f"flight landed={out.landed} cards={out.cards}")
        if out.landed:
            validate(page, "flights")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
