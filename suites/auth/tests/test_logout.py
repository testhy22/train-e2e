"""退出登录：结构验证（默认跑）+ 真实注销（opt-in）。

## 为什么不是一个「点一下就断言未登录」的用例

2026-11-01 实测了三件事，每条都改变设计：

1. **退出入口是懒渲染的**（tools/probes/probe_logout_menu.py）
   悬停前扫全文档，与「退出」相关的节点数 = **0**，`body.innerText` 也不含「退出」。
   悬停后才出现。所以顺序只能是「先 hover，再找」，反过来会稳定超时。

2. **退出是普通链接，不是前端 JS**
   `a[class*='logout']` 的属性：
       href = https://passport.ctrip.com/user/logout
       onclick = ''（空）
       data-ubt-key = PC_head_logout_click
   整套动作就是**导航到那个 URL**，凭据由服务端 302 + Set-Cookie 清除。
   推论：任何「拦截注销请求、返回假响应」的 mock 都**测不出退出**——
   浏览器收不到 Set-Cookie，cookie 一条不少，页面还是登录态。
   要让它变绿只能自己伪造 Set-Cookie，那验证的是我们写的假响应，不是携程的退出。

3. **真实退出会消耗凭据**（tools/probes/probe_logout_effect.py）
   点退出前 ctrip 域登录 cookie：AHeadUserInfo / DUID / IsNonUser / cticket /
   login_type / login_uid / suid …
   点退出后只剩 GUID / _RGUID / _abtest_userid / suid。
   用**同一份 state 文件**新开 context 重放 → 判定「未登录」。
   也就是说：文件本身没被改写（我们从没写回），但文件里的 cticket 已经作废。
   「母版只读所以安全」这个推理漏掉的一环就在这里 —— cookie 的有效性不在文件里。

## 因此拆成两条用例

| 用例 | 默认 | 是否消耗 state | 验什么 |
|---|---|---|---|
| `test_account_menu_exposes_logout_entry` | 跑 | 否（只 hover，不点） | 入口存在、可见、指向正确的注销端点 |
| `test_real_logout_revokes_session` | **skip** | **是，一次性** | 端到端：点了之后服务端真的注销 |

第二条要显式设 `AUTH_REAL_LOGOUT=1` 才跑，跑完自动把 state 文件改名成
`*.consumed` —— 不这么做的话，文件看起来还在，下次登录用例会红，
而红的原因（凭据已被自己作废）没人看得出来。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config import settings
from pages.header_account import HeaderAccount, LogoutUnavailable
from utils.auth import is_logged_in, looks_like_login_wall

pytestmark = [pytest.mark.auth]

# 注销端点。实测来自 tools/probes/probe_logout_requests.py：退出后 75 条请求里
# 只有这一条的 URL 带注销语义，且是 302 —— 与 <a href> 完全一致。
EXPECTED_LOGOUT_URL_PART = "passport.ctrip.com/user/logout"

# 真实退出开关。默认关，理由见文件头第 3 条。
REAL_LOGOUT = os.getenv("AUTH_REAL_LOGOUT", "").strip() == "1"


def test_account_menu_exposes_logout_entry(auth_base_page, request, blocked_guard):
    """默认跑：账户浮层能展开，且退出入口指向 passport 注销端点。

    这条**不点退出**，所以可以反复跑、可以进 CI、不会消耗凭据。

    它证明的是三件在真实退出用例里看不到的事：
      · 浮层是懒渲染的（悬停前 DOM 里没有它）——展开这一步本身是有效的；
      · 入口仍然存在且可见（站点改版会先在这里断）；
      · href 指向 passport 注销端点（前端配置正确，而不是跳去别的页面）。

    它**不证明**退出功能可用 —— 那需要真的点，见下一条。
    """
    with blocked_guard():
        auth_base_page.open_and_check("/")

    page = auth_base_page.page
    logged, evidence = is_logged_in(page)
    assert logged, f"前置条件不成立：没进入登录态（{evidence}）"

    acct = HeaderAccount(page)
    try:
        acct.open_menu()
    except LogoutUnavailable as exc:
        auth_base_page.capture("auth__logout_menu_unavailable")
        pytest.skip(f"known-limit: AUTH-03 账户浮层未展开，站点结构可能已改版：{exc}")

    entry = acct.logout_entry().first
    shots = auth_base_page.capture("auth__logout_menu")

    href = entry.get_attribute("href") or ""
    visible = entry.is_visible()
    box = entry.bounding_box()

    request.node.add_report_section(
        "call",
        "退出入口",
        f"visible：{visible}\n"
        f"bounding_box：{box}\n"
        f"href：{href}\n"
        f"文案：{entry.inner_text(timeout=2000).strip()}\n"
        f"截图：{', '.join(str(p) for p in shots) or '(截图失败)'}",
    )

    # 顺序有意：先断言可见，再断言 href。
    # 若浮层是「存在但 0×0」（overflow 容器里的隐藏项），只判 count 会假通过。
    assert visible, "退出入口在 DOM 里但不可见 —— 浮层可能只展开了外壳"
    assert box and box["width"] > 0 and box["height"] > 0, (
        f"退出入口边界框为空：{box}（0 尺寸元素 is_visible 也可能为 True）"
    )
    assert EXPECTED_LOGOUT_URL_PART in href, (
        f"退出链接没有指向注销端点，实际 href={href!r}；"
        f"期望包含 {EXPECTED_LOGOUT_URL_PART} —— 前端可能改了退出实现"
    )

    # 收尾：把鼠标移开，避免浮层悬在页面上影响后续观察
    page.mouse.move(5, 600)


@pytest.mark.skipif(
    not REAL_LOGOUT,
    reason=(
        "known-limit: AUTH-04 真实退出会作废本机登录态（实测：重放同一份 state 判未登录），"
        "跑一次就要重扫一次码。需要验证端到端注销时设 AUTH_REAL_LOGOUT=1"
    ),
)
def test_real_logout_revokes_session(auth_base_page, auth_state: Path, request, blocked_guard):
    """opt-in：真实点退出，验证端到端注销，然后把 state 标记为已消耗。

    「标记为已消耗」不是洁癖：退出之后文件还在、内容也没变（我们从没写回），
    但里面的 cticket 已经作废。不改名的话，下一次跑 `test_login_state.py`
    会红在「未检测到已登录态」，而没人会想到是这条用例干的。

    改名成 `<原名>.consumed` 之后，`auth_state` 夹具会因「文件不存在」而
    skip(AUTH-01) 并提示重扫 —— 把「莫名其妙红」换成「一句明确的待办」。
    """
    with blocked_guard():
        auth_base_page.open_and_check("/")

    page = auth_base_page.page
    logged, evidence = is_logged_in(page)
    assert logged, f"前置条件不成立：没进入登录态（{evidence}）"

    acct = HeaderAccount(page)
    try:
        before = acct.logout()
    except LogoutUnavailable as exc:
        auth_base_page.capture("auth__logout_unavailable")
        pytest.skip(f"known-limit: AUTH-03 退出入口不可用：{exc}")

    shots = auth_base_page.capture("auth__after_logout")

    # 退出后的复核要独立再取一次，不只看 logout() 的返回值：
    # logout() 内部已经轮询过 is_logged_in，这里额外确认没有落回登录墙。
    still_logged, after_evidence = is_logged_in(page)
    request.node.add_report_section(
        "call",
        "退出结果",
        f"退出过程：{before}\n"
        f"退出后判定：{after_evidence}\n"
        f"是否落在登录墙：{looks_like_login_wall(page.url)}\n"
        f"url：{page.url}\n"
        f"截图：{', '.join(str(p) for p in shots) or '(截图失败)'}",
    )

    assert not still_logged, f"点了退出但仍是登录态：{after_evidence}"

    # 标记消耗。放在断言之后：断言红了说明退出没生效，凭据大概率还在，不该改名。
    consumed = auth_state.with_name(auth_state.name + ".consumed")
    try:
        auth_state.rename(consumed)
        print(
            f"\n⚠️ 真实退出已作废本机登录态，已把 {auth_state.name} 改名为 "
            f"{consumed.name}。要继续跑登录用例，请重跑：\n"
            f"    python scripts/setup_auth.py --entry www"
        )
    except Exception as exc:  # 改名失败不该把用例判红，但要喊出来
        print(f"\n⚠️ 改名失败（{exc}）：{auth_state} 内容是失效凭据，请手动删除后重扫")
