#!/usr/bin/env python
"""人工扫码建立登录态，落到 config/auth/ctrip_state.json。

只做一件事：**给真人**开一扇有头窗口，让他自己扫码，然后把会话存下来。
工程不代填账号密码、不模拟扫码、不碰验证码 —— 这三条任一成立，本文件就退化成
「拿凭据换通过率」的工具，与 config/settings.py 文件头的 D 决策冲突。

用法（在仓库根执行）
    python scripts/setup_auth.py                    # 默认从 www.ctrip.com 入口
    python scripts/setup_auth.py --entry trains     # 从火车票站入口
    python scripts/setup_auth.py --timeout 300      # 给自己 5 分钟扫码
    python scripts/setup_auth.py --no-trains-check  # 跳过两站互通性检查

⚠️ 选择器取证的诚实标注（别跳过这段）
    本项目真正取过证的只有一条：未登录态提交搜索会落到
    `passport.ctrip.com/user/login?backurl=…`（K-04 / K-06 的现场截图）。
    **微信入口、微信授权页、「允许」按钮、已登录后的头像区，一次都没取过证。**
    所以本脚本不假装知道 DOM：
      · 每个候选选择器都在注释里标了是否实测；
      · 任何一步失败都会把现场落盘到 `reports/probe_auth/<时间戳>/`
        （html + 可见文本 + anchors/imgs/iframes 摘要 + cookie 快照），
        并把路径打在屏幕上，据此再精确化 —— 这是「先落证据再改选择器」，
        不是「先猜一个能跑的」；
      · 宁可停下来报错，也不硬点一个不匹配的元素制造「看似成功」。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import (
    BrowserContext,
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeout,
)

from config import settings
from utils.auth import is_logged_in, state_summary
from utils.overlay import dismiss_promo_overlays
from utils.wechat import can_show_dialog, detect_wechat, prompt_user_login

# ---------------------------------------------------------------- 常量

ENTRY_URLS = {
    "www": "https://www.ctrip.com/",
    "trains": "https://trains.ctrip.com/",
}

# 登录入口。`登录` 在截图里确认存在于 www 顶部导航（2026-11-01 用户截图），
# ⚠️ trains 站未取证，靠同站模板推断。
LOGIN_TEXT = "登录"

# 微信入口候选。**全部 ⚠️[未查实]** —— 没有任何一次实测。顺序按语义稳定性排：
# 文案 > alt 属性 > 类名片段。类名片段放最后，因为携程构建后可能带哈希。
WECHAT_ENTRY_CANDIDATES: tuple[str, ...] = (
    "text=微信",
    "text=扫码登录",
    "img[alt*='微信']",
    "[class*='weixin']",
    "[class*='wechat']",
    "[class*='wx-login']",
)

# 微信授权页的「允许」按钮候选。**全部 ⚠️[未查实]**。
# 注意：PC 端扫码授权常常是手机侧操作，授权页本体可能只有二维码、没有「允许」。
# 所以「找不到允许按钮」不是错误路径，是预期分支之一（见 handle_authorize_page）。
ALLOW_BUTTON_CANDIDATES: tuple[str, ...] = (
    'button:has-text("允许")',
    'a:has-text("允许")',
    '[role="button"]:has-text("允许")',
)

# 已登录后的头像 / 账户区候选、以及「是否已登录」的全部判据，**不在这里定义**：
# 统一放在 utils/auth.py，采集脚本与 suites/auth 共用同一份，避免出现
# 「脚本说登录成功了、用例说没登录」这种自相矛盾。

# 只用这两个参数做「URL 脏参数」清洗。刻意不做通配剥离：
# 把不认识的 query 一起剁掉，可能连带剁掉站点自己的状态参数，反而让登录流程异常。
DIRTY_QUERY_KEYS = ("allianceid", "sid")


# ---------------------------------------------------------------- 小工具

def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK，中文会变成乱码假象。

    这里只改**本进程输出编码**，不碰任何文件编码 —— 按工作区规矩，
    控制台乱码是显示假象，不是文件坏了，绝不能因此去改文件编码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


def clean_url(url: str) -> str:
    """剥掉 allianceid / sid 两个联盟推广参数，其余原样保留。"""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in DIRTY_QUERY_KEYS]
    return urlunparse(parsed._replace(query=urlencode(kept)))


def install_dirty_url_guard(context: BrowserContext) -> None:
    """拦截文档请求，把带联盟参数的 URL 换掉。

    只处理 `resource_type == "document"`：静态资源带不带 allianceid 不影响登录，
    全量重写只会让请求变慢，出问题时还更难查。
    """

    def _handler(route, request):
        try:
            if request.resource_type == "document":
                cleaned = clean_url(request.url)
                if cleaned != request.url:
                    route.continue_(url=cleaned)
                    return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    context.route("**/*", _handler)


# 页面现场采集脚本。三个刻意的设计，都是为了「不漏」：
#   1. **不按可见性过滤**，改成记 visible 字段。微信登录入口很可能挂在某个
#      未激活的 tab 里（display:none），按可见性过滤就永远找不到它 ——
#      这也是旧版探针对这类入口全盲的原因。
#   2. **采 data-\* 属性**。现代前端把关键语义放在 data-xxx 上，只抓 class 会漏一半线索。
#   3. **单独扫 keyword_hits**：不限标签，凡 class / id / data-* / 文本里含
#      微信 / weixin / qrcode 的元素全收。这是「坐实选择器」这一步的主索引。
# 关键词里的 'wx-' 与 'wxlogin' 是刻意写窄的：单写 'wx' 会把一堆无关类名卷进来。
_PROBE_JS = r"""
() => {
  const info = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const attrs = {};
    for (const a of el.attributes) {
      const n = a.name.toLowerCase();
      if (n.startsWith('data-') || ['role','aria-label','title','alt','src','href',
          'placeholder','type','name','for'].includes(n)) {
        attrs[n] = (a.value || '').slice(0, 200);
      }
    }
    return {
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || el.textContent || el.value || '').trim().slice(0, 100),
      cls: (el.className || '').toString().slice(0, 200),
      id: el.id || '',
      visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden'
               && s.display !== 'none' && s.opacity !== '0',
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      attrs: attrs,
    };
  };

  const all = (sel, limit) => {
    const nodes = Array.from(document.querySelectorAll(sel));
    return {
      total: nodes.length,
      truncated: nodes.length > limit,
      items: nodes.slice(0, limit).map(info),
    };
  };

  const KW = ['微信', 'weixin', 'wechat', 'wx-', 'wxlogin', 'wx_login',
              'qrcode', 'qr-code', '二维码', '扫码'];
  const hits = [];
  const nodes = document.querySelectorAll('*');
  const scanLimit = Math.min(nodes.length, 8000);
  for (let i = 0; i < scanLimit; i++) {
    const el = nodes[i];
    let hay = ((el.className || '').toString() + ' ' + (el.id || '')).toLowerCase();
    for (const a of el.attributes) {
      if (a.name.toLowerCase().startsWith('data-')) hay += ' ' + (a.value || '').toLowerCase();
    }
    const txt = (el.innerText || '').slice(0, 60);
    const matched = KW.filter(k => hay.includes(k) || txt.includes(k));
    if (matched.length) {
      hits.push(Object.assign(info(el), { matched: matched }));
      if (hits.length >= 300) break;
    }
  }

  return {
    title: document.title,
    url: location.href,
    body_len: (document.body.innerText || '').length,
    dom_nodes_scanned: scanLimit,
    dom_nodes_total: nodes.length,
    anchors: all('a', 1000),
    imgs: all('img', 1000),
    buttons: all('button', 200),
    interactive: all('[role="button"],[onclick],[class*="btn"],[class*="Btn"],[class*="tab"]', 300),
    canvases: all('canvas', 50),
    iframes: all('iframe', 50),
    keyword_hits: hits,
  };
}
"""


def probe_page(page: Page, outdir: Path, tag: str) -> Path:
    """把单页现场落盘：html / 正文 / 结构化摘要 / 逐 frame 概览。

    主文档摘要用**一次 evaluate** 取回，不是逐元素 inner_text()：
    本项目有过 538 条逐条取文本撞 180s 超时的前科（见 PR #1 的 d9a9cfd）。

    frame 另走一遍 Playwright 的 locator 而不是 JS：
    JS 受同源策略限制读不到跨域 iframe 的内容，而**微信登录很可能就在跨域 iframe 里**。
    Playwright 走 CDP，不受这条限制 —— 这是它比 `document.querySelectorAll` 强的地方。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = outdir / tag

    try:
        (stamp.with_suffix(".html")).write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # 页面正在跳转时 content() 可能失败
        (stamp.with_suffix(".html")).write_text(f"(content() 失败: {exc})", encoding="utf-8")

    try:
        (stamp.with_suffix(".txt")).write_text(
            page.locator("body").inner_text(timeout=5000), encoding="utf-8"
        )
    except Exception as exc:
        (stamp.with_suffix(".txt")).write_text(f"(inner_text 失败: {exc})", encoding="utf-8")

    summary: dict = {}
    try:
        summary = page.evaluate(_PROBE_JS)
    except Exception as exc:
        summary = {"error": f"evaluate 失败: {exc}"}

    frames: list[dict] = []
    for index, frame in enumerate(page.frames):
        row: dict = {"index": index, "url": frame.url, "name": frame.name}
        try:
            row["anchor_count"] = frame.locator("a").count()
            row["img_count"] = frame.locator("img").count()
            row["button_count"] = frame.locator("button").count()
        except Exception as exc:
            row["counts_error"] = str(exc)
        try:
            row["body_head"] = frame.locator("body").inner_text(timeout=3000)[:2000]
        except Exception as exc:
            row["body_error"] = str(exc)
        frames.append(row)

    summary["frames"] = frames
    try:
        (stamp.with_suffix(".json")).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        (stamp.with_suffix(".json")).write_text(
            json.dumps({"error": str(exc)}, ensure_ascii=False), encoding="utf-8"
        )

    return outdir


def probe_context(context: BrowserContext, outdir: Path, tag: str) -> Path:
    """把 context 里**所有页面**各 dump 一份，并写一份索引。

    为什么需要：`expect_page` 有可能漏捕 popup（窗口在监听注册之前就打开了）。
    那一刻只 dump 主 page，会得出「点了没反应」的错误结论，
    而真正的授权页其实已经开在另一个 tab 里。全 context 扫一遍最省事，
    也顺便回答了「当时到底开了几个页面」这个事后很难重建的问题。
    """
    index: list[dict] = []
    for i, one in enumerate(context.pages):
        try:
            page_url = one.url
        except Exception:
            page_url = "(url 不可读)"
        sub = outdir / f"{tag}__page{i}"
        try:
            probe_page(one, sub, "page")
            index.append({"index": i, "url": page_url, "probed": True, "dir": str(sub)})
        except Exception as exc:
            index.append({"index": i, "url": page_url, "probed": False, "error": str(exc)})

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{tag}__pages_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return outdir


# 登录判定（is_logged_in）与 state 统计（state_summary）都在 utils/auth.py，
# 与 suites/auth 共用同一份判据 —— 两处各写一份必然漂移。

def wait_for_login(page: Page, context: BrowserContext, timeout_s: int) -> tuple[bool, str]:
    """轮询等真人扫码完成。每 5 秒报一次进度，让人知道脚本还活着。"""
    deadline = time.time() + timeout_s
    waited = 0
    last = ""
    while time.time() < deadline:
        logged, evidence = is_logged_in(page)
        last = evidence
        if logged:
            return True, evidence
        if waited and waited % 5 == 0:
            print(f"  …等待扫码中（已等 {waited}s / 上限 {timeout_s}s）：{evidence}")
        # 用 Playwright 的等待而不是 time.sleep：同一进程内不会阻塞事件派发
        page.wait_for_timeout(1000)
        waited += 1
    return False, f"超时 {timeout_s}s，最后一次判定：{last}"


# ---------------------------------------------------------------- 主流程

def open_login_panel(page: Page, outdir: Path) -> bool:
    """点顶部「登录」，等落点变化。返回是否已离开首页。"""
    try:
        entry = page.get_by_text(LOGIN_TEXT, exact=True).first
        if not entry.is_visible():
            raise RuntimeError("「登录」文本不可见")
    except Exception as exc:
        probe_page(page, outdir, "01_home_no_login_entry")
        print(f"✗ 首页找不到可点的「登录」入口：{exc}")
        print(f"  现场已落盘：{outdir}")
        return False

    before = page.url
    try:
        entry.click(timeout=10000)
    except Exception as exc:
        probe_page(page, outdir, "02_login_click_failed")
        print(f"✗ 点击「登录」失败：{exc}")
        print(f"  现场已落盘：{outdir}")
        return False

    # 落点可能是新页面（popup）也可能是同页跳转，两种都接受
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeout:
        pass

    moved = page.url != before
    print(f"  点击后 URL：{page.url}")
    print(f"  是否离开首页：{moved}（无论是否离开都继续，下一步按现场判断）")
    probe_page(page, outdir, "03_after_login_click")
    return True


def click_wechat_entry(page: Page, context: BrowserContext, outdir: Path) -> Page | None:
    """点微信入口，返回授权页（popup 或同页）。全失败返回 None 并落盘。"""
    for selector in WECHAT_ENTRY_CANDIDATES:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0 or not locator.is_visible():
                continue
        except Exception:
            continue

        print(f"  尝试微信入口：{selector}")
        try:
            # expect_page 必须在 click **之前**注册，否则 popup 可能在注册前就打开了
            with context.expect_page(timeout=8000) as popup_info:
                locator.click(timeout=8000)
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            print(f"  ✓ 捕获到 popup：{popup.url}")
            probe_page(popup, outdir, "04_wechat_popup")
            return popup
        except PlaywrightTimeout:
            # 没有 popup ⇒ 可能是同页跳转，也可能点了没反应；交给下一步判断
            print("  未捕获 popup（可能是同页跳转）")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except PlaywrightTimeout:
                pass
            probe_page(page, outdir, "04_wechat_same_page")
            if "passport" in page.url or "weixin" in page.url or "wechat" in page.url:
                return page
        except Exception as exc:
            print(f"  选择器 {selector} 点击异常：{exc}")
            continue

    # 用 context 级 dump 而不是单页：万一 popup 已经打开但 expect_page 漏捕，
    # 单页视角会给出「点了没反应」的错结论，而这正是最需要排除的一种情况。
    probe_context(context, outdir, "05_wechat_entry_not_found")
    print(f"✗ 微信入口候选全部未命中，现场已落盘：{outdir}")
    print("  重点看两个文件：")
    print("    · 05_wechat_entry_not_found__pages_index.json —— 当时一共开了几个页面")
    print("    · 03_after_login_click.json 的 keyword_hits —— 含「微信/weixin/qrcode」的元素")
    print("  把这两个发出来，就能坐实入口选择器。")
    return None


def handle_authorize_page(auth_page: Page, outdir: Path) -> None:
    """在授权页上尽力点「允许」。

    PC 端微信扫码授权，确认动作通常在**手机**上完成，页面上可能根本没有「允许」按钮。
    所以这里找不到按钮是**正常分支**，不算失败：把现场落下，继续等扫码结果。
    """
    for selector in ALLOW_BUTTON_CANDIDATES:
        try:
            button = auth_page.locator(selector).first
            if button.count() == 0 or not button.is_visible():
                continue
            print(f"  授权页出现「允许」，点击：{selector}")
            button.click(timeout=5000)
            return
        except Exception:
            continue

    print("  授权页没有可见的「允许」按钮 —— 按预期分支处理：请在**手机**上确认登录")
    probe_page(auth_page, outdir, "06_authorize_page")


# ---------------------------------------------------------------- 启动前检查

def check_wechat_before_start(*, skip: bool, auto_yes: bool) -> bool:
    """动手前提醒一句：如果你要用 PC 微信扫码，先把它开好。返回是否继续。

    措辞边界（见 utils/wechat.py 文件头）：检到的是「微信客户端在不在跑」，
    不是「微信有没有登录」。把前者说成后者，等于告诉用户一个我们查不到的结论 ——
    他按错误信息排查，比没有提示更糟。
    """
    if skip:
        print("  PC 微信检查：已跳过（--no-wechat-check）")
        return True

    running, evidence = detect_wechat()
    print(f"  PC 微信检查：{evidence}")
    if running:
        # 已就绪就静默通过，不打扰 —— 这时弹窗只会让人以为哪里出错
        print("  → 客户端在运行，静默继续，不弹窗")
        return True

    can, why = can_show_dialog()
    if auto_yes or not can:
        print(f"  → 未弹窗（{why}）；若打算用 PC 微信扫码，请先打开并登录微信。")
        return True

    proceed = prompt_user_login(evidence)
    print("  → 用户点「确定」，继续启动浏览器" if proceed else "  → 用户点「取消」，中止")
    return proceed


def main() -> int:
    _force_utf8_stdio()

    parser = argparse.ArgumentParser(
        description="人工扫码建立携程登录态（有头，一次），保存 storageState",
    )
    parser.add_argument("--entry", choices=tuple(ENTRY_URLS), default="www",
                        help="入口站：www（默认）或 trains")
    parser.add_argument("--timeout", type=int, default=180, help="给自己多少秒扫码，默认 180")
    parser.add_argument("--no-trains-check", action="store_true",
                        help="跳过「两站登录态是否互通」的检查")
    parser.add_argument("--no-wechat-check", action="store_true",
                        help="跳过「PC 微信是否在运行」的检查与系统弹窗")
    parser.add_argument("--yes", action="store_true",
                        help="所有确认自动通过（非交互场景用，不弹任何系统对话框）")
    parser.add_argument("--out", default=None, help="现场落盘目录（默认 reports/probe_auth/<时间戳>）")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) if args.out else settings.REPORTS_DIR / "probe_auth" / stamp
    state_path = settings.auth_state_path()

    print("=" * 68)
    print("携程登录态采集（人工扫码）")
    print(f"  入口    ：{ENTRY_URLS[args.entry]}")
    print(f"  落盘    ：{outdir}")
    print(f"  目标状态：{state_path}")
    print("  说明    ：本脚本不代填账号密码、不模拟扫码。窗口打开后请自行扫码。")
    print("=" * 68)

    print("\n[0/7] 启动前检查")
    if not check_wechat_before_start(skip=args.no_wechat_check, auto_yes=args.yes):
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            # 与用例保持一致：预拒绝原生权限框，避免弹窗挡住点击目标
            permissions=[],
        )
        install_dirty_url_guard(context)

        page = context.new_page()
        page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

        try:
            print("\n[1/7] 打开首页（已在请求层剥掉 allianceid / sid）")
            page.goto(clean_url(ENTRY_URLS[args.entry]), wait_until="domcontentloaded")
            print(f"      URL：{page.url}")
            closed = dismiss_promo_overlays(page, tag="首页")
            print(f"      营销浮层：{closed or '未发现'}")
            print(f"      登录判定：{is_logged_in(page)[1]}")

            print("\n[2/7] 点击顶部「登录」")
            if not open_login_panel(page, outdir):
                return 2

            print("\n[3/7] 寻找微信入口")
            auth_page = click_wechat_entry(page, context, outdir)
            if auth_page is None:
                return 3

            print("\n[4/7] 处理授权页")
            handle_authorize_page(auth_page, outdir)

            print(f"\n[5/7] 等待扫码完成（上限 {args.timeout}s）")
            logged, evidence = wait_for_login(page, context, args.timeout)
            print(f"      判定：{evidence}")
            if not logged:
                probe_page(page, outdir, "07_login_timeout")
                print("✗ 未检测到登录成功，未写入任何 state 文件。")
                print(f"  现场：{outdir}")
                print("  常见原因：二维码过期 / 只在手机上确认但页面没刷新 / 判据未覆盖新的头像结构。")
                return 4

            print("\n[6/7] 保存 storageState")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(state_path))
            print(f"      ✓ 已写入 {state_path}")

            data = json.loads(state_path.read_text(encoding="utf-8"))
            print("      " + state_summary(data).replace("\n", "\n      "))

            if not args.no_trains_check and args.entry == "www":
                print("\n[附] 检查火车票站是否共用这份登录态")
                try:
                    page.goto(ENTRY_URLS["trains"], wait_until="domcontentloaded")
                    dismiss_promo_overlays(page, tag="trains")
                    trains_logged, trains_evidence = is_logged_in(page)
                    print(f"      trains 登录判定：{trains_evidence}")
                    if trains_logged:
                        # 合在一份 state 里：两站 cookie 同属一个 context
                        context.storage_state(path=str(state_path))
                        print("      ✓ 两站共用同一份登录态，已合并写入")
                    else:
                        print("      ⚠️ trains 未判定为已登录。这两站的登录态可能不互通，")
                        print("         若后续登录用例跑 trains，需要单独 --entry trains 再采一次。")
                except Exception as exc:
                    print(f"      ⚠️ 检查失败（不影响已保存的 www 登录态）：{exc}")

            print("\n完成。下一步：")
            print("  HEADED=1 python -m pytest suites/auth -m auth -v")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
