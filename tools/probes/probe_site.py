"""一次性探针：打开目标站点，记录 title / URL / 是否被拦 / 候选定位器。

只读取，不提交表单、不点击搜索。跑完即弃，不进 testpaths。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from pages.base_page import BasePage

TARGET = "https://trains.ctrip.com"
OUT = Path("reports/probe")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    result: dict = {"target": TARGET}

    try:
        page.goto(TARGET, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        result["url"] = page.url
        result["title"] = page.title()
        result["body_len"] = len(page.locator("body").inner_text(timeout=5000))

        bp = BasePage(page, TARGET)
        sig = bp.detect_block()
        result["blocked"] = None if sig is None else {"kind": sig.kind, "detail": sig.detail}

        page.screenshot(path=str(OUT / "home.png"), full_page=True)

        # 候选定位器候选：所有输入框与按钮
        result["inputs"] = page.eval_on_selector_all(
            "input",
            """els => els.slice(0, 25).map(e => ({
                 type: e.type, placeholder: e.placeholder, aria: e.getAttribute('aria-label'),
                 id: e.id, cls: (e.className || '').toString().slice(0, 80),
                 visible: !!(e.offsetWidth || e.offsetHeight) }))""",
        )
        result["buttons"] = page.eval_on_selector_all(
            "button, [role=button], a.btn",
            "els => els.slice(0, 25).map(e => (e.innerText || '').trim().slice(0, 20)).filter(Boolean)",
        )
    except Exception as exc:  # 打开失败本身也要落证据
        result["error"] = f"{type(exc).__name__}: {exc}"
        try:
            page.screenshot(path=str(OUT / "open-failed.png"), full_page=True)
        except Exception:
            pass

    (OUT / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2500])
    ctx.close()
    browser.close()
