"""取证「退出登录」入口的真实位置与触发方式，回答三个具体问题：

    1. 退出入口是**悬停浮层**还是**点击展开**？触发前它到底在不在 DOM 里？
    2. 「退出」文案的精确节点是什么（tag / class / 完整文本）？
    3. 它归属哪个容器？—— 决定了选择器要限定在哪个 DOM 子树里，
       而不是像早期 `get_by_text(x, exact=True).first` 那样全局揪一个凑巧同名的。

背景（2026-11-01）：
    用户在需求里给了「hover 用户区 → 等下拉菜单 → 点退出登录」的流程，但
    那是从页面结构**推断**的，本项目从没取过证。项目里已经有过一次同类教训：
    「城市名 exact 匹配」命中了内层 span 和页面底部的同名文案，做出假成功。
    所以在写 `logout()` 之前，先把这个浮层扫出来。

只读：不点退出、不改 cookie、不写 state。产出落 reports/probe_logout/<时间戳>/。

用法：
    python tools/probes/probe_logout_menu.py            # www
    python tools/probes/probe_logout_menu.py --entry trains --headed
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
from utils.auth import is_logged_in  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

# 用户区候选。www 站实测是 `span[class*='header_login_title_name']`
# （tools/probes/probe_header_user.py 取证）；trains 站同构。
USER_ZONE = "span[class*='header_login_title_name']"

# 退出入口候选文本。中文站常见三种写法，全部按**子串**扫，
# 因为取证阶段还不知道精确文案 —— 这一轮就是为了消除这个「不知道」。
LOGOUT_HINTS = ("退出登录", "退出", "注销", "退登")


def scan(page) -> dict:
    """扫一遍当前 DOM 里与「退出」有关的节点，含隐藏的。

    关键：**不过滤可见性**。悬停菜单在触发前通常是 `display:none`
    或挂在别的容器里，按可见性过滤会让它看起来「根本不存在」，
    于是得出「只能清 cookie」的假结论。
    """
    return page.evaluate(
        """(hints) => {
          const vis = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0
              && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const hits = [];
          document.querySelectorAll('a,button,li,div,span,p').forEach(el => {
            if (hits.length >= 60) return;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 12) return;
            if (!hints.some(h => t.includes(h))) return;
            const r = el.getBoundingClientRect();
            hits.push({
              tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().slice(0, 140),
              id: el.id || '',
              text: t,
              visible: vis(el),
              rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
              parent_cls: (el.parentElement && el.parentElement.className || '').toString().slice(0, 120),
              // 往上找 3 层，用来判断该限定在哪个子树里定位
              trail: (() => {
                const out = [];
                let cur = el.parentElement, n = 0;
                while (cur && n < 3) {
                  out.push(cur.tagName.toLowerCase() + '.' + (cur.className || '').toString().slice(0, 60));
                  cur = cur.parentElement; n++;
                }
                return out;
              })(),
            });
          });
          return {
            url: location.href,
            title: document.title,
            body_len: (document.body.innerText || '').length,
            hits,
            // 全量 body 文本里有没有「退出」二字，用来区分
            // 「有节点但没抓到」与「页面里压根没有」
            body_has_logout: document.body.innerText.includes('退出'),
          };
        }""",
        list(LOGOUT_HINTS),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="取证退出登录入口（只读）")
    ap.add_argument("--entry", choices=("www", "trains"), default="www")
    ap.add_argument("--headed", action="store_true", help="有头（trains 站无头会被 WAF 拦）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    state = settings.auth_state_path(args.entry)
    if not state.exists():
        print(f"✗ state 不存在：{state}（先跑 setup_auth.py --entry {args.entry}）")
        return 2

    url = settings.hotel_base_url() if args.entry == "www" else settings.base_url()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) if args.out else settings.REPORTS_DIR / "probe_logout" / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"state : {state.name}")
    print(f"url   : {url}")
    print(f"落盘  : {outdir}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=not args.headed)
        ctx = browser.new_context(
            storage_state=str(state),
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        dismiss_promo_overlays(page, tag="logout")

        logged, evidence = is_logged_in(page)
        print(f"\n登录判定：{logged} | {evidence}")
        if not logged:
            print("✗ 未进入登录态，先解决 state 问题再看退出入口")
            page.screenshot(path=str(outdir / "not_logged_in.png"))
            ctx.close(); browser.close()
            return 3

        zone = page.locator(USER_ZONE).first
        box = zone.bounding_box()
        print(f"用户区：{USER_ZONE} box={box}")
        page.screenshot(path=str(outdir / "01_before_hover.png"))

        before = scan(page)
        (outdir / "01_before_hover.json").write_text(
            json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[悬停前] 命中 {len(before['hits'])} 个退出相关节点；"
              f"body 含「退出」={before['body_has_logout']}")
        for h in before["hits"][:10]:
            print(f"  vis={str(h['visible']):5} <{h['tag']}> {h['cls'][:50]!r} text={h['text']!r}")

        # 用 mouse.move 而不是 locator.hover()：实测过「hover 与 pointer-enter
        # 竞争导致菜单开了又关」，物理移动更接近真人，也便于后续复用同一个动作。
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            zone.hover()

        # 等浮层出现：轮询 DOM 里是否多出可见的退出节点，而不是固定 sleep
        appeared = False
        for _ in range(20):
            page.wait_for_timeout(300)
            cur = scan(page)
            if any(h["visible"] for h in cur["hits"]):
                appeared = True
                break

        after = scan(page)
        (outdir / "02_after_hover.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
        page.screenshot(path=str(outdir / "02_after_hover.png"))

        vis_hits = [h for h in after["hits"] if h["visible"]]
        print(f"\n[悬停后] 命中 {len(after['hits'])} 个，其中可见 {len(vis_hits)} 个；"
              f"浮层是否出现={appeared}")
        for h in vis_hits[:10]:
            print(f"  <{h['tag']}> {h['cls'][:60]!r} text={h['text']!r} rect={h['rect']}")
            print(f"      上级：{h['trail']}")

        print(f"\n结论材料：{'悬停可展开' if appeared else '悬停未展开可见退出节点'}")
        print(f"现场：{outdir}")

        ctx.close(); browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
