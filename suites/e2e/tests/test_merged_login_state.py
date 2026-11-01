"""登录态自检：**本轮 e2e 的结论成不成立，取决于这一条**。

## 为什么需要它（不是形式主义）

e2e 那 12 条用例断言的是「填表 → 提交 → 结果列表出数据」。这些**匿名态也能过**：
2026-11-01 之前，匿名态跑同一套就是绿的。所以「12 passed」本身**不能**证明
「登录态下也能搜」——它只证明「有 12 次搜索拿到了结果」。区别在于：

    登录态 → 提交后进 hotels/list，出卡片
    匿名态 → 提交后被重定向到 passport 登录墙，也可能在首页就拿到某些结果

两者的报告长得一模一样。这条用例就是把「当时到底登录没登录」从**假设**变成
**报告里的一行字**：跑 `pytest -m e2e` 的人不必再翻日志找 `cookies=39（跨站合并=是）`。

## 它断言什么，不断言什么

断言：合并后的会话在 www / flights / trains **三站都判已登录**（判据复用
`utils/auth.wait_logged_in`，与 `suites/auth` 同源，不另立一套）。
不断言：具体的用户名、头像、任何业务字段 —— 那属于 `suites/auth` 的职责，
抄一份只会让两边日后各自漂移。

## 判失败而不是 skip / xfail 的理由

状态文件缺失 ⇒ `conftest._require_auth_state` 已经把整条轨道 skip 掉，轮不到本条。
所以本条能跑到这里，说明**文件在**；此时若判「未登录」，只有两种可能：

  1. cookie 过期（真人扫码的会话有寿命，K-07 记录过）；
  2. 合并逻辑坏了（`trains` 那份没补进 `trains.ctrip.com` 域名）。

两种都必须让人看见 —— 前者要重新扫码，后者是代码缺陷。转成 xfail 会让它变成
一片黄色，而「黄色没人看」正是这个项目反复踩过的坑（见 docs/BLOCKED-GATE.md）。

## 位置

文件名 `test_merged_login_state.py` 排在 `test_search_chain.py` 之前，pytest 按文件名
顺序执行，所以它**天然跑在十二次搜索之前**：登录态不成立时，第一眼看到的就是
它，而不是等 300 秒后从某条搜索的红里猜原因。
"""

from __future__ import annotations

import datetime as dt
import json
import time

import allure
import pytest

from config import settings
from pages.base_page import BasePage
from utils.auth import looks_like_login_wall, wait_logged_in
from utils.waf import check_waf_block

pytestmark = [pytest.mark.e2e]

# 顺序有意：www 是酒店表单所在站（主会话来源）→ flights 验证主会话可用 →
# trains 验证「专属域 cookie 补进来了」。判别项放最后，前面两站的事实会先落进报告，
# 真红了也能立刻看出「是整体过期，还是只有合并那一步没生效」。
SITES: tuple[tuple[str, str], ...] = (
    ("酒店 / 主站（www）", "www"),
    ("机票（flights）", "flights"),
    ("火车票（trains）", "trains"),
)


def _site_url(entry: str) -> str:
    """站点根地址。**走 settings 的解析函数**，不在这里另抄一份域名。

    另抄一份的后果很具体：`FLIGHT_BASE_URL` 改了环境变量，自检还指着旧域名，
    于是自检说「已登录」而真实用例打的是另一个站 —— 自检与用例脱节比没有自检更糟。
    """
    if entry == "www":
        return settings.hotel_base_url()
    if entry == "flights":
        return settings.flight_base_url()
    return settings.base_url()          # trains


def _state_facts() -> str:
    """两份登录态文件的老化程度。**只写进报告，不参与断言**。

    会话寿命是站点下发的（K-07），拿它判失败会造出与代码无关的红；
    但「这份 state 是 3 天前扫的」这种事实必须留痕 —— 它决定了失败时该先怀疑什么。
    """
    lines: list[str] = []
    for entry, label in (("www", "主站"), ("trains", "火车票")):
        path = settings.auth_state_path(entry)
        if not path.is_file():
            lines.append(f"{label}：缺失（{path.name}）⇒ 该站必然判未登录")
            continue
        age_h = (time.time() - path.stat().st_mtime) / 3600
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cookies = data.get("cookies") or []
            session_cookies = sum(1 for c in cookies if c.get("expires", -1) in (-1, None))
        except Exception as exc:                  # noqa: BLE001 - 报告用途，读坏不该红
            lines.append(f"{label}：{path.name} 读取失败（{type(exc).__name__}）")
            continue
        lines.append(
            f"{label}：{path.name}｜cookies={len(cookies)}（会话级 {session_cookies}）"
            f"｜采集于 {age_h:.1f} 小时前"
            f"（{dt.datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M}）"
        )
    return "\n".join(lines)


@allure.story("登录态自检：合并会话在三站的登录有效性")
@allure.title("登录态自检：www / flights / trains 三站均应已登录")
@allure.severity(allure.severity_level.CRITICAL)
def test_merged_state_logged_in_on_all_sites(shared_page, shared_session, request):
    """三站逐个判登录，全部通过才继续；任一站判否即失败并给出可执行的下一步。"""
    rows: list[str] = []
    failed: list[str] = []

    for label, entry in SITES:
        url = _site_url(entry)
        with allure.step(f"打开 {label}：{url}"):
            shared_page.goto(url, wait_until="domcontentloaded", timeout=settings.NAV_TIMEOUT_MS)

        # 被对方拦下时，**任何登录判定都不成立**（拿不到 header，也拿不到用户名）。
        # 先判 WAF，避免把「被拦」误读成「掉登录」—— 这两种红的处置完全不同。
        signal = check_waf_block(shared_page)
        if signal is not None:
            shots = [str(p) for p in BasePage(shared_page).capture(
                request.node.nodeid + "__login_state")]
            request.node.add_report_section(
                "call",
                "WAF 守卫",
                f"信号：{signal}\nurl：{shared_page.url}\n截图：{', '.join(shots) or '(截图失败)'}",
            )
            pytest.xfail(f"blocked: {signal.kind}（{signal.detail}）—— 无从判断登录态")

        with allure.step(f"判定登录状态（{label}）"):
            # 用 wait_logged_in 而不是 is_logged_in：www 首页的 header 是 JS 水合，
            # 一次性判定会把已登录判成未登录（K-08 的成因，实测过一次偶发红）。
            # 这里不再「等失败后再判一次」——那是拿运气换绿，且第一次等待里
            # wait_logged_in 自己已经逐个候选选择器试过了。
            ok, evidence = wait_logged_in(shared_page, timeout_ms=settings.EXPECT_TIMEOUT_MS + 3000)
            wall = looks_like_login_wall(shared_page.url)

        rows.append(
            f"{'✅' if ok else '❌'} {label}\n"
            f"    url      : {shared_page.url}\n"
            f"    判据证据 : {evidence}\n"
            f"    登录墙   : {'是' if wall else '否'}"
        )
        if not ok:
            failed.append(f"{label}（{shared_page.url}）")

    body = (
        "【三站判定】\n" + "\n".join(rows)
        + "\n\n【登录态文件】\n" + _state_facts()
        + f"\n\n【共享会话】重建 page 次数：{shared_session.rebuilds}"
    )
    request.node.add_report_section("call", "登录态自检", body)

    assert not failed, (
        "登录态不成立，本轮 e2e 结果**不能**当作「登录态下通过」的证据：\n"
        f"  判为未登录：{'; '.join(failed)}\n"
        + body
        + "\n\n下一步：重新扫码采一份 state"
        "（python scripts/setup_auth.py；火车票站另加 --entry trains），"
        "再跑 `pytest -m e2e`。"
    )
