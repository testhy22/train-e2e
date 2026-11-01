"""验证「退出登录」的影响面：只清本地会话，还是连服务端票据一起废掉？

为什么必须单独验（2026-11-01）：
    一旦退出动作会让服务端注销 cticket，那么
      · 跑一次退出用例 = 烧掉一份本机凭据（要重新扫码才能恢复）；
      · 更糟的是它不会当场报错 —— 用例本身绿了（确实退出成功），
        下次跑 auth 登录用例才发现「未登录」，届时没人会想到是退出用例干的。
    这类「绿了但埋雷」的副作用必须先量出来，才能决定退出用例是否默认执行。

方法（两段，同一份 state 文件）：
    阶段 A：加载 state → 已登录 → hover → 点退出 → 确认回到未登录态
    阶段 B：用**同一个 state 文件**新开一个 context → 打开站点 → 是否仍已登录

    阶段 B 仍登录  ⇒ 退出只是本地行为（或服务端懒注销），state 可反复使用，
                     退出用例可以放心默认跑。
    阶段 B 未登录  ⇒ state 已被消耗。退出用例必须改成 opt-in（默认 skip），
                     且文档要写明「跑一次要重扫一次」。

只读以外的动作只有「点退出」这一下，且只作用于浏览器内会话；磁盘上的
state 文件不会被改写（脚本全程不调 storage_state 写回）。

用法：
    python tools/probes/probe_logout_effect.py             # www
    python tools/probes/probe_logout_effect.py --headless  # 无头（trains 会被 WAF 拦）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.header_account import HeaderAccount, LogoutUnavailable, session_cookie_names  # noqa: E402
from utils.auth import is_logged_in, looks_like_login_wall  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402


def open_site(ctx, url: str):
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    dismiss_promo_overlays(page, tag="logout-effect")
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description="量退出动作的影响面（只读 state 文件）")
    ap.add_argument("--entry", choices=("www", "trains"), default="www")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    state = settings.auth_state_path(args.entry)
    if not state.exists():
        print(f"✗ state 不存在：{state}")
        return 2
    url = settings.hotel_base_url() if args.entry == "www" else settings.base_url()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) if args.out else settings.REPORTS_DIR / "probe_logout_effect" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"state : {state.name}")
    print(f"url   : {url}")
    print(f"落盘  : {outdir}")

    report: dict = {"entry": args.entry, "state": state.name}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=args.headless)

        # ---------------- 阶段 A：确认已登录 → 退出 ----------------
        print("\n" + "=" * 66)
        print("阶段 A：加载 state → 确认已登录 → 点退出")
        ctx_a = browser.new_context(
            storage_state=str(state),
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
        )
        page = open_site(ctx_a, url)
        logged_a, ev_a = is_logged_in(page)
        print(f"  登录判定：{logged_a} | {ev_a}")
        report["before_logged"] = logged_a
        report["before_evidence"] = ev_a
        report["cookies_before"] = session_cookie_names(ctx_a)
        print(f"  登录相关 cookie：{report['cookies_before']}")

        if not logged_a:
            print("  ✗ 前置条件不成立（本来就没登录），无法量退出影响")
            ctx_a.close(); browser.close()
            return 3

        acct = HeaderAccount(page)
        try:
            evidence = acct.logout()
            print(f"  ✓ 退出成功：{evidence}")
            report["logout_ok"] = True
            report["logout_evidence"] = evidence
        except LogoutUnavailable as exc:
            print(f"  ⚠️ 退出入口不可用（已知限制）：{exc}")
            report["logout_ok"] = False
            report["logout_reason"] = f"LogoutUnavailable: {exc}"
            page.screenshot(path=str(outdir / "A_logout_unavailable.png"))
            ctx_a.close(); browser.close()
            (outdir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 4
        except AssertionError as exc:
            print(f"  ✗ 点了退出但仍是登录态：{exc}")
            report["logout_ok"] = False
            report["logout_reason"] = f"AssertionError: {exc}"
            page.screenshot(path=str(outdir / "A_logout_failed.png"))
            ctx_a.close(); browser.close()
            (outdir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 5

        page.screenshot(path=str(outdir / "A_after_logout.png"))
        report["url_after_logout"] = page.url
        report["cookies_after"] = session_cookie_names(ctx_a)
        print(f"  退出后登录相关 cookie：{report['cookies_after']}")
        print(f"  退出后 url：{page.url}")
        ctx_a.close()

        # ---------------- 阶段 B：同一份 state 重放 ----------------
        print("\n" + "=" * 66)
        print("阶段 B：用**同一份 state 文件**新开 context，看是否仍能登录")
        ctx_b = browser.new_context(
            storage_state=str(state),
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
        )
        page_b = open_site(ctx_b, url)
        logged_b, ev_b = is_logged_in(page_b)
        print(f"  登录判定：{logged_b} | {ev_b}")
        print(f"  落在登录墙：{looks_like_login_wall(page_b.url)}")
        page_b.screenshot(path=str(outdir / "B_replay.png"))
        report["replay_logged"] = logged_b
        report["replay_evidence"] = ev_b
        report["replay_url"] = page_b.url
        ctx_b.close()

        browser.close()

    verdict = (
        "state 未被消耗：退出只影响该浏览器会话，退出用例可以默认执行"
        if report.get("replay_logged")
        else "⚠️ state 已被消耗：跑一次退出用例就要重扫一次，退出用例必须改为 opt-in"
    )
    report["verdict"] = verdict
    (outdir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 66)
    print(f"结论：{verdict}")
    print(f"现场：{outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
