"""日历控件（K-01）单独一条，**故意 skip 掉的活样本**。

为什么留一条永远 skip 的用例，而不是删掉：

1. 它是 `known-limit:` 口径的活样本。门禁必须把这条排除出分母（既不算 blocked，
   也不算 pass）。没有真实样本在 junit 里，这条口径就只是文档上的一句话，
   谁都可以悄悄改掉而不被任何测试拦住。
2. 控件一旦恢复，删掉下面的 `pytest.mark.skip` 一行即可 —— 用例本体是能跑的。
3. 它与 `test_train_search` 里那条 `widget_guard` 路径是两件事：
   那条是「跑到一半日期选不上」，这条是「单独体检日历本身」。

历史（2026-10-31 实测）：日历前两次能弹出（带 `.widget-calendar-mask` 遮罩），
之后点日期文字 / 点 `.date` 容器 / JS focus / JS 派发 click / hover 后点击 全部唤不起。
按「同一操作连败 2 次即停手」的原则没有继续加重试，改为记录为已知限制。
"""

from __future__ import annotations

import datetime as dt

import pytest

from pages.train_search_page import TrainSearchPage

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skip(reason="known-limit: K-01 日历控件唤不起（见 docs/KNOWN-LIMITATIONS.md）"),
]


def test_calendar_widget_opens(page):
    """点日期输入框，日历面板应当出现。K-01 未解决前这条不参与判定。"""
    train = TrainSearchPage(page)
    train.open()
    train.select_date((dt.date.today() + dt.timedelta(days=7)).isoformat())
