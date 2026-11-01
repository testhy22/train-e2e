"""验证性探针 44：诊断监听（console / response / requestfailed）真的抓得到吗。

为什么要单独验：这三条监听如果悄悄失效，症状是「失败报告里永远写着
『未捕获 JS 报错 / 4xx / 请求失败』」—— 看起来像「页面很干净」，
实际是监听没挂上。这种假阴性比报错更难发现，必须用一个**确定会触发**的场景验一遍。

三个触发器（都不依赖站点正常与否）：
  1. `set_content` 里 setTimeout 抛异常        → console error
  2. `<img>` 指向 127.0.0.1:9（必然连不上）    → requestfailed
  3. 打开 `www.ctrip.com/__e2e_404__`（实测 404）→ response 4xx
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
from utils.diagnostics import diagnostics_of, instrument_page  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_diagnostics" / datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(locale=settings.LOCALE, no_viewport=True, permissions=[])
        try:
            page = context.new_page()
            diag = instrument_page(page, label="probe")
            log(f"[1] 已挂监听：{diag.label}")

            # 触发器 1 + 2
            # 端口 9 会被 Chromium 判为 unsafe port，**根本不会发出请求**；
            # 换成 65500（必然 connection refused）。URL 用 `.json` 而不是 `.png`，
            # 因为诊断里把图片后缀当噪音过滤掉了 —— 拿图片验会得出「监听没生效」的错误结论。
            page.set_content(
                """
                <html><body>
                  <script>
                    fetch('http://127.0.0.1:65500/nope.json').catch(() => {});
                  </script>
                </body></html>
                """
            )
            page.wait_for_timeout(1200)
            # 未捕获异常：Chromium 走的是 pageerror，不是 console
            page.evaluate("() => { setTimeout(() => { throw new Error('boom-js-from-probe'); }, 0); }")
            page.wait_for_timeout(1200)
            log(f"[2] 触发后：JS={diag.js_errors} 失败请求={diag.failed_requests}")

            # 触发器 3：真实 404
            page.goto("https://www.ctrip.com/__e2e_404__", wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            log(f"[3] 打开 404 路径后：HTTP≥400={diag.http_errors}")

            facts.update(
                js_errors=list(diag.js_errors),
                http_errors=list(diag.http_errors),
                failed_requests=list(diag.failed_requests),
            )
            facts["same_object"] = diagnostics_of(page) is diag
            facts["summary"] = diag.summary()
            log("[4] summary：\n" + diag.summary())

            page.screenshot(path=str(OUT / "01.png"))
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            ok = bool(diag.js_errors) and bool(diag.http_errors) and bool(diag.failed_requests)
            log(f"结论：三条监听都生效={ok}")
            return 0 if ok else 1
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
