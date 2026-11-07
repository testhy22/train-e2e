"""只读探针：无头返回 432 时，判据到底是「无显示设备」还是「headless shell 二进制」。

起因（2026-11-07）：K-05 的对照实验只切了 `HEADED`，而 `HEADED` 同时改变了两件事 ——
  ① 有没有真实显示设备；
  ② 用哪个二进制：无头走 `chromium_headless_shell`，有头走 `chromium`（同一份 Playwright 包里两个 exe）。
所以「有头 200 / 无头 432」只证明了两者相关，**没有**证明判据是显示设备。
本探针把 ② 单独拎出来：都无头，只换二进制。

三组（同 UA 口径、同 args、同 locale/时区，只有 channel 不同）：
  A. 默认（channel=None）      → chromium_headless_shell
  B. channel="chromium"        → 同一份 Playwright 的完整 chromium + 新版无头
  C. channel="chrome"          → 系统真实 Chrome + 新版无头

只读：每组只发一次 GET，不点任何控件、不写 cookie、不登录。
禁改指纹：不设 UA、不注入脚本、不加 AutomationControlled 参数。

    python tools/probes/probe_headless_binary.py

产物：stdout 一张表 + `reports/probe/headless_binary_<时间戳>.json`。
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

TARGET = "https://trains.ctrip.com"
GROUPS = [
    ("A 默认(headless shell)", None),
    ("B chromium 全量二进制", "chromium"),
    ("C 系统真实 Chrome", "chrome"),
]
BODY_SNIPPET_CHARS = 300


def probe_once(pw, channel: str | None) -> dict:
    record: dict = {"channel": channel or "(default)"}
    kwargs: dict = {
        "headless": True,
        "args": list(settings.BROWSER_UI_ARGS),
    }
    if channel:
        kwargs["channel"] = channel
    try:
        browser = pw.chromium.launch(**kwargs)
    except Exception as exc:  # noqa: BLE001 —— 探针要把「启动不起来」也记成一行结果
        record["launch_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return record

    try:
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            viewport=settings.VIEWPORT,
        )
        page = context.new_page()
        response = page.goto(TARGET, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1500)
        body = ""
        try:
            body = page.inner_text("body")[:BODY_SNIPPET_CHARS]
        except Exception:  # noqa: BLE001
            pass
        record.update(
            {
                "status": response.status if response else None,
                "title": (page.title() or "")[:80],
                "body_len": len(page.content()),
                "body_snippet": body.replace("\n", " ")[:120],
                "whaleguard": "whaleguard" in body.lower(),
                "ua": page.evaluate("() => navigator.userAgent"),
                "webdriver": page.evaluate("() => navigator.webdriver"),
            }
        )
        context.close()
    except Exception as exc:  # noqa: BLE001
        record["page_error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        browser.close()
    return record


def main() -> int:
    results = []
    with sync_playwright() as pw:
        for label, channel in GROUPS:
            record = probe_once(pw, channel)
            record["label"] = label
            results.append(record)
            print(
                f"{label:26s} status={record.get('status')} "
                f"body_len={record.get('body_len')} "
                f"whaleguard={record.get('whaleguard')} "
                f"err={record.get('launch_error') or record.get('page_error') or '-'}"
            )

    out = Path("reports/probe")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"headless_binary_{datetime.now():%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
