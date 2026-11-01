#!/usr/bin/env python
"""建立登录态，落到 config/auth/ctrip_state.json。

只做一件事：给真人开一扇有头窗口，走一次微信授权，然后把会话存下来。
工程不代填账号密码、不模拟扫码、不碰验证码 —— 这三条任一成立，本文件就退化成
「拿凭据换通过率」的工具，与 config/settings.py 文件头的 D 决策冲突。

用法（在仓库根执行）
    python scripts/setup_auth.py                    # 默认：系统 Chrome + 自动点「允许」
    python scripts/setup_auth.py --no-auto-allow    # 最后那一下「允许」由人工点
    python scripts/setup_auth.py --entry trains     # 从火车票站入口
    python scripts/setup_auth.py --timeout 300      # 给自己 5 分钟
    python scripts/setup_auth.py --chromium         # 对照实验用（拿不到快捷登录）

⚠️ 选择器的取证状态（2026-11-01 本机实跑，全部有现场文件）
    · 顶部「登录」→ 落地 `passport.ctrip.com/user/login?BackUrl=…`          ✅
    · 必须先勾 `input#checkboxAgreementInput`。不勾就点微信，页面只弹一个
      「请先阅读并勾选协议」气泡，URL 停在 passport，看起来像"点了没反应"  ✅
    · 微信入口 `a.icon-new-wechat` → popup
      `open.weixin.qq.com/connect/qrconnect?appid=wx29cad1c2709a80bc`       ✅
    · 授权页两种形态（取决于 PC 微信客户端能否被探测到）：
        "fast" → `div.js_quick_login` + `button.js_quick_login_btn`
        "qr"   → `img.js_qrcode_img`（手机扫）
      二维码先渲染，快捷登录面板要等探测回调，**必须先给面板 15s 独占窗口** ✅
    · 必须用**系统 Chrome**：Playwright 自带 Chromium 里回环探测被切
      （`ERR_CONNECTION_CLOSED`），快捷登录永不出现                     ✅
    · 必须放行 `local-network-access` 权限：微信靠它探测本机客户端        ✅
    · 点「微信快捷登录」后，微信客户端弹一个 468×334 的 Qt 顶层窗口
      「携程旅行网 申请使用 / 你的昵称、头像 / 允许 拒绝」。
      该窗口 `EnumChildWindows`=0、UIA 连续 6 轮 `GetChildren()`=0，
      拿不到控件树 ⇒ 只能按品牌绿像素定位「允许」再点                     ✅

第一条（登录入口）和第一条之后的一切，都是本轮实测出来的；任何一步失败仍会把
现场落盘到 `reports/probe_auth/<时间戳>/`，据此再精确化 —— 这是「先落证据再改选择器」，
不是「先猜一个能跑的」。
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

# 必须在取窗口坐标/截图之前声明 DPI 感知：否则 GetWindowRect 与 ImageGrab
# 分处"逻辑像素/物理像素"两套坐标系，高分屏上坐标会整体偏移。
try:
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

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

# 登录入口。`登录` 在截图里确认存在于 www 顶部导航（2026-11-01 用户截图）。
# trains 站已实测同样能落到 passport（2026-11-01 本机跑）。
LOGIN_TEXT = "登录"

# ---------------------------------------------------------------- 实测选择器
# 以下三条全部来自 2026-11-01 本机实跑取证，不再是推测：
#   reports/probe_auth/agent-run-20261101-1705/  （登录页 DOM）
#   reports/probe_login/20261101-170429/          （勾选协议 + 微信授权页）

# 协议勾选框。**必须先勾**，否则点微信只会弹「请先阅读并勾选协议」气泡，
# 页面一动不动 —— 这正是旧版脚本拿到「点了没反应」假结论的原因。
AGREE_INPUT = "input#checkboxAgreementInput"
AGREE_TIP = "div[data-testid='agreementTip']"

# 微信入口。实测 `<a class="icon-new-wechat" data-testid="commonEntry">微信</a>`，
# 是登录框底部那排无文字圆形图标里最左的绿色一个。
# 第一条为实测值；后两条保留作站点改版后的降级，命中即会在日志里打出来。
WECHAT_ENTRY_CANDIDATES: tuple[str, ...] = (
    "a.icon-new-wechat",
    "[class*='icon-new-wechat']",
    "text=微信",
)

# 微信授权页的域名特征。点微信**必然**开 popup 到 open.weixin.qq.com
# （实测 appid=wx29cad1c2709a80bc，scope=snsapi_login）。
WECHAT_AUTH_HOST = "open.weixin.qq.com"

# 授权页上的二维码。实测 `<img class="js_qrcode_img web_qrcode_img" src="/connect/qrcode/xxx">`，160×160。
WECHAT_QR_CANDIDATES: tuple[str, ...] = (
    "img.js_qrcode_img",
    "img.web_qrcode_img",
    "img[src*='/connect/qrcode']",
)

# 「允许」按钮：**实测确认不存在**。微信开放平台 snsapi_login 扫码页只有
# 「使用微信扫一扫登录 / 「携程旅行网」 / 二维码」三样，确认动作全在手机上。
# 保留空元组是为了让 handle_authorize_page 的语义显式化 —— 不是忘了找，是查过没有。
ALLOW_BUTTON_CANDIDATES: tuple[str, ...] = ()

# 「微信快捷登录」面板（PC 微信已登录时才出现）。实测：
#   div.js_quick_login         178×180，含头像 + 昵称
#   button.js_quick_login_btn  132×30，绿色，文本「微信快捷登录」
# 点它之后**微信客户端会弹自己的原生授权框**（携程旅行网 申请使用 你的昵称、头像），
# 那个框在浏览器之外，Playwright 点不到 —— 必须由人点「允许」。
WECHAT_FAST_PANEL = ".js_quick_login"
WECHAT_FAST_BTN = ".js_quick_login_btn"

# 登录态采集需要的浏览器权限。
# local-network-access：微信页面用它探测本机微信客户端；
# 缺了这条，PC 微信明明在跑也只会给二维码（2026-11-01 实测）。
AUTH_PERMISSIONS: tuple[str, ...] = ("local-network-access",)

# 优先等快捷登录面板的时长。二维码几乎立刻就在，快捷登录要等本机客户端探测回调，
# 所以必须先给它一个独占窗口，否则会被二维码抢先判定（实测踩过，见 wait_wechat_ready）。
FAST_PANEL_WAIT_S = 15

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

    # 截图单独一档：DOM/JSON 能说明结构，但「元素在不在视口里、有没有被浮层盖住」
    # 只有像素能回答。视口截图必留，全页截图尽力而为（超长页会失败）。
    try:
        page.screenshot(path=str(stamp.with_suffix(".png")), timeout=10000)
    except Exception as exc:
        (stamp.with_suffix(".png.error.txt")).write_text(
            f"(截图失败: {exc})", encoding="utf-8"
        )
    try:
        page.screenshot(path=str(outdir / f"{tag}__fullpage.png"), full_page=True,
                        timeout=15000)
    except Exception:
        pass  # 全页截图是加分项，失败不影响主证据

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

def wait_for_login(
    page: Page,
    context: BrowserContext,
    timeout_s: int,
    popup: Page | None = None,
) -> tuple[bool, str]:
    """轮询等真人扫码完成。每 5 秒报一次进度，让人知道脚本还活着。

    `popup` 是微信授权页。它自己关掉是「手机确认完成」最硬的信号 ——
    那一刻主页面通常还停在 passport、尚未刷新，所以一旦观察到关闭，
    主动回 www 复核一次，而不是干等主页面自己跳转（实测它不一定跳）。
    """
    deadline = time.time() + timeout_s
    waited = 0
    last = ""
    rechecked = False

    while time.time() < deadline:
        logged, evidence = is_logged_in(page)
        last = evidence
        if logged:
            return True, evidence

        if popup is not None and not rechecked:
            try:
                closed = popup.is_closed()
            except Exception:
                closed = False
            if closed:
                rechecked = True
                print("  …授权页已关闭（手机侧确认完成），回 www 复核登录态")
                try:
                    page.goto(clean_url(ENTRY_URLS["www"]), wait_until="domcontentloaded")
                    dismiss_promo_overlays(page, tag="复核")
                except Exception as exc:
                    print(f"  ⚠️ 复核导航失败：{exc}")
                continue

        if waited and waited % 5 == 0:
            print(f"  …等待扫码中（已等 {waited}s / 上限 {timeout_s}s）：{evidence}")
        # 用 Playwright 的等待而不是 time.sleep：同一进程内不会阻塞事件派发。
        # 但授权完成的瞬间页面可能在导航/关闭，这时 wait_for_timeout 会抛
        # TargetClosedError —— 那不是失败，是流程正在切换，下一轮继续判定即可。
        try:
            page.wait_for_timeout(1000)
        except Exception as exc:
            print(f"  …等待期间页面状态变化（{type(exc).__name__}），继续判定")
            time.sleep(0.5)
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


def check_agreement(page: Page, outdir: Path) -> bool:
    """勾选「阅读并同意服务协议」。这一步是点微信的前置闸门。

    实测（2026-11-01）：不勾就点微信，页面只弹一个
    `div[data-testid="agreementTip"]`「请先阅读并勾选协议」气泡，
    既不切面板也不开 popup —— 而 URL 停在 passport，看起来"什么都没发生"。

    为什么必须 force=True：`input#checkboxAgreementInput` 被 `<label>` 覆盖，
    常规 check() 会等可交互性等到超时（实测 4s 抛 TimeoutError）。
    force 跳过该检查直接派发点击，且**随后回读 is_checked()** ——
    不读回就容易把"点了但没勾上"记成成功。
    """
    try:
        if page.locator(AGREE_INPUT).count() == 0:
            print("  ⚠️ 未找到协议勾选框 —— 站点可能改了版式，继续往下走但别当已勾选")
            probe_page(page, outdir, "03a_no_agree_checkbox")
            return False
    except Exception as exc:
        print(f"  ⚠️ 协议勾选框探测失败：{exc}")
        return False

    for how, action in (
        ("check(force)", lambda: page.locator(AGREE_INPUT).check(timeout=4000, force=True)),
        ("click", lambda: page.locator(AGREE_INPUT).click(timeout=4000, force=True)),
    ):
        try:
            action()
        except Exception as exc:
            print(f"  协议勾选 {how} 失败：{type(exc).__name__}")
            continue
        if page.locator(AGREE_INPUT).is_checked():
            print(f"  ✓ 协议已勾选（{how}，is_checked=True 已回读）")
            return True

    print("  ✗ 协议勾选失败：is_checked 仍为 False")
    probe_page(page, outdir, "03b_agree_check_failed")
    return False


def _fast_panel_ready(auth_page: Page) -> bool:
    """快捷登录面板是否已经真正渲染出来。

    判据用**边界框**而不是 is_visible()：面板未激活时 `display:none` 与 `0×0`
    两种状态都出现过，只看 is_visible 会给出假阳性。
    """
    try:
        if auth_page.locator(WECHAT_FAST_PANEL).count() == 0:
            return False
        box = auth_page.locator(WECHAT_FAST_PANEL).first.bounding_box()
        if not box or box["width"] < 40 or box["height"] < 40:
            return False
        print(f"  ✓ 快捷登录面板已出现（{int(box['width'])}×{int(box['height'])}）")
        return True
    except Exception:
        return False


def wait_wechat_ready(auth_page: Page, outdir: Path, timeout_s: int = 40) -> str:
    """等授权页进入可操作状态。返回 "fast" / "qr" / "none"。

    **两阶段等待**，顺序不能反（2026-11-01 实测踩过）：
        第一阶段（前 15s）只认「微信快捷登录」。二维码在页面骨架一出来就渲染好了，
        而快捷登录面板要等微信客户端探测成功的回调才把 display:none 改掉 ——
        如果这一阶段就把二维码当成结论返回，会在探测成功前 1 秒退出，
        日志写成「只有二维码」，看起来像站点/浏览器问题，其实只是等短了。
        第二阶段（剩余时间）才接受二维码，两个都持续检查。

    两种实测形态（同一 appid，取决于 PC 微信客户端能否被探测到）：
        "fast" → div.js_quick_login 178×180（含头像、昵称）+ 绿色按钮「微信快捷登录」
        "qr"   → img.js_qrcode_img 160×160（手机扫码）
    """
    started = time.time()
    fast_deadline = started + min(FAST_PANEL_WAIT_S, timeout_s)
    deadline = started + timeout_s

    # 阶段一：优先等快捷登录
    while time.time() < fast_deadline:
        if _fast_panel_ready(auth_page):
            return "fast"
        auth_page.wait_for_timeout(500)

    print(f"  …{FAST_PANEL_WAIT_S}s 内未见快捷登录面板（PC 微信可能未被探测到），改看二维码")

    # 阶段二：接受二维码（快捷登录若此刻才出现也照样认）
    while time.time() < deadline:
        if _fast_panel_ready(auth_page):
            print("  ✓ 快捷登录面板在第二阶段出现")
            return "fast"
        for selector in WECHAT_QR_CANDIDATES:
            try:
                locator = auth_page.locator(selector).first
                if locator.count() == 0 or not locator.is_visible():
                    continue
                box = locator.bounding_box()
                # 只有宽度够大才算真二维码：0 宽元素 is_visible 也可能为 True
                if box and box["width"] >= 40:
                    print(f"  ✓ 二维码已渲染：{selector} {int(box['width'])}×{int(box['height'])}")
                    return "qr"
            except Exception:
                continue
        auth_page.wait_for_timeout(500)

    print(f"  ⚠️ 等了 {timeout_s}s，既没有快捷登录面板也没有二维码")
    probe_page(auth_page, outdir, "05_auth_not_ready")
    return "none"


# ---------------------------------------------------------- 微信确认框（客户端侧）

def _qt_windows() -> list[dict]:
    """枚举可见的 Qt 顶层窗口。微信 4.x 的主窗口、托盘、授权确认框都是这个类名。"""
    import ctypes.wintypes as wt

    u = ctypes.windll.user32
    rows: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        if not cls.value.startswith("Qt"):
            return True
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rows.append({"hwnd": int(h), "class": cls.value, "x": r.left, "y": r.top,
                     "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    u.EnumWindows(cb, 0)
    return rows


def _find_green_box(img) -> tuple[tuple[int, int, int, int] | None, int]:
    """返回品牌绿像素的包围盒与数量。确认框里只有「允许」是绿的。"""
    w, h = img.size
    px = img.load()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            try:
                r, g, b = px[x, y][:3]
            except Exception:
                continue
            if g > 140 and r < 110 and b < 140 and (g - r) > 60 and (g - b) > 40:
                xs.append(x)
                ys.append(y)
    if len(xs) < 30:
        return None, len(xs)
    return (min(xs), min(ys), max(xs), max(ys)), len(xs)


def _green_box_stable(img, tries: int = 6) -> tuple[tuple[int, int, int, int] | None, int]:
    """反复取绿块，直到连续两次位置一致 —— 用「位置稳定」代替固定等待。

    为什么需要（2026-11-01 失败现场）：微信确认框有淡入动画，窗口刚出现时
    截到的是动画中间态，绿块位置还在移动。按那一刻算出的坐标点下去，
    正好落在按钮外 —— 表现就是「点了但授权框还杵在那」，
    而日志会写成「已点击」，看起来一切正常。
    """
    last = None
    stable = 0
    count = 0
    for _ in range(tries):
        box, count = _find_green_box(img)
        if box is None:
            time.sleep(0.25)
            continue
        if last is not None and box == last:
            stable += 1
            if stable >= 2:
                return box, count
        else:
            stable = 0
        last = box
        time.sleep(0.25)
    return last, count


def auto_allow_wechat(
    before: set[int], outdir: Path, timeout_s: int = 15, attempts: int = 3
) -> tuple[bool, str]:
    """在微信确认框上点【允许】。返回 (是否点成功, 说明)。

    为什么是像素而不是 UIA（2026-11-01 实测，两条都试过）：
        该窗口 `EnumChildWindows` 返回 0；`uiautomation` 连续 6 轮 `GetChildren()`
        也全是 0 —— 这个版本的微信没有对 UIA 暴露控件树，拿不到
        `Button(Name='允许')`。所以只能落到坐标点击。

    为什么不按比例估坐标：换 DPI / 换字号就偏，可能点到「拒绝」。
    按品牌绿 #07C160 找像素簇，再**先检查簇的尺寸合理**才点 ——
    实测按钮簇是 189×37，图标/噪点会明显偏小。

    为什么点完要回读（2026-11-01 补）：首版点一次就 return True，
    于是「点了没中」被记成成功，后续等扫码等到超时，日志看起来像站点问题。
    现在每轮点完都验证确认框是否真的消失，没消失就**重新定位**再点
    （窗口可能移动，不能复用上一轮坐标）。最多 attempts 轮，不做无限重试。

    风险边界：这是一次系统级鼠标点击，会短暂接管光标。点偏最坏结果是授权被拒、
    重跑一次即可；本函数在找不到可靠目标时不硬点。
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return False, "Pillow 未安装，无法定位按钮（pip install pillow），请人工点【允许】"

    import ctypes.wintypes as wt

    u = ctypes.windll.user32
    target = None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        fresh = [w for w in _qt_windows()
                 if w["hwnd"] not in before and w["w"] > 250 and w["h"] > 150]
        if fresh:
            target = fresh[0]
            break
        time.sleep(0.8)

    if target is None:
        return False, f"{timeout_s}s 内没等到微信确认窗口，请人工点【允许】"

    print(f"  微信确认窗口：{target['w']}×{target['h']} at ({target['x']},{target['y']})")
    time.sleep(0.5)  # 给淡入动画一点起步时间，稳定性靠下面的位置复核保证

    outdir.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, attempts + 1):
        # 每轮都重新取窗口矩形：窗口可能被移动过，坐标不能跨轮复用
        rect = wt.RECT()
        try:
            u.GetWindowRect(target["hwnd"], ctypes.byref(rect))
            tx, ty = rect.left, rect.top
            tw, th = rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            tx, ty, tw, th = target["x"], target["y"], target["w"], target["h"]

        bbox = (tx, ty, tx + tw, ty + th)
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception as exc:
            return False, f"截图失败：{exc}，请人工点【允许】"

        try:
            img.save(outdir / f"09_wechat_confirm_{attempt}.png")
        except Exception:
            pass

        box, count = _green_box_stable(img)
        if box is None:
            return False, f"只找到 {count} 个绿色像素，无法定位按钮，请人工点【允许】"

        x0, y0, x1, y1 = box
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if not (60 <= bw <= tw and 15 <= bh <= 90):
            return False, f"绿块尺寸 {bw}×{bh} 不像按钮，拒绝盲点，请人工点【允许】"

        cx = tx + (x0 + x1) // 2
        cy = ty + (y0 + y1) // 2
        print(f"  第 {attempt} 轮：绿块 {bw}×{bh}，目标点 ({cx},{cy})")

        try:
            u.SetForegroundWindow(target["hwnd"])
        except Exception:
            pass
        # 先进窗口中心再移到按钮：Qt 需要一次 pointerenter 才把窗口当作「鼠标在内」，
        # 直接从窗口外跳到按钮上，个别版本会丢掉这次 click。
        u.SetCursorPos(tx + tw // 2, ty + th // 2)
        time.sleep(0.15)
        u.SetCursorPos(cx, cy)
        time.sleep(0.2)
        u.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.08)
        u.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP

        # 回读：确认框消失才算点中。IsWindow 为 0 或已不可见都算。
        settled = False
        for _ in range(10):
            time.sleep(0.3)
            if not u.IsWindow(target["hwnd"]) or not u.IsWindowVisible(target["hwnd"]):
                settled = True
                break
        if settled:
            return True, f"已按品牌绿定位并点击 ({cx},{cy})，确认框已关闭（第 {attempt} 轮）"

        print("    点击后确认框仍在 —— 重新定位再试")

    return False, f"连续 {attempts} 轮点击后确认框仍未关闭，请人工点【允许】"


def click_fast_login(auth_page: Page, outdir: Path, auto_allow: bool = True) -> bool:
    """点「微信快捷登录」，然后尝试自动点微信客户端的【允许】。

    第二步落在浏览器之外：微信客户端会弹一个 468×334 的 Qt 窗口，内容是
    「携程旅行网 申请使用 / 你的昵称、头像 / [允许] [拒绝]」。
    """
    before = {w["hwnd"] for w in _qt_windows()}

    try:
        button = auth_page.locator(WECHAT_FAST_BTN).first
        if button.count() == 0:
            print("  ⚠️ 找不到 js_quick_login_btn")
            return False
        button.click(timeout=8000)
    except Exception as exc:
        print(f"  ⚠️ 点击「微信快捷登录」失败：{type(exc).__name__}: {exc}")
        probe_page(auth_page, outdir, "05_fast_click_failed")
        return False

    print("  ✓ 已点击「微信快捷登录」")
    probe_page(auth_page, outdir, "05_after_fast_click")

    if not auto_allow:
        print("  → --no-auto-allow：请到微信客户端窗口上点【允许】")
        return True

    ok, why = auto_allow_wechat(before, outdir)
    if ok:
        print(f"  ✓ {why}")
        print("    （这是一次系统级鼠标点击，光标会被短暂接管）")
    else:
        print(f"  ⚠️ 自动点【允许】未完成：{why}")
        print("     手动点一下即可，脚本会继续等")
    return True


def click_wechat_entry(page: Page, context: BrowserContext, outdir: Path) -> Page | None:
    """点微信入口，返回**已渲染出二维码**的授权页。全失败返回 None 并落盘。

    这里修掉了旧版一个假成功（2026-11-01 实测暴露）：
        旧判据 `if "passport" in page.url` 恒为真 —— 因为点「登录」之后
        URL 本来就在 passport 上，所以"没开 popup"也会被当成"同页授权页"返回。
        结果 [4/7] 对着登录页找「允许」按钮，[5/7] 对着登录页等扫码，全程空转。
    新判据只认两件事：**popup 落在 open.weixin.qq.com**，或**同页 URL 真的变了且含微信域**。
    """
    before_url = page.url

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
            with context.expect_page(timeout=12000) as popup_info:
                locator.click(timeout=8000)
            popup = popup_info.value
        except PlaywrightTimeout:
            moved = page.url != before_url
            print(f"  未捕获 popup（同页 URL 是否变化：{moved}）")
            probe_page(page, outdir, "04_wechat_no_popup")
            if moved and WECHAT_AUTH_HOST in page.url:
                print(f"  ✓ 同页跳转到微信域：{page.url[:100]}")
                return page
            if not moved:
                print("  ⚠️ 点击后页面毫无变化 —— 最可能是协议没勾上（会弹「请先阅读并勾选协议」）")
            continue
        except Exception as exc:
            print(f"  选择器 {selector} 点击异常：{type(exc).__name__}: {exc}")
            continue

        try:
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightTimeout:
            pass

        print(f"  ✓ 捕获到 popup：{popup.url[:120]}")
        if WECHAT_AUTH_HOST not in popup.url:
            print(f"  ⚠️ popup 不在微信授权域（期望含 {WECHAT_AUTH_HOST}）")
            probe_page(popup, outdir, "04_popup_unexpected_host")

        # 就绪判定（二维码 or 快捷登录面板）交给 handle_authorize_page：
        # 它需要在判定之后立刻决定「点按钮」还是「提示手机扫码」，放一起才不留缝。
        return popup

    # 用 context 级 dump 而不是单页：万一 popup 已经打开但 expect_page 漏捕，
    # 单页视角会给出「点了没反应」的错结论，而这正是最需要排除的一种情况。
    probe_context(context, outdir, "05_wechat_entry_not_found")
    print(f"✗ 微信入口候选全部未命中，现场已落盘：{outdir}")
    print("  两处优先看：")
    print("    · 04_wechat_no_popup.png —— 点击后长什么样（有没有协议提示气泡）")
    print("    · 05_wechat_entry_not_found__pages_index.json —— 当时开了几个页面")
    return None


def handle_authorize_page(auth_page: Page, outdir: Path, auto_allow: bool = True) -> str:
    """判定授权页形态并做对应动作，返回 "fast" / "qr" / "none"。

    2026-11-01 实测两条路径：
      fast → 面板含头像 + 绿色按钮「微信快捷登录」（PC 微信已登录时）；
             点它之后**微信客户端自己弹原生授权框**，要人点「允许」。
      qr   → 只有二维码，确认动作在手机上，网页侧只等 redirect。

    微信开放平台 snsapi_login 授权页**没有**「允许」按钮（已实测），
    所以这里不再"尽力找按钮" —— 那会凭空造出一个不存在的步骤。
    """
    mode = wait_wechat_ready(auth_page, outdir)

    if mode == "fast":
        click_fast_login(auth_page, outdir, auto_allow=auto_allow)
    elif mode == "qr":
        print("  → 只有二维码（PC 微信未被探测到）：请用**手机微信**扫码并确认")
        print("     想走一键登录：确认微信客户端已登录，且浏览器是系统 Chrome")
    else:
        print("  → 两种形态都没等到，先看现场再判断")
        print("     · 05_auth_not_ready.png")
        print("     若本机微信确实在运行，检查是否误加了 --chromium（实测拿不到快捷登录）")

    # 授权成功时 popup 会立刻关闭，此刻再 probe 必抛 TargetClosedError。
    # 这不是失败，恰恰是流程走完了 —— 所以单独兜住，不往上抛。
    try:
        probe_page(auth_page, outdir, "06_authorize_page")
    except Exception as exc:
        print(f"  （授权页已关闭，跳过落盘：{type(exc).__name__}）")
    return mode


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


def launch_browser(pw, *, force_chromium: bool = False):
    """启动浏览器。默认用**系统 Chrome**，这是实测必需，不是偏好。

    2026-11-01 对照实验（同一台机器、同一 URL、同一套代码，唯一变量是浏览器二进制）：

        系统 Chrome 156.0.8078.79     → 「微信快捷登录」0.1s 出现，拿到头像昵称
        Playwright Chromium 156.0.8078.4 → 永远 display:none，只有二维码

    差异落在网络层，抓包证据：
        微信页面会 POST 六个回环端口 /api/check-login
        （localhost.weixin.qq.com:14013/14014/14015/13013/13014/13015）
        Playwright 里 14013 报 net::ERR_CONNECTION_CLOSED（连接被切断）
        系统 Chrome 里 14013 无任何失败记录（成功）
        而 `curl -k -X POST https://localhost.weixin.qq.com:14013/api/check-login`
        返回 HTTP 200 —— 所以不是站点、不是网络、不是证书，是浏览器构建本身。

    **这不是反检测**：不加 stealth、不改 UA、不动 navigator.webdriver，
    也不碰日常浏览数据（Playwright 为它开的是临时 profile）。
    拿不到快捷登录时自动退化为二维码路径，功能不受影响。
    """
    args: dict = {"headless": False, "args": list(settings.BROWSER_UI_ARGS)}

    if not force_chromium:
        try:
            browser = pw.chromium.launch(channel="chrome", **args)
            print(f"      浏览器：系统 Chrome {browser.version}（快捷登录可用）")
            return browser
        except Exception as exc:
            print(f"      ⚠️ 系统 Chrome 启动失败（{type(exc).__name__}），退回 Playwright Chromium")
            print("         代价：拿不到「微信快捷登录」，只能走二维码（需手机扫）")

    browser = pw.chromium.launch(**args)
    print(f"      浏览器：Playwright Chromium {browser.version}")
    return browser


def _branch_exit(args, code: int) -> int:
    """「没走到底」的已知分支统一出口。

    默认保留各自的非 0 码：人工建登录态时，非 0 才是正确信号。
    `--diagnose` 下归 0 —— 该模式的目的是取证不是验收。
    注意区分：这只收敛**已知分支**，真异常照旧抛出 —— 把异常也吞成 0
    就等于「跑挂了也说成功」，那正是这个项目反复在防的事。
    """
    if getattr(args, "diagnose", False):
        print(f"  （--diagnose：分支码 {code} 按 0 收尾，现场已落盘）")
        return 0
    return code


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
    parser.add_argument("--no-auto-allow", action="store_true",
                        help="不自动点微信客户端的【允许】（改为人工点）。"
                             "自动模式是一次系统级鼠标点击，会短暂接管光标")
    parser.add_argument("--chromium", action="store_true",
                        help="强制用 Playwright 自带 Chromium（实测拿不到「微信快捷登录」，"
                             "只能走二维码；只在做对照实验时用）")
    parser.add_argument("--diagnose", action="store_true",
                        help="取证模式：把「找不到登录入口 / 微信入口不中 / 扫码超时」三个"
                             "已知分支一律按 exit 0 收尾（现场照常落盘）。只用于自动化探测，"
                             "人工建登录态时不要开 —— 开了就看不出到底哪步没成。")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) if args.out else settings.REPORTS_DIR / "probe_auth" / stamp
    state_path = settings.auth_state_path(args.entry)

    print("=" * 68)
    print("携程登录态采集（人工扫码）")
    print(f"  入口    ：{ENTRY_URLS[args.entry]}")
    print(f"  落盘    ：{outdir}")
    print(f"  目标状态：{state_path}")
    print("  说明    ：本脚本不代填账号密码、不模拟扫码。窗口打开后请自行扫码。")
    print("=" * 68)

    print("\n[0/8] 启动前检查")
    if not check_wechat_before_start(skip=args.no_wechat_check, auto_yes=args.yes):
        return 1

    with sync_playwright() as pw:
        browser = launch_browser(pw, force_chromium=args.chromium)
        try:
            context = browser.new_context(
                viewport=settings.VIEWPORT,
                locale=settings.LOCALE,
                timezone_id=settings.TIMEZONE_ID,
                # 与用例的不同点：这里**必须**放行 local-network-access。
                # 微信页面靠它探测本机客户端（POST localhost.weixin.qq.com:14013/api/check-login），
                # 拿不到就永远只给二维码，不会出现「微信快捷登录」。
                permissions=list(AUTH_PERMISSIONS),
            )
        except Exception as exc:
            print(f"      ⚠️ 权限声明失败（{type(exc).__name__}），改为空权限：{exc}")
            context = browser.new_context(
                viewport=settings.VIEWPORT,
                locale=settings.LOCALE,
                timezone_id=settings.TIMEZONE_ID,
                permissions=[],
            )
        # 刻意**不注册全局 route**（2026-11-01 实测结论）：
        # `context.route("**/*", ...)` 会让每个请求都改由 Playwright 的网络栈转发，
        # 而微信页面要 POST 到 localhost.weixin.qq.com:14013 探测本机客户端 ——
        # 这类回环请求最经不起中间层改动。首屏 URL 已由 clean_url() 在 goto 时剥干净，
        # 实测不注册 route 时落地 URL 同样没有 allianceid，所以这个拦截器在这里没有收益。

        page = context.new_page()
        page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

        try:
            print("\n[1/8] 打开首页（已在请求层剥掉 allianceid / sid）")
            page.goto(clean_url(ENTRY_URLS[args.entry]), wait_until="domcontentloaded")
            print(f"      URL：{page.url}")
            closed = dismiss_promo_overlays(page, tag="首页")
            print(f"      营销浮层：{closed or '未发现'}")
            print(f"      登录判定：{is_logged_in(page)[1]}")

            print("\n[2/8] 点击顶部「登录」")
            if not open_login_panel(page, outdir):
                return _branch_exit(args, 2)

            print("\n[3/8] 勾选「阅读并同意服务协议」（不勾则点微信无效，实测）")
            if not check_agreement(page, outdir):
                print("  ⚠️ 继续往下走，但点微信很可能只弹出协议提示气泡")

            print("\n[4/8] 点微信入口，捕获授权页")
            auth_page = click_wechat_entry(page, context, outdir)
            if auth_page is None:
                return _branch_exit(args, 3)

            print("\n[5/8] 授权页检查")
            mode = handle_authorize_page(auth_page, outdir, auto_allow=not args.no_auto_allow)

            print(f"\n[6/8] 等待确认完成（上限 {args.timeout}s）")
            if mode == "fast":
                print("      预期动作：在**微信客户端**的授权框上点【允许】")
            elif mode == "qr":
                print("      预期动作：用**手机微信**扫上面的二维码")
            # 授权页若是独立 popup 就传进去：它关闭 = 确认完成的硬信号。
            # 同页跳转的情况传 None，避免把主 page 当成 popup 判断关闭。
            popup = auth_page if auth_page is not page else None
            logged, evidence = wait_for_login(page, context, args.timeout, popup=popup)
            print(f"      判定：{evidence}")
            if not logged:
                probe_page(page, outdir, "07_login_timeout")
                # 超时那一刻很可能有第二个页面（授权 popup）开着，
                # 只 dump 主 page 会丢掉最关键的现场，所以整个 context 再扫一遍。
                probe_context(context, outdir, "08_timeout_context")
                print("✗ 未检测到登录成功，未写入任何 state 文件。")
                print(f"  现场：{outdir}")
                print("  常见原因：二维码过期 / 只在手机上确认但页面没刷新 / 判据未覆盖新的头像结构。")
                return _branch_exit(args, 4)

            print("\n[7/8] 保存 storageState")
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
