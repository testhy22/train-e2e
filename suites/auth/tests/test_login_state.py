"""登录态轨道：验证 config/auth/ctrip_state.json 真能免扫码进已登录态。

存在理由（2026-11-01 用户裁决「立即推进微信快捷登录」）：把扫码从
「每次跑测试的人工动作」变成「一次性的准备动作」，后续用例直接复用会话。

与匿名轨道刻意隔离的三处，每一处都有具体理由：

  1. 单独 marker `auth` → 单独 scope `ui_auth`（conftest 的 collected 钩子里）。
     不并入 ui / ui_hotels 的 ratio 序列：那两条的前提是「匿名访问能拿到什么」，
     掺进登录态会让比例变成「今天扫没扫码」。

  2. 单独 context（auth_context），不是给全局 page 挂 storage_state。
     酒店轨道的断言依赖「匿名态」这个实验条件（提交路径落 passport）。
     全局挂上会把条件改掉，报告里 login_wall= 从 True 变 False，
     读报告的人会以为登录墙放开了。

  3. 缺 state 文件时 skip（`known-limit: AUTH-01`）而不是 fail。
     凭据不进 CI（config/settings.py 的登录态段写了原因），
     让 CI 永久挂一条与代码质量无关的红，最终必然被无视。

本轨道**不验证**登录功能本身：不输账号密码、不处理验证码、不模拟扫码。
它只回答一个问题——「手上这份会话还能不能用」。
微信授权页的 DOM 在本项目里一次都没取过证，所以这里也不写任何关于它的断言。
"""

from __future__ import annotations

import json

import pytest

from config import settings
from utils.auth import is_logged_in, looks_like_login_wall, state_summary

pytestmark = [pytest.mark.auth]


def test_state_file_is_usable(auth_state, request):
    """state 文件的结构性检查：有 cookie、且至少一条属于 ctrip 域。

    这条不启浏览器，放在最前面：文件都读不出来时，
    后面的浏览器用例失败会显得像站点问题，实际是我们的准备物坏了。
    """
    data = json.loads(auth_state.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    summary = state_summary(data)
    request.node.add_report_section("call", "state 快照", summary)

    assert cookies, f"state 文件里没有 cookie，这份登录态不可用：\n{summary}"

    ctrip_cookies = [c for c in cookies if "ctrip" in (c.get("domain") or "")]
    assert ctrip_cookies, (
        "state 文件里没有任何 ctrip 域 cookie —— 扫的可能不是携程的码，"
        f"或者保存时页面已经掉了登录：\n{summary}"
    )


def test_homepage_shows_logged_in_account(auth_base_page, request, blocked_guard):
    """加载 state 后直接访问首页，断言已进入登录态。

    断言两条，顺序有意：
      1. 先排除落在 passport 登录墙 —— 落在墙上时「注册」多半也不可见，
         只判第 2 条会得出「没检测到登录标识」这种含糊结论；
      2. 再判登录标识（`utils/auth.is_logged_in` 的三级判据）。
    """
    with blocked_guard():
        auth_base_page.open_and_check("/")

    page = auth_base_page.page
    logged, evidence = is_logged_in(page)
    shots = auth_base_page.capture("auth__homepage_logged_in")

    request.node.add_report_section(
        "call",
        "登录态证据",
        f"url：{page.url}\n"
        f"title：{page.title()}\n"
        f"判定：{evidence}\n"
        f"是否落在登录墙：{looks_like_login_wall(page.url)}\n"
        f"营销浮层：{auth_base_page.overlay_log or '未发现'}\n"
        f"截图：{', '.join(str(p) for p in shots) or '(截图失败)'}",
    )

    assert not looks_like_login_wall(page.url), (
        f"落在 passport 登录墙，说明 state 已失效或未被加载：{page.url}"
    )
    assert logged, (
        f"未检测到已登录态（{evidence}）。"
        "若 state 文件刚扫过仍如此，先看 reports 里那张截图右上角是不是还显示「登录 / 注册」。"
    )


def test_state_age_is_reported(auth_state_age_days, request):
    """登录态存放时长只做提示，**不做断言**。

    写这条用例的目的不是"测"什么，而是把「这份凭据存了几天」落进 junit 与报告：
    登录用例某天突然红了，第一件事该是看它存了多久，而不是怀疑代码改动。
    """
    threshold = settings.AUTH_STATE_TTL_DAYS
    request.node.add_report_section(
        "call",
        "登录态时效",
        f"已存放 {auth_state_age_days:.1f} 天，建议阈值 {threshold} 天",
    )
    if auth_state_age_days > threshold:
        pytest.skip(
            f"known-limit: AUTH-02 登录态已存放 {auth_state_age_days:.1f} 天，"
            f"超过建议阈值 {threshold} 天；先重跑 python scripts/setup_auth.py"
        )
