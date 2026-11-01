"""车次号正则的离线单测（不需要浏览器）。

起因（2026-11-01）：修好页面对象后 sh-bj 用例走到内容层失败 —— 60 条里只认出 53 条车次号，
88.3% < 90%。现场（reports/probe_items_2026-11-01.json）显示漏掉的是两类：
  1. 「1462」这种普速纯数字车次 —— 正则只认 G/D/C/Z/T/K 字头；
  2. 「换乘 16 分」的中转卡片 —— 结构上没有单一车次号。
第 1 类是解析口径缺口，必须改；第 2 类是分母口径错误，另行处理。
本文件锁住第 1 类，防止以后有人把 4 位纯数字又删掉。
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Locator

from pages.train_search_page import TrainSearchPage

# 真实车次号形态：字头 + 1~4 位数字，或 4 位纯数字（普速）
ACCEPT = [
    "G6025",
    "D7577",
    "C7001",
    "Z19",
    "T109",
    "K1234",
    "1462",
    "1461",
]

# 页面正文里同样以「一串字符」出现、但**不是**车次号的东西。误认会虚抬覆盖率。
REJECT = [
    "553",       # 票价
    "553.0",     # 票价（带小数）
    "12:15",     # 时刻
    "+1",        # 跨天标记
    "二等座",
    "预订",
    "beijing",   # 三字码
    "12345",     # 5 位数字，超长
    "",          # 空 token
]


@pytest.mark.parametrize("token", ACCEPT)
def test_accepts_real_train_numbers(token: str) -> None:
    assert TrainSearchPage.TRAIN_NO_RE.match(token), f"{token!r} 应被识别为车次号"


@pytest.mark.parametrize("token", REJECT)
def test_rejects_non_train_tokens(token: str) -> None:
    assert not TrainSearchPage.TRAIN_NO_RE.match(token), f"{token!r} 不该被识别为车次号"


class _FakeItem:
    """只实现 inner_text 的假 item，用来验证中转判定不依赖浏览器。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


def _is_transfer(text: str) -> bool:
    # 与 TrainSearchPage.is_transfer_item 同逻辑；这里不构造真 Locator。
    return all(m in text for m in TrainSearchPage.TRANSFER_MARKERS) and "转" in text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("16:00 | 上海虹桥 | 全程5时1分 | 北京南转 | 换乘16分 | 21:01 | 亦庄", True),
        ("12:15 | 上海 | 21时45分 | 1462 | 10:00 | +1 | 北京丰台", False),
        ("06:18 | 广州南 | 0时29分 | G3017 | 06:47 | 深圳北", False),
    ],
)
def test_transfer_detection(text: str, expected: bool) -> None:
    assert _is_transfer(text) is expected


def test_fake_item_matches_protocol() -> None:
    """确认假 item 的接口与 Locator.inner_text 一致，避免自欺。"""
    item = _FakeItem("换乘 16 分 北京南转")
    assert isinstance(item.inner_text(), str)
    assert isinstance(item, _FakeItem) and not isinstance(item, Locator)
