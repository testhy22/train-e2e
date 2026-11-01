"""拟人节奏原语的测试。

不需要浏览器：用假的 Page / Locator 断言「停了多少次、移了几步、输入延迟落在哪」。
这样能锁住三件最容易回退的事：

1. **关闭时零开销** —— 一旦有人在 disabled 分支里也插了 wait，CI 会集体变慢而不报错。
2. **延迟落在 Rhythm 区间内** —— 防止有人图省事改回固定值（`slowmo` 那种机械节奏）。
3. **种子可复现** —— 时序问题最难查的就是「这次没复现」，固定种子必须真的固定。
"""

from __future__ import annotations

import pytest

from utils.humanize import DEFAULT_RHYTHM, Humanizer, Rhythm, human_enabled

# ---------------------------------------------------------------- 假对象

class FakeMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float, int]] = []

    def move(self, x: float, y: float, steps: int = 1) -> None:
        self.moves.append((x, y, steps))


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


_UNSET = object()   # 哨兵：区分「没传 box」和「显式传 box=None（元素未布局）」


class FakeLocator:
    """只实现 Humanizer 会碰到的方法。"""

    def __init__(self, box: dict | None = _UNSET) -> None:  # type: ignore[assignment]
        self.box = (
            {"x": 100.0, "y": 200.0, "width": 60.0, "height": 20.0}
            if box is _UNSET
            else box
        )
        self.clicks = 0
        self.typed: list[tuple[str, int]] = []

    def bounding_box(self) -> dict | None:
        return self.box

    def click(self) -> None:
        self.clicks += 1

    def press_sequentially(self, text: str, delay: int = 0) -> None:
        self.typed.append((text, delay))


def make(enabled: bool, seed: int | None = None, rhythm: Rhythm = DEFAULT_RHYTHM):
    page = FakePage()
    return page, Humanizer(page, enabled=enabled, rhythm=rhythm, seed=seed)


# ---------------------------------------------------------------- 开关解析

@pytest.mark.parametrize(
    ("headless", "expected"),
    [(True, False), (False, True)],
    ids=["无头→关", "有头→开"],
)
def test_未设HUMAN时跟随HEADED(headless, expected):
    assert human_enabled(headless, "") is expected


@pytest.mark.parametrize(
    ("raw", "headless", "expected"),
    [
        ("1", True, True),      # 无头也强制开（调试用）
        ("true", True, True),
        ("on", True, True),
        ("0", False, False),    # 有头也强制关（跑得快）
        ("false", False, False),
        ("off", False, False),
        (" 1 ", True, True),    # 前后空格要容忍
    ],
)
def test_HUMAN显式开关覆盖HEADED(raw, headless, expected):
    assert human_enabled(headless, raw) is expected


def test_HUMAN非法值必须报错而不是静默当关():
    """静默降级会让人以为「开了拟人」，实际全速跑完，排查成本比报错高得多。"""
    with pytest.raises(ValueError, match="HUMAN 只接受"):
        human_enabled(True, "yes-please")


# ---------------------------------------------------------------- 关闭时零开销

def test_关闭时点击零额外开销():
    """disabled 分支必须直通原生 click：不移动鼠标、不停顿、不额外调用。"""
    page, human = make(enabled=False)
    loc = FakeLocator()

    human.click(loc)

    assert loc.clicks == 1
    assert page.mouse.moves == [], "关闭时不该移动鼠标"
    assert page.waits == [], "关闭时不该有任何 wait_for_timeout"


def test_关闭时输入走一次性原生调用():
    page, human = make(enabled=False)
    loc = FakeLocator()

    human.type_text(loc, "上海")

    assert loc.typed == [("上海", 0)], "关闭时应整串一次性输入、零延迟"
    assert page.waits == []


def test_关闭时停顿原语全部直通():
    page, human = make(enabled=False)

    human.think()
    human.read()
    human.pause((100, 200))

    assert page.waits == []


# ---------------------------------------------------------------- 开启时的节奏

def test_开启时点击先移动再停顿再点():
    page, human = make(enabled=True, seed=1)
    loc = FakeLocator(box={"x": 10.0, "y": 20.0, "width": 100.0, "height": 40.0})

    human.click(loc)

    assert loc.clicks == 1
    assert len(page.mouse.moves) == 1
    x, y, steps = page.mouse.moves[0]
    assert (x, y) == (60.0, 40.0), "应移到元素中心（x+w/2, y+h/2）"
    assert DEFAULT_RHYTHM.move_steps[0] <= steps <= DEFAULT_RHYTHM.move_steps[1]
    assert len(page.waits) == 1, "到位后应停一次"
    lo, hi = DEFAULT_RHYTHM.click_settle_ms
    assert lo <= page.waits[0] <= hi


def test_取不到bounding_box时跳过移动但照常点击():
    """元素未布局时 bounding_box 返回 None，不能因此报错中断用例。"""
    page, human = make(enabled=True, seed=2)
    loc = FakeLocator(box=None)

    human.click(loc)

    assert page.mouse.moves == []
    assert loc.clicks == 1


def test_逐字符输入且每次延迟都在区间内():
    page, human = make(enabled=True, seed=3)
    loc = FakeLocator()

    human.type_text(loc, "上海")

    assert [c for c, _ in loc.typed] == ["上", "海"], "必须逐字符调用，不能整串塞"
    lo, hi = DEFAULT_RHYTHM.type_ms
    extra_hi = 260                          # 思考停顿的附加量上限
    for _, delay in loc.typed:
        assert lo <= delay <= hi + extra_hi, f"延迟 {delay} 超出可能范围"
    assert all(lo <= _ <= hi for _, _ in loc.typed), "首字符不应叠加思考停顿"
    # 输完后应留一次停顿给联想面板刷新
    assert len(page.waits) >= 1


def test_固定种子两次运行完全一致():
    page_a, human_a = make(enabled=True, seed=42)
    page_b, human_b = make(enabled=True, seed=42)
    loc_a, loc_b = FakeLocator(), FakeLocator()

    for human, loc in ((human_a, loc_a), (human_b, loc_b)):
        human.type_text(loc, "北京上海")
        human.think()

    assert page_a.waits == page_b.waits, "同种子必须得到同一串停顿"
    assert [d for _, d in loc_a.typed] == [d for _, d in loc_b.typed]


def test_不同种子产生不同节奏():
    """固定间隔本身就是机器特征；这条防止有人把随机源写死成常量。"""
    page_a, human_a = make(enabled=True, seed=1)
    page_b, human_b = make(enabled=True, seed=999)

    for h in (human_a, human_b):
        h.type_text(FakeLocator(), "上海北京广州")

    assert page_a.waits != page_b.waits


def test_按键后有小停顿():
    page, human = make(enabled=True, seed=5)

    human.press("Escape")

    assert page.keyboard.pressed == ["Escape"]
    lo, hi = DEFAULT_RHYTHM.key_gap_ms
    assert len(page.waits) == 1 and lo <= page.waits[0] <= hi
