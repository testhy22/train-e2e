"""只读探测 25：同一份 state 连续开 context，登录态会不会偶发不生效。

**背景（2026-11-01 18:0x）**：`suites/auth/tests/test_hotel_login_search.py` 全量 6 条
跑了两轮，每轮都有 **1 条**红在「顶部『注册』仍可见 ⇒ 未登录」，且：

    · 失败用例前后紧挨着的同类用例，用的是**同一份** state 文件，全部通过；
    · 失败时 URL 是 `https://www.ctrip.com/`（正常首页，不是 passport 登录墙）；
    · 加了 8 秒有界等待（等用户名容器可见）仍然判未登录。

三种可能，必须用数据分开，不能靠推测：
    A. 会话偶发不生效（站点侧 CDN / 会话校验抖动）→ 页面就是未登录
    B. 等待目标选错了（用户名容器的 class 前缀在某个版本里不同）→ 其实已登录
    C. state 快照本身在退坡（每开一次 context 就被服务端轮换掉一部分）

跑法：
    python tools/probes/probe_hotel_auth_state_stability.py            # 默认 5 轮
    python tools/probes/probe_hotel_auth_state_stability.py 10         # 自定义轮数
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
from utils.auth import is_logged_in, looks_like_login_wall, wait_logged_in  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_auth_stability" / datetime.now().strftime("%Y%m%d-%H%M%S")

# B 分支的判定：不依赖任何 class 前缀，直接把顶部区域能看到的文字全抓回来。
_HEADER_TEXT_JS = r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll('header, [class*="header"], [class*="nav"]')) {
    const r = el.getBoundingClientRect();
    if (r.top > 120 || r.height < 10) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 120) out.push(t);
  }
  return [...new Set(out)].slice(0, 8);
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    log("=" * 70)
    log(f"落盘：{OUT}   轮数={rounds}   state 存在={state.exists()}")
    log("=" * 70)
    if not state.exists():
        log("✗ 没有 state 文件")
        return 0

    rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        try:
            for i in range(1, rounds + 1):
                context = browser.new_context(
                    viewport=settings.VIEWPORT,
                    locale=settings.LOCALE,
                    timezone_id=settings.TIMEZONE_ID,
                    storage_state=str(state),
                    permissions=[],
                )
                try:
                    page = context.new_page()
                    page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
                    page.goto("https://www.ctrip.com/", wait_until="domcontentloaded")
                    # 一次性判定（旧口径）与有界等待（新口径）都记，用来分开 A / B。
                    instant, instant_ev = is_logged_in(page)
                    waited, waited_ev = wait_logged_in(page)
                    cookie_n = len(context.cookies())
                    header_text = page.evaluate(_HEADER_TEXT_JS)
                    row = {
                        "round": i,
                        "url": page.url,
                        "instant": instant,
                        "instant_ev": instant_ev,
                        "waited": waited,
                        "waited_ev": waited_ev,
                        "wall": looks_like_login_wall(page.url),
                        "cookies": cookie_n,
                        "header_text": header_text,
                    }
                    if not waited:
                        page.screenshot(path=str(OUT / f"fail_round{i}.png"))
                    rows.append(row)
                    log(
                        f"[{i}] 即时={instant}({instant_ev}) 等待后={waited}({waited_ev}) "
                        f"cookies={cookie_n} wall={row['wall']}"
                    )
                    log(f"    header: {header_text}")
                finally:
                    context.close()
        finally:
            browser.close()

    ok = sum(1 for r in rows if r["waited"])
    log("=" * 70)
    log(f"结果：{ok}/{len(rows)} 轮判已登录")
    (OUT / "rounds.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"落盘：{OUT / 'rounds.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
