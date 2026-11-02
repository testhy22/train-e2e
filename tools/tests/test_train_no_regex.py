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
    # 2026-11-02 新增：汉口↔武昌 同城两站实测出现 Y 字头（旅游/临客列车）
    # 现场：`19:19 汉口 0时18分 Y719 19:37 武昌`、`10:33 … Y713 …`、`16:08 … Y720 …`
    # 不加这三条的话，该区间覆盖率 4/6=67%，往返用例会红在「抽不到车次号」上。
    "Y719",
    "Y713",
    "Y720",
    # 2026-11-02 新增：中转方案里实测出现 S 字头（市郊列车）。
    # 现场：广州→东莞 中转卡 `第二程 S4793 抢票`、`第一程 S4771 抢票`、`S4907` 等，
    # 一次探测里 `S` 字头共 17 段。不加就整条中转轨道抽不到第二程。
    "S4793",
    "S4771",
    "S4907",
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
    # 加 Y 字头之后的边界：不能被扩成「任何字母 + 数字」
    "Y",         # 只有字母没有数字
    "Y12345",    # 5 位数字，超长
    "H6025",     # 未实测的字头（H 是货运线，不该混进客运车次）
    # 加 S 字头之后的边界
    "S",         # 只有字母
    "S12345",    # 6 位数字，超长
    "L1234",     # 临客字头：至今没有实测样本，**故意不放行**（遇到再按同样流程补）
    "X1",        # 同上
]


@pytest.mark.parametrize("token", ACCEPT)
def test_accepts_real_train_numbers(token: str) -> None:
    assert TrainSearchPage.TRAIN_NO_RE.match(token), f"{token!r} 应被识别为车次号"


@pytest.mark.parametrize("token", REJECT)
def test_rejects_non_train_tokens(token: str) -> None:
    assert not TrainSearchPage.TRAIN_NO_RE.match(token), f"{token!r} 不该被识别为车次号"


class _FakeItem:
    """只实现 inner_text 的假 item，用来对照页面的纯文本接口，不碰浏览器。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("16:00 | 上海虹桥 | 全程5时1分 | 北京南转 | 换乘16分 | 21:01 | 亦庄", True),
        ("12:15 | 上海 | 21时45分 | 1462 | 10:00 | +1 | 北京丰台", False),
        ("06:18 | 广州南 | 0时29分 | G3017 | 06:47 | 深圳北", False),
    ],
)
def test_transfer_detection(text: str, expected: bool) -> None:
    assert TrainSearchPage.is_transfer_text(text) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("06:18 | 广州南 | 0时29分 | G3017 | 06:47 | 深圳北", "G3017"),
        ("12:15 | 上海 | 21时45分 | 1462 | 10:00 | +1 | 北京丰台", "1462"),
        ("16:00 | 上海虹桥 | 全程5时1分 | 北京南转 | 换乘16分 | 21:01 | 亦庄", None),
    ],
)
def test_train_number_of(text: str, expected: str | None) -> None:
    assert TrainSearchPage.train_number_of(text) == expected


def test_fake_item_matches_real_text_source() -> None:
    """假 item 的 inner_text 与 evaluate_all 取到的字符串是同一口径，避免自欺。"""
    item = _FakeItem("换乘 16 分 北京南转")
    assert TrainSearchPage.is_transfer_text(item.inner_text()) is True


@pytest.mark.parametrize(
    "text, expected",
    [
        # 普通卡片：行首就是出发时刻
        ("09:49\n汉口\n5时48分\nG1030\n15:37\n深圳北", "09:49"),
        # 抢票卡片：文本以「成功率：较高」开头（2026-11-02 实测形态），
        # 锚定行首的旧写法会在这类卡片上返回 None，把时段统计凭空少算一截。
        ("成功率：较高\n20:21\n汉口\n0时18分\nD105\n20:39\n武昌", "20:21"),
        # 完全没有时刻的文本
        ("精确搜索 汉口", None),
    ],
)
def test_depart_clock_of(text: str, expected: str | None) -> None:
    assert TrainSearchPage.depart_clock_of(text) == expected


@pytest.mark.parametrize(
    "clock, start, end, expected",
    [
        ("06:00", 6, 12, True),      # 左闭
        ("11:59", 6, 12, True),
        ("12:00", 6, 12, False),     # 右开
        ("05:59", 6, 12, False),
        ("23:59", 18, 24, True),
        ("00:30", 18, 24, False),    # 跨零点不归 night，归 early
        ("00:30", 0, 6, True),
    ],
)
def test_in_window(clock: str, start: int, end: int, expected: bool) -> None:
    assert TrainSearchPage.in_window(clock, start, end) is expected


# ---------------------------------------------------------------- 实测形态回归（2026-11-02 往返探测）
#
# 下面三条文本是**逐字从页面取回**的（reports/probe/round_trip_verify_20261102-221225.json 同批探测），
# 不是编的样例。锁它们的理由：
#   · 抢票卡以「成功率：较高」开头，且末尾是「暂无余票，建议抢票，成功率较高」——
#     这种文本里既有数字也有中文，最容易出现"抽不到"或"抽错"；
#   · Y 字头是这次新加进正则的，必须有真实文本兜住。

REAL_CARD_TEXTS = [
    (
        "抢票卡（D 字头 + 抢票态）",
        "成功率：较高\n20:21\n汉口\n0时18分\nD105\n20:39\n武昌\n5\n抢\n暂无余票，建议抢票，成功率较高",
        "D105",
    ),
    (
        "Y 字头（旅游/临客）",
        "19:19\n汉口\n0时18分\nY719\n19:37\n武昌\n2\n订\n硬座有票",
        "Y719",
    ),
    (
        "站名里带空格（「武 昌」）",
        "10:33\n汉口\n0时24分\nY713\n10:57\n武 昌\n2\n订\n硬座有票",
        "Y713",
    ),
]


@pytest.mark.parametrize("label, text, expected", REAL_CARD_TEXTS, ids=[t[0] for t in REAL_CARD_TEXTS])
def test_real_card_texts_still_parse(label: str, text: str, expected: str) -> None:
    """真实卡片文本必须稳定抽出车次号 —— 抽不到就等于覆盖率口径崩了。"""
    assert TrainSearchPage.train_number_of(text) == expected, f"{label}：抽取失败"


@pytest.mark.parametrize("label, text, expected", REAL_CARD_TEXTS, ids=[t[0] for t in REAL_CARD_TEXTS])
def test_real_card_texts_are_not_transfer(label: str, text: str, expected: str) -> None:
    """"这几张是点到点车次，不能因为文本里出现某个词被误判成中转（那会把它踢出分母）。"""
    assert TrainSearchPage.is_transfer_text(text) is False, f"{label}：被误判为中转"


# ---------------------------------------------------------------- 中转：两段车次号（2026-11-02）


@pytest.mark.parametrize(
    "label, legs, expected",
    [
        (
            "标准两段",
            ["第一程 G3785 二等座有票", "第二程 D7565 二等座有票"],
            ["G3785", "D7565"],
        ),
        (
            "S 字头 + 抢票态（广州→东莞实测原文）",
            ["第一程 S4771 抢票", "第二程 S4793 抢票"],
            ["S4771", "S4793"],
        ),
        (
            "DOM 顺序反了也按「一/二」归位",
            ["第二程 D7565 二等座有票", "第一程 G3785 二等座有票"],
            ["G3785", "D7565"],
        ),
        (
            "第二程没车次号（「暂无」这类），只返回第一程，不补不猜",
            ["第一程 G1 二等座有票", "第二程 暂无"],
            ["G1"],
        ),
        ("只有一条", ["第一程 C4767 二等座有票"], ["C4767"]),
        ("非法车次号被拦", ["第一程 ABC 二等座有票", "第二程 D1 二等座有票"], ["D1"]),
        ("完全没有 legs", [], []),
    ],
    ids=[
        "two-legs",
        "s-prefix-ticket-grab",
        "dom-order-reversed",
        "second-leg-missing",
        "single-leg",
        "invalid-number",
        "empty",
    ],
)
def test_transfer_leg_numbers(label: str, legs: list[str], expected: list[str]) -> None:
    assert TrainSearchPage.transfer_leg_numbers(legs) == expected, label
