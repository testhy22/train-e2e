"""日历控件（原 K-01）单独一条体检用例。

2026-11-01 解封：K-01 的两个具体成因都已在 pages/train_search_page.py 修掉 ——
  ① 日期块点的是不可见的 `#label-departDate`（class 带 assist-ib-dom），改成点可见的
     `div.search-date div.date`；
  ② 城市面板选完后 `div.widget-city-mask` 没关，遮罩拦截了后续点击，现在选完即关。
  只读探测记录见 reports/probe_dom3_2026-11-01.json（日历容器可见、标题「2026年 11月」）。

原来 skip 的理由是「控件唤不起」。既然唤得起了，把它解封才有意义 ——
一条永远 skip 的用例只能当口径样本，不能证明控件是好的。
保留守卫的原因：无头模式下整站会被 WAF 拦（K-05），没有 blocked_guard 这条会记成
failure 而不是 xfail，等于用一条与控件无关的原因污染门禁。
"""

from __future__ import annotations

import datetime as dt

import pytest

from pages.train_search_page import TrainSearchPage

pytestmark = [pytest.mark.ui]


def test_calendar_widget_opens(page, blocked_guard, widget_guard):
    """点日期块，日历应当弹出并接受选日。K-01 修好后这条参与判定。"""
    depart = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    train = TrainSearchPage(page)

    with blocked_guard():
        train.open()

    with blocked_guard(), widget_guard():
        train.select_date(depart)

    assert depart in train.body_text(), f"选完日期后页面上应显示 {depart}"
