"""探针：把两份登录态合并进**一个 context**，看三站是否同时登录。

要回答的问题：
  1. www 那份 state 能否覆盖 flights.ctrip.com（同属 .ctrip.com）？
  2. trains 站必须靠它自己那份 state 里的 `trains.ctrip.com` cookie，
     合并后会不会被 `.ctrip.com` 上的同名 cookie 顶掉？
  3. 三种合并策略哪一种能让 www / flights / trains 同时判登录？

做法：一次启动浏览器，三个候选各开一个 context 依次验，不重启浏览器。
  A  www 全量 + trains 专属域（trains.ctrip.com / .trains.ctrip.com）
  B  trains 全量 + www 专属域（passport.ctrip.com 与 www 的 origin 存储）
  C  trains 全量（trains 那份本身也带 20 个 .ctrip.com cookie，可能 www 也认）

跑法：HEADED=1 python tools/probes/probe_merged_auth.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright

from config import settings
from utils.auth import wait_logged_in

SITES = {
    "www": "https://www.ctrip.com",
    "flights": "https://flights.ctrip.com",
    "trains": "https://trains.ctrip.com",
}

TRAINS_DOMAINS = {"trains.ctrip.com", ".trains.ctrip.com"}
WWW_SPECIFIC = {"passport.ctrip.com"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dedupe(cookies: list[dict]) -> list[dict]:
    """同一 (name, domain, path) 只留一条，后者覆盖前者。"""
    seen: dict[tuple, dict] = {}
    for c in cookies:
        seen[(c.get("name"), c.get("domain"), c.get("path"))] = c
    return list(seen.values())


def merge(mode: str, www: dict, trains: dict) -> dict:
    if mode == "A":
        # www 打底；trains 只补它自己的专属域，不去盖 .ctrip.com
        extra = [c for c in trains["cookies"] if c["domain"] in TRAINS_DOMAINS]
        cookies = _dedupe(www["cookies"] + extra)
        origins = www.get("origins", [])
    elif mode == "B":
        # trains 打底；www 只补 passport 域
        extra = [c for c in www["cookies"] if c["domain"] in WWW_SPECIFIC]
        cookies = _dedupe(trains["cookies"] + extra)
        origins = trains.get("origins", [])
    else:  # C
        cookies = _dedupe(trains["cookies"])
        origins = trains.get("origins", [])
    return {"cookies": cookies, "origins": origins}


def main() -> int:
    www_state = _load(settings.auth_state_path("www"))
    trains_state = _load(settings.auth_state_path("trains"))

    results: dict[str, dict[str, tuple[bool, str]]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.HEADLESS, args=settings.BROWSER_UI_ARGS)
        for mode in ("A", "B", "C"):
            state = merge(mode, www_state, trains_state)
            ctx_args = {
                "locale": settings.LOCALE,
                "timezone_id": settings.TIMEZONE_ID,
                "permissions": [],
                **settings.context_view_args(),
            }
            ctx = browser.new_context(**ctx_args)
            ctx.add_cookies(state["cookies"]) if state["cookies"] else None
            print(f"\n===== 策略 {mode}：cookies={len(state['cookies'])} =====")
            results[mode] = {}
            for name, url in SITES.items():
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    ok, reason = wait_logged_in(page, timeout_ms=8000)
                    if not ok:
                        ok, reason = (False, page.url[:120])
                except Exception as exc:  # noqa: BLE001
                    ok, reason = False, f"异常：{exc}"
                results[mode][name] = (ok, reason)
                print(f"  {name:8s} 登录={str(ok):5s}  {reason}")
                page.close()
            ctx.close()
        browser.close()

    print("\n===== 汇总 =====")
    for mode, row in results.items():
        good = sum(1 for v in row.values() if v[0])
        print(f"{mode}: {good}/3  " + "  ".join(f"{k}={'Y' if v[0] else 'N'}" for k, v in row.items()))
    best = max(results.items(), key=lambda kv: sum(1 for v in kv[1].values() if v[0]))
    print(f"最优策略：{best[0]}（{sum(1 for v in best[1].values() if v[0])}/3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
