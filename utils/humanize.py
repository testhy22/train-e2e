"""拟人操作节奏：让有头模式下的操作速度接近真人，肉眼能跟上。

**它不是反检测手段。** 四条边界写死在这里，改代码前先读完：

1. **用途是「让人看清」+ 长期低频预防，不是「骗过风控」。** 工程原则「不伪装 UA、不改自动化标记、
   不注入 stealth 脚本」一条没动（见 `config/settings.py` 与 `conftest.py` 的
   `browser_context_args`）。本模块只调**时间**，不碰身份。
   定位（2026-11-02 定案）：**预防性措施，不是解封手段** —— 解封关键已实证是有头模式
   （K-05：同登录态无头 8 xfailed / `HEADED=1` 8 passed）；拟人化只降低单次被行为模型
   命中的概率，**不提高吞吐、不改变「失败即停/熔断」与 blocked→xfail 语义**。
2. **不因被拦而调参。** 出现 432 / 登录墙时，正确动作是记 `blocked` → `xfail`
   （`pages/base_page.py` 的 `detect_block`），不是把节奏调慢再试一遍。谁把 `Rhythm`
   按「哪里被拦」分叉成两套参数，谁就把它变成了反检测工具 —— 那是退回原点的改法。
3. **不替代等待条件。** 所有等待仍然是 `expect` / `wait_for_selector`。这里的停顿只发生在
   动作与动作之间，任何一处都不得拿来「等页面加载」。二者一旦混用就会退化回硬 sleep。
4. **CI 默认关闭。** 无头跑没有观众，慢只会拖长反馈周期；CI 恒 `HUMAN=0`。
   显式 `HUMAN=1` 的本地/低频预防跑不受此限（见边界 1），但默认值不变。

开关优先级：`HUMAN=1` 强制开 → `HUMAN=0` 强制关 → 未设时**跟随 HEADED**
（有头自动开，无头自动关）。这样「想看过程」不用记参数，CI 也永远不会意外变慢。

与 `--slowmo` 的关系：两者会叠加，别一起用。`--slowmo` 是全局固定间隔，节奏机械；
本模块按动作类型给不同分布，更接近真人。要拟人就别加 `--slowmo`。

**页面接入清单（2026-11-02，新页面接入前先看这里）：**

| 页面 | 状态 | 说明 |
|---|---|---|
| `pages/hotel_search_page.py` | 已接入 | 16 处（既有）；关遮罩坐标已加抖动 |
| `pages/flight_search_page.py` | 已接入 | 6 处（既有）；关遮罩坐标已加抖动 |
| `pages/train_search_page.py` | 已接入 | 9 处（2026-11-02 补齐城市项/日历/提交；2026-11-07 日期格改 `move_neutral` + `click_soon`） |
| `pages/header_account.py` | 已接入 | 悬停多步移动 + 退出点击（非 BasePage 子类，自带 Humanizer） |
| `pages/base_page.py` `goto()` | 已接入 | 2026-11-07 补 L2：导航后 1–2 次滚动（150–400px）+ 滚动间停顿；**顺序固定为 关浮层 → 滚动 → 读页停顿** |
| 轮询类 `wait_for_timeout` | 刻意不接入 | 语义是「等条件」不是节奏（边界 3） |
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查，避免导入期拉起 playwright
    from playwright.sync_api import Locator, Page


# ---------------------------------------------------------------- 时间分布

@dataclass(frozen=True)
class Rhythm:
    """真人操作的时间分布（毫秒）。

    全部用区间而不是定值：**固定间隔本身就是机器特征**，`slowmo=600` 每一步都 600ms
    比不做拟人还显眼。取值依据是「一个普通用户对着表单操作」的常识区间，不是拟合出来的。
    """

    #: 单字符输入。普通中文输入约 5–9 字/秒。
    type_ms: tuple[int, int] = (110, 190)
    #: 鼠标移动到目标时分几步走。Playwright 默认 1 步 = 瞬移。
    move_steps: tuple[int, int] = (8, 16)
    #: 鼠标到位后、按下前的停顿（人有反应时间）。
    click_settle_ms: tuple[int, int] = (180, 420)
    #: 翻月这类**可能连点**的场景：单次停顿压到 150–350ms，避免「翻 3 个月」
    #: 累积成 3×400ms，把节奏拖成另一种机械。
    pagination_settle_ms: tuple[int, int] = (150, 350)
    #: 指针离开目标后等 hover 态消失的时间（2026-11-07）。
    #: 用在「悬停本身会触发重绘」的目标上（火车票日历格会刷新票价/余票）：
    #: 先把指针挪到中性区、等这一段，让上一次 hover 引起的重绘结束，再重新解析目标。
    hover_clear_ms: tuple[int, int] = (140, 300)
    #: 移动到位后、按下前的**短**停顿（2026-11-07）。用在同上场景：
    #: 停得越久，「解析到的 DOM」与「点击落下的瞬间」之间被重绘插队的窗口越大。
    #: 40–120ms 仍落在人类「定位→按下」的反应区间内，不是瞬点。
    click_soon_ms: tuple[int, int] = (40, 120)
    #: 两个动作之间的间隔（看完再动）。
    action_gap_ms: tuple[int, int] = (350, 900)
    #: 需要读页面的地方（比如选完日期看摘要）停久一点。
    read_ms: tuple[int, int] = (600, 1300)
    #: 按键（Esc 之类）之后的小停顿。
    key_gap_ms: tuple[int, int] = (120, 320)
    #: 导航后「扫一眼」的滚动幅度（像素）与两次滚动之间的间隔。
    #: 幅度取 150–400：低于 150 等于没动，高于 400 会把表单整块推出视口，
    #: 后续点击要靠 Playwright 自动回滚，反而多出一段不自然的跳转。
    scroll_px: tuple[int, int] = (150, 400)
    scroll_gap_ms: tuple[int, int] = (400, 900)


DEFAULT_RHYTHM = Rhythm()

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}


def human_enabled(headless: bool, raw: str | None = None) -> bool:
    """解析开关。`raw` 留空时读环境变量 `HUMAN`。

    `headless` 只作为「未显式设置」时的默认值来源，不参与其他判断。
    """
    value = (raw if raw is not None else os.getenv("HUMAN", "")).strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    if value:
        raise ValueError(f"HUMAN 只接受 0/1/true/false/on/off，收到 {value!r}")
    return not headless


# ---------------------------------------------------------------- 原语

class Humanizer:
    """把「移动 → 停顿 → 动作」包成一次调用；关闭时等价于原生调用。"""

    def __init__(
        self,
        page: Page,
        *,
        enabled: bool,
        rhythm: Rhythm = DEFAULT_RHYTHM,
        seed: int | None = None,
    ) -> None:
        self.page = page
        self.enabled = enabled
        self.rhythm = rhythm
        # 独立随机源：不污染全局 random，也让失败可复现（HUMAN_SEED 固定即可）。
        self._rng = random.Random(seed)

    # ------------------------------------------------------------ 开关
    @property
    def disabled(self) -> bool:
        return not self.enabled

    def _ms(self, pair: tuple[int, int]) -> int:
        return self._rng.randint(*pair)

    # ------------------------------------------------------------ 停顿
    def pause(self, pair: tuple[int, int]) -> None:
        """动作间停顿。**关闭时直接返回**，不留任何额外开销。"""
        if self.disabled:
            return
        self.page.wait_for_timeout(self._ms(pair))

    def think(self) -> None:
        """两个动作之间的自然间隔。"""
        self.pause(self.rhythm.action_gap_ms)

    def read(self) -> None:
        """读完页面上某处信息后的停顿。"""
        self.pause(self.rhythm.read_ms)

    def browse(self) -> None:
        """导航后「上下扫一眼」：1–2 次小幅滚动，滚动之间停顿。关闭时零开销。

        补的是原先缺的一层：旧版导航后只有 `read()` 一段停顿，鼠标一动不动。
        真人打开首页会先扫一屏再动手；`wheel` 只改滚动位置，不碰指针与键盘，
        **不改变浏览器对外暴露的任何身份信息**（边界 1）。

        调用点固定在 `BasePage.goto()`，且**必须在 `dismiss_overlays()` 之后** ——
        关浮层用的是坐标点击，先滚动会让坐标落到别处。
        """
        if self.disabled:
            return
        for _ in range(self._rng.randint(1, 2)):
            delta = self._rng.randint(*self.rhythm.scroll_px) * self._rng.choice((-1, 1))
            self.page.mouse.wheel(0, delta)
            self.pause(self.rhythm.scroll_gap_ms)

    # ------------------------------------------------------------ 鼠标
    def move_to(self, locator: Locator) -> None:
        """把鼠标按多步移到元素中心。

        取不到 bounding_box（元素已消失 / 未布局）时**静默跳过移动**，不在这里报错 ——
        真正的失败应该由随后的 click 用 Playwright 自己的超时信息说清楚。
        """
        if self.disabled:
            return
        box = locator.bounding_box()
        if not box:
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=self._ms(self.rhythm.move_steps))

    def move_to_xy(self, x: float, y: float) -> None:
        """按多步移到指定坐标（调用方已算好位置）。关闭时等价单步 move。

        与 `move_to` 的区别：不取元素中心 —— 悬停目标可能来自「带零尺寸兜底」的
        自定义计算（如 header_account 的用户区），元素中心语义由调用方负责。
        """
        if self.disabled:
            self.page.mouse.move(x, y)
            return
        self.page.mouse.move(x, y, steps=self._ms(self.rhythm.move_steps))

    def click(self, locator: Locator) -> None:
        """移动到位 → 停一下 → 点击。要求元素已可见（不做隐式等待）。"""
        if self.disabled:
            locator.click()
            return
        self.move_to(locator)
        self.pause(self.rhythm.click_settle_ms)
        locator.click()

    def move_neutral(self, x: int = 28, y: int = 28, jitter: int = 10) -> None:
        """把指针挪到视口左上角的中性区，**只移动、不点击**（2026-11-07）。

        用途：消掉上一个目标上的 hover 态。火车票日历鼠标经过某天就会刷新那一列的
        票价/余票并重绘整块网格（`pages/train_search_page.py` 的既有注释已记），
        所以「解析日期格 → 点击」之前必须让重绘先结束，否则解析到的格子与点下去的
        格子可能不是同一块 DOM。

        关闭时退化为一次 `mouse.move`（仍然移动，因为调用方依赖「hover 被清掉」这个
        副作用，而不只是节奏）。
        """
        if self.disabled:
            self.page.mouse.move(x, y)
            return
        self.page.mouse.move(
            x + self._rng.randint(-jitter, jitter), y + self._rng.randint(-jitter, jitter)
        )
        self.pause(self.rhythm.hover_clear_ms)

    def click_soon(self, locator: Locator) -> None:
        """移动到位后**尽快**点击（40–120ms）。与 `click` 只差停顿时长。

        为什么需要这一档：`click_settle_ms` 的 180–420ms 对普通控件是对的
        （人有反应时间），但对「悬停即重绘」的目标是风险窗口 —— 鼠标停在格子上
        越久，越可能等到一次把 DOM 换掉的刷新。这里压到 40–120ms，
        让「解析到的格子」和「按下的瞬间」落在同一个绘制帧内。
        """
        if self.disabled:
            locator.click()
            return
        self.move_to(locator)
        self.pause(self.rhythm.click_soon_ms)
        locator.click()

    def click_quick(self, locator: Locator) -> None:
        """翻月等**可能连点**的场景：移动到位后只停 150–350ms 就点。

        与 `click` 的区别只在停顿时长 —— 重复动作若每次都吃 180–420ms，
        「翻 3 个月」就是 3×400ms，节奏反而机械。
        """
        if self.disabled:
            locator.click()
            return
        self.move_to(locator)
        self.pause(self.rhythm.pagination_settle_ms)
        locator.click()

    def click_neutral(self, x: int = 20, y: int = 20, jitter: int = 15) -> None:
        """点中性空白区（关遮罩/面板）。开启时坐标加 ±jitter 抖动。

        固定 (20, 20) 每次完全一致是指纹级特征；真人点「空白处」也不会两次落在同一点。
        关闭时等价 `mouse.click(x, y)` 原行为，字节级不变。
        """
        if self.disabled:
            self.page.mouse.click(x, y)
            return
        self.page.mouse.click(
            self._rng.randint(x - jitter, x + jitter),
            self._rng.randint(y - jitter, y + jitter),
        )

    def press(self, key: str) -> None:
        """按键（Esc 等），带一点操作后的停顿。"""
        self.page.keyboard.press(key)
        self.pause(self.rhythm.key_gap_ms)

    # ------------------------------------------------------------ 键盘
    def type_text(self, locator: Locator, text: str) -> None:
        """逐字符输入，每字符单独采样延迟。

        为什么不用 `locator.fill()`：fill 是「清空 + 一次性塞值」，浏览器只收到一次
        input 事件，联想面板未必触发；也不像人。为什么不用 `press_sequentially(delay=n)`：
        那是**固定**间隔，正好是机器特征。
        """
        if self.disabled:
            locator.press_sequentially(text, delay=0)
            return
        for i, ch in enumerate(text):
            # 每 2–3 个字符插入一次稍长的停顿，模拟换键/思考。
            delay = self._ms(self.rhythm.type_ms)
            if i and i % self._rng.randint(2, 3) == 0:
                delay += self._ms((120, 260))
            locator.press_sequentially(ch, delay=delay)
        # 输完到下一步之间留一点，让联想面板有机会刷新。
        self.pause(self.rhythm.click_settle_ms)
