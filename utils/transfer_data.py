"""中转用例数据：解析 YAML → `TransferCase`。

为什么单独一个模块：`data_loader.Case` 是单程契约、`RoundTripCase` 是往返契约，
中转需要的是「方案条数下界 + 两段车次号覆盖率下界」这两个**完全不同**的阈值字段。
往既有 dataclass 里加可选字段，会让单程/往返的既有单测都去处理"这个字段可能为 None"。
酒店 / 机票 / 往返都已经是这个先例，保持一致。

三条硬约束（前两条踩过，第三条是本轮实测）：

1. **出发日**必须落在 +3..+15（`settings.MAX_PRESALE_DAYS`，预售期唯一事实源）。
   越界抛错，**不静默夹取** —— 夹取会把「数据写错」变成「跑了一条别人看不出问题的用例」。
2. 出发与到达不能相同。
3. `min_two_leg_ratio` 是**下界**而不是 1.0：实测存在抽不出第二程车次号的卡片
   （如「第二程 暂无」），写死 100% 会在余票波动时误红。

本模块不依赖 pytest、不触网，可单独导入与单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from config import settings

MIN_OFFSET_DAYS = 3
MAX_OFFSET_DAYS = settings.MAX_PRESALE_DAYS

REQUIRED_FIELDS = ("id", "from_city", "to_city", "depart_offset_days", "min_cards")


class DataError(ValueError):
    """中转用例数据不合法。"""


@dataclass(frozen=True)
class TransferCase:
    id: str
    name: str
    from_city: str
    to_city: str
    depart_date: str            # YYYY-MM-DD（由 offset 现算，不写死）
    depart_offset_days: int
    min_cards: int              # 方案条数下界
    min_two_leg_ratio: float    # 「能抽出两段车次号」的卡片占比下界
    tags: tuple[str, ...]       # 分类标签，报告分组用（common() 要求非空）
    expected: dict[str, Any]    # 预期契约（common() 要求非空，见 parse_cases 的注释）

    @property
    def param_id(self) -> str:
        """pytest 报告里的用例名：tr-hk-sz-longhaul-T+4-2026-11-06"""
        return f"{self.id}-T+{self.depart_offset_days}-{self.depart_date}"


def _require(raw: dict, key: str, where: str) -> Any:
    if key not in raw or raw[key] in (None, ""):
        raise DataError(f"{where}: 缺少必填字段 {key!r}")
    return raw[key]


def parse_cases(raw_cases: list[dict], today: date) -> list[TransferCase]:
    """把 YAML 里的 cases 解析成 `TransferCase` 列表；任何一处不合法都抛 `DataError`。"""
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DataError("中转用例必须是非空列表")

    cases: list[TransferCase] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise DataError(f"每条用例必须是映射，收到 {type(raw).__name__}")
        case_id = str(_require(raw, "id", "某条用例")).strip()
        where = f"用例 {case_id}"
        if case_id in seen:
            raise DataError(f"{where}: id 重复")
        seen.add(case_id)

        from_city = str(_require(raw, "from_city", where)).strip()
        to_city = str(_require(raw, "to_city", where)).strip()
        if from_city == to_city:
            raise DataError(f"{where}: 出发与到达不能相同（{from_city}）")

        offset = _require(raw, "depart_offset_days", where)
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise DataError(f"{where}: depart_offset_days 必须是整数，收到 {type(offset).__name__}")
        if not (MIN_OFFSET_DAYS <= offset <= MAX_OFFSET_DAYS):
            raise DataError(
                f"{where}: depart_offset_days={offset} 越界，必须在 "
                f"+{MIN_OFFSET_DAYS}..+{MAX_OFFSET_DAYS}（预售期，见 settings.MAX_PRESALE_DAYS）"
            )

        min_cards = _require(raw, "min_cards", where)
        if not isinstance(min_cards, int) or isinstance(min_cards, bool) or min_cards < 1:
            raise DataError(f"{where}: min_cards 必须是 >=1 的整数")

        ratio = raw.get("min_two_leg_ratio", 0.95)
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            raise DataError(f"{where}: min_two_leg_ratio 必须是数字")
        if not (0 < float(ratio) <= 1):
            raise DataError(f"{where}: min_two_leg_ratio 必须在 (0, 1] 内，收到 {ratio}")

        cases.append(
            TransferCase(
                id=case_id,
                name=str(raw.get("name") or case_id),
                from_city=from_city,
                to_city=to_city,
                depart_date=(today + timedelta(days=offset)).isoformat(),
                depart_offset_days=offset,
                min_cards=int(min_cards),
                min_two_leg_ratio=float(ratio),
                tags=tuple(str(t) for t in (raw.get("tags") or ["transfer"])),
                # 预期契约：中转轨道只对「HTTP 能拿到结果页」有稳定预期。
                # 页面标题「中转方案推荐」不写进这里，由用例直接比对 POM 里的常量 ——
                # 否则同一句话会有两处事实源，改一处忘另一处就是静默漏测。
                expected=dict(raw.get("expected") or {"http_status": 200}),
            )
        )
    return cases


def load_cases(path: str | Path, today: date | None = None) -> list[TransferCase]:
    """从 YAML 文件读取中转用例。文件结构：`cases:` 下的一串映射。"""
    target = Path(path)
    if not target.is_file():
        raise DataError(f"中转用例文件不存在：{target}")
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "cases" not in data:
        raise DataError(f"{target}: 顶层必须是含 cases 的映射")
    return parse_cases(data["cases"], today or date.today())
