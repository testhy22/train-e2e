"""抓「退出登录」真正发出的请求 URL，供用例做精确 mock。

为什么需要（2026-11-01）：
    已实测：点退出会把服务端 cticket 一起注销 —— 用同一份 state 文件重放
    判定为未登录。所以「真实退出」用例跑一次就要重扫一次码。
    要让它能反复跑，只能 mock 掉注销请求；但 mock 需要**精确的 URL**，
    猜 `**/logout**` 有可能拦不到（揽程的接口名未必叫 logout），
    届时请求照发，state 一样被烧，而且用例还会绿 —— 最坏的一种失败。

    所以先量：全量记录退出触发的请求，不拦截（会消耗这份 state），
    拿到 URL 之后用例里再精确阻截。

成本与取舍：
    这个探针**必须消耗一份 state**（不拦截就必然真注销）。
    因此默认拿 trains 那份跑：它只被火车票登录用例使用，
    www 的那份留着不动。跑完记得 `setup_auth.py --entry trains` 重采。

用法：
    python tools/probes/probe_logout_requests.py --entry trains --headed
    python tools/probes/probe_logout_requests.py --entry www --headed   # 会烧 www
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.header_account import HeaderAccount, LogoutUnavailable  # noqa: E402
from utils.auth import is_logged_in  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

# 判断「这条请求像不像注销」的线索。只用于**报告排序**，不用于拦截 ——
# 拦截决策在这里是「一条都不拦」，否则拿不到真实 URL。
SUSPECT = re.compile(r"logout|signout|sign-out|loginout|quit|exit|unbind", re.IGNORECASE)


def main() -> int:
    ap = argparse.ArgumentParser(description="记录退出时发出的请求（会消耗 state）")
    ap.add_argument("--entry", choices=("www", "trains"), default="trains")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    state = settings.auth_state_path(args.entry)
    if not state.exists():
        print(f"✗ state 不存在：{state}")
        return 2
    url = settings.hotel_base_url() if args.entry == "www" else settings.base_url()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) if args.out else settings.REPORTS_DIR / "probe_logout_requests" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"state : {state.name}（跑完即失效）")
    print(f"url   : {url}")

    records: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=args.headless)
        ctx = browser.new_context(
            storage_state=str(state),
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
        )

        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type not in ("xhr", "fetch", "document"):
                    return
                records.append({
                    "method": req.method,
                    "url": resp.url,
                    "status": resp.status,
                    "resource_type": req.resource_type,
                    "post_data": (req.post_data or "")[:300],
                })
            except Exception:
                pass

        ctx.on("response", on_response)

        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        dismiss_promo_overlays(page, tag="logout-req")

        logged, ev = is_logged_in(page)
        print(f"登录判定：{logged} | {ev}")
        if not logged:
            print("✗ 前置条件不成立")
            ctx.close(); browser.close()
            return 3

        mark = len(records)
        acct = HeaderAccount(page)
        try:
            evidence = acct.logout()
            print(f"✓ 退出成功：{evidence}")
        except LogoutUnavailable as exc:
            print(f"⚠️ 退出入口不可用：{exc}")
            ctx.close(); browser.close()
            return 4

        page.wait_for_timeout(3000)  # 让跳转/额外的清理请求跑完
        ctx.close()
        browser.close()

    after = records[mark:]
    suspects = [r for r in after if SUSPECT.search(r["url"])]
    (outdir / "requests.json").write_text(
        json.dumps({"all_after_logout": after, "suspects": suspects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n退出后共 {len(after)} 条请求，其中 URL 含注销字样的 {len(suspects)} 条：")
    for r in suspects:
        print(f"  {r['method']:6} {r['status']} {r['url'][:110]}")
        if r["post_data"]:
            print(f"         body: {r['post_data'][:120]}")

    print(f"\n全部请求（供逐条判断哪条才是注销）：")
    for r in after:
        print(f"  {r['method']:6} {r['status']} {r['resource_type']:9} {r['url'][:100]}")

    print(f"\n现场：{outdir}")
    print("下一步：用上面命中的 URL 写 mock 用例，然后重采这份 state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
