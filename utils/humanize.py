"""拟人操作节奏：让有头模式下的操作速度接近真人，肉眼能跟上。

**它不是反检测手段。** 四条边界写死在这里，改代码前先读完：

1. **用途是「让人看清」，不是「骗过风控」。** 工程原则「不伪装 UA、不改自动化标记、
   不注入 stealth 脚本」一条没动（见 `config/settings.py` 与 `conftest.py` 的
   `browser_context_args`）。本模块只调**时间**，不碰身份。
2. **不因被拦而调参。** 出现 432 / 登录墙时，正确动作是记 `blocked` → `xfail`
   （`pages/base_page.py` 的 `detect_block`），不是把节奏调慢再试一遍。谁把 `Rhythm`
   按「哪里被拦」分叉成两套参数，谁就把它变成了反检测工具 —— 那是退回原点的改法。
3. **不替代等待条件。** 所有等待仍然是 `expect` / `wait_for_selector`。这里的停顿只发生在
   动作与动作之间，任何一处都不得拿来「等页面加载」。二者一旦混用就会退化回硬 sleep。
4. **CI 恒关闭。** 无头跑没有观众，慢只会拖长反馈周期；`HUMAN=0` 显式关闭。

开关优先级：`HUMAN=1` 强制开 → `HUMAN=0` 强制关 → 未设时**跟随 HEADED**
（有头自动开，无头自动关）。这样「想看过程」不用记参数，CI 也永远不会意外变慢。

与 `--slowmo` 的关系：两者会叠加，别一起用。`--slowmo` 是全局固定间隔，节奏机械；
本模块按动作类型给不同分布，更接近真人。要拟人就别加 `--slowmo`。
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
    #: 两个动作之间的间隔（看完再动）。
    action_gap_ms: tuple[int, int] = (350, 900)
    #: 需要读页面的地方（比如选完日期看摘要）停久一点。
    read_ms: tuple[int, int] = (600, 1300)
    #: 按键（Esc 之类）之后的小停顿。
    key_gap_ms: tuple[int, int] = (120, 320)


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

    def click(self, locator: Locator) -> None:
        """移动到位 → 停一下 → 点击。要求元素已可见（不做隐式等待）。"""
        if self.disabled:
            locator.click()
            return
        self.move_to(locator)
        self.pause(self.rhythm.click_settle_ms)
        locator.click()

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
