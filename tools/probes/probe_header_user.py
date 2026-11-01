"""只读探测 22：登录态首页顶部那块「用户名」到底是什么结构。

**背景（2026-11-01）**：`is_logged_in` 原三条判据（头像 img / 「退出」 / 「注册」可见）
在登录态首页全部落空 —— 实测截图里顶部只有**纯文本**用户名 +「我的订单」+「联系客服」。
临时补的第 4 条是「登录/注册都不可见 + 我的订单在场」，能用但偏糙。

本探针要把「用户名容器」的真实 selector 取证出来，好把判据从
「全局文本特征」升级成「定位容器 + 校验文本形态」，同时不硬编码用户名。

做法：加载已有的 ctrip_state.json，打开首页，把视口顶部 160px 内所有元素的
tag/class/文本导出，按文本长度筛出候选账号容器。

跑法：
    python tools/probes/probe_header_user.py
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
from utils.auth import is_logged_in  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_header" / datetime.now().strftime("%Y%m%d-%H%M%S")

_HEADER_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.top > 160 || r.bottom < 0) continue;      // 只看顶部
    if (!vis(el)) continue;
    if (el.children.length > 2) continue;            // 只收叶子附近，避免整块容器
    const t = (el.innerText || el.textContent || '').trim();
    if (!t || t.length > 24) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 120),
      id: el.id || '',
      text: t.slice(0, 24),
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
      parent_cls: (el.parentElement && el.parentElement.className || '').toString().slice(0, 100),
    });
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path()
    log("=" * 70)
    log(f"落盘：{OUT}")
    log(f"state：{state}  存在={state.exists()}")
    log("=" * 70)

    if not state.exists():
        log("✗ 没有 state 文件，先跑 scripts/setup_auth.py")
        return 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state),
            permissions=["local-network-access"],
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            page.goto("https://www.ctrip.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            ok, ev = is_logged_in(page)
            log(f"当前判据结果：{ok} / {ev}")

            nodes = page.evaluate(_HEADER_JS)
            log(f"\n顶部元素 {len(nodes)} 个：")
            for n in nodes[:45]:
                log(f"  <{n['tag']}> {n['w']}×{n['h']} at({n['x']},{n['y']}) "
                    f"text={n['text']!r}")
                log(f"       cls={n['cls']!r} id={n['id']!r} parent={n['parent_cls']!r}")

            (OUT / "header_nodes.json").write_text(
                json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.screenshot(path=str(OUT / "header.png"), clip={"x": 0, "y": 0,
                                                               "width": settings.VIEWPORT["width"],
                                                               "height": 160})
            log(f"\n截图：{OUT / 'header.png'}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
