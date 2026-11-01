"""取证 trains.ctrip.com 登录态的顶部结构，回答一个具体问题：

    手上这份 ctrip_state_trains.json 到底能不能让火车票站进入登录态？
    如果能，登录标识长什么样、`utils/auth.is_logged_in` 的四条判据为什么没命中？

背景（2026-11-01 实测）：
    www.ctrip.com 登录后用同一份 state 打开 trains.ctrip.com 仍判未登录 —— 两站登录态
    不互通，因此 `scripts/setup_auth.py --entry trains` 单独采了一份
    `config/auth/ctrip_state_trains.json`。采完立刻验证时 `title` 是空的、
    四条判据全落空，但当时只等了 networkidle 8s。trains 是 SPA，
    首屏骨架和账号区渲染时机不同，**很可能又是我等短了**，而不是 state 无效。

    这个探针就是为了区分这两种情况：state 无效 vs 判据不覆盖。

它只读：不登录、不点击、不写 state。产出落 reports/probe_trains_header/<时间戳>/。

用法：
    python tools/probes/probe_trains_header.py
    python tools/probes/probe_trains_header.py --state config/auth/ctrip_state_trains.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from utils.auth import is_logged_in  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

# 顶部账号区候选。全部按**结构语义**而非具体类名取样：
# www 站实测是 `span[class*='header_login_title_name']`，类名带构建哈希不可硬编码；
# trains 站是否同构未知，所以用多组片段扫描而不是断言某一个。
ZONE_FRAGMENTS = (
    "login_title_name",
    "loginName",
    "userName",
    "user-name",
    "hdr-user",
    "member",
    "avatar",
    "nickname",
)


def snapshot(page, outdir: Path, tag: str) -> dict:
    """一次 evaluate 取回账号区 + 顶部导航摘要。

    用单次 evaluate 而不是逐元素 inner_text()：本项目有 538 条逐条取文本
    撞 180s 超时的前科（E-008），元素一多就必须一次往返。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    data = page.evaluate(
        """(frags) => {
          const vis = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0
              && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const zones = [];
          const sel = frags.map(f => `[class*='${f}']`).join(',');
          document.querySelectorAll(sel).forEach(el => {
            if (zones.length >= 40) return;
            zones.push({
              tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().slice(0, 120),
              text: (el.innerText || '').trim().slice(0, 40),
              visible: vis(el),
              w: Math.round(el.getBoundingClientRect().width),
              h: Math.round(el.getBoundingClientRect().height),
            });
          });
          // 顶部 200px 内所有可见文本，用来判断「登录/注册」还在不在
          const headerTexts = [];
          document.querySelectorAll('header *, [class*="header"] *, [class*="hdr"] *').forEach(el => {
            if (headerTexts.length >= 60) return;
            const r = el.getBoundingClientRect();
            if (r.top > 200 || r.width <= 0 || r.height <= 0) return;
            const t = (el.innerText || '').trim();
            if (t && t.length <= 20 && el.children.length === 0) headerTexts.push(t);
          });
          return {
            url: location.href,
            title: document.title,
            body_len: (document.body.innerText || '').length,
            zones,
            header_texts: [...new Set(headerTexts)],
            has_login_text: document.body.innerText.includes('登录'),
            has_register_text: document.body.innerText.includes('注册'),
          };
        }""",
        list(ZONE_FRAGMENTS),
    )
    (outdir / f"{tag}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        page.screenshot(path=str(outdir / f"{tag}.png"), full_page=False)
    except Exception:
        pass
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="取证 trains 站登录态顶部结构（只读）")
    parser.add_argument("--state", default=None, help="state 文件路径（默认 trains 那份）")
    parser.add_argument("--url", default="https://trains.ctrip.com/")
    parser.add_argument("--wait", type=int, default=20, help="等账号区渲染的上限秒数")
    parser.add_argument("--headed", action="store_true",
                        help="有头运行。**trains 站默认无头会被风控拦**（K-05：页面只剩 "
                             "「whaleguard block」一行，body_len≈17），验证登录态必须开这个")
    args = parser.parse_args()

    state = Path(args.state) if args.state else settings.auth_state_path("trains")
    if not state.exists():
        print(f"✗ state 文件不存在：{state}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = settings.REPORTS_DIR / "probe_trains_header" / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"state : {state.name}（{state.stat().st_size} bytes）")
    print(f"url   : {args.url}")
    print(f"落盘  : {outdir}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=not args.headed)
        context = browser.new_context(
            storage_state=str(state),
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
        )
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")

        # 轮询等「页面真的画出来了」，而不是固定 sleep：
        # 判据是 body 有文本 + 能找到任一账号区候选。两者都不满足就等满上限再下结论。
        deadline = time.time() + args.wait
        best = None
        while time.time() < deadline:
            try:
                dismiss_promo_overlays(page, tag="trains")
            except Exception:
                pass
            data = snapshot(page, outdir, "current")
            best = data
            if data["body_len"] > 200 and data["zones"]:
                break
            page.wait_for_timeout(1000)

        data = best or {}
        print(f"\ntitle    : {data.get('title')!r}")
        print(f"body_len : {data.get('body_len')}")
        print(f"含「登录」: {data.get('has_login_text')}  含「注册」: {data.get('has_register_text')}")
        print(f"\n顶部文本（去重，前 30 条）：{data.get('header_texts', [])[:30]}")

        zones = data.get("zones") or []
        print(f"\n账号区候选 {len(zones)} 个（visible 在前）：")
        for z in sorted(zones, key=lambda x: (not x["visible"], -x["w"]))[:15]:
            print(
                f"  vis={str(z['visible']):5} {z['w']:>4}x{z['h']:<4} "
                f"<{z['tag']}> {z['cls'][:60]!r} text={z['text'][:24]!r}"
            )

        logged, evidence = is_logged_in(page)
        (outdir / "verdict.json").write_text(
            json.dumps(
                {"logged": logged, "evidence": evidence, "zones": zones,
                 "header_texts": data.get("header_texts")},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n当前判据结果：logged={logged}  证据={evidence}")

        # 额外：直接看 cookie 里到底有没有 trains 域的登录票据
        cookies = context.cookies()
        trains_ck = [c for c in cookies if "trains" in (c.get("domain") or "")]
        print(f"\ncookie 总数 {len(cookies)}，trains 域 {len(trains_ck)} 条：")
        for c in trains_ck:
            print(f"  {c['name']} = {str(c['value'])[:24]}…  domain={c['domain']}")

        context.close()
        browser.close()

    print(f"\n现场：{outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
