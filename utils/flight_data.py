"""机票搜索用例数据：把「偏移天数」解析成具体出发日期。

与酒店（`utils/hotel_data.py`）、火车票（`utils/data_loader.py`）分开，理由和
酒店那份文件头写的一样：三者可变字段完全不同（酒店是城市+晚数+房间住客，
火车票是区间+预售期，机票是区间+舱等+人数），合进一个 dataclass 会造出
一半字段恒为 None 的结构。

**机票可订窗口没有官方硬口径**：不像火车票有 12306 的 15 天预售期可以引用，
所以这里只给工程边界（1…60 天），取它纯粹是为了「别去查远期数据」，
不要理解成「携程机票最多只能订 60 天」。⚠️[未查实]

统一入口（默认路径、版本包装）在 `utils/case_data.py`，本模块只做解析与校验。
本模块不依赖 pytest，可单独导入与单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

MIN_OFFSET_DAYS = 1
MAX_OFFSET_DAYS = 60

REQUIRED_FIELDS = ("id", "from_city", "to_city", "depart_offset_days")


class FlightDataError(ValueError):
    """机票用例数据不合法。"""


@dataclass(frozen=True)
class FlightCase:
    id: str
    from_city: str
    to_city: str
    depart_offset_days: int
    depart: date
    adults: int = 1
    name: str = ""
    tags: tuple[str, ...] = ()
    expected: dict[str, Any] | None = None

    @property
    def param_id(self) -> str:
        """报告里的用例名，全 ASCII：sha-bjs-T+7-2026-11-08（理由同酒店）。"""
        return f"{self.id}-T+{self.depart_offset_days}-{self.depart.isoformat()}"


def _require_int(raw: dict, key: str, default: int | None = None) -> int:
    value = raw.get(key, default)
    if value is None:
        raise FlightDataError(f"用例 {raw.get('id')!r} 缺少字段 {key}")
    if not isinstance(value, int) or isinstance(value, bool):
        raise FlightDataError(f"用例 {raw.get('id')!r} 的 {key} 必须是整数，收到 {value!r}")
    return value


def parse_case(raw: dict, today: date | None = None) -> FlightCase:
    today = today or date.today()
    case_id = str(raw.get("id", "")).strip()
    if not case_id:
        raise FlightDataError("用例缺少 id")
    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise FlightDataError(f"用例 {case_id!r} 缺少字段 {field}")

    from_city = str(raw["from_city"]).strip()
    to_city = str(raw["to_city"]).strip()
    if not from_city or not to_city:
        raise FlightDataError(f"用例 {case_id!r} 的 from_city / to_city 不能为空")
    if from_city == to_city:
        raise FlightDataError(f"用例 {case_id!r} 的出发地与目的地相同：{from_city}")

    offset = _require_int(raw, "depart_offset_days")
    if not MIN_OFFSET_DAYS <= offset <= MAX_OFFSET_DAYS:
        raise FlightDataError(
            f"用例 {case_id!r} 的 depart_offset_days={offset} 越界"
            f"（{MIN_OFFSET_DAYS}…{MAX_OFFSET_DAYS}）"
        )

    adults = _require_int(raw, "adults", 1)
    if adults < 1:
        raise FlightDataError(f"用例 {case_id!r} 的 adults 必须 >= 1，收到 {adults}")

    return FlightCase(
        id=case_id,
        from_city=from_city,
        to_city=to_city,
        depart_offset_days=offset,
        depart=today + timedelta(days=offset),
        adults=adults,
        name=str(raw.get("name", "")).strip(),
        tags=tuple(str(t) for t in (raw.get("tags") or [])),
        expected=dict(raw.get("expected") or {}),
    )


def load_cases(path: Path, today: date | None = None) -> list[FlightCase]:
    if not path.is_file():
        raise FlightDataError(f"找不到用例数据：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        cases = raw
    elif isinstance(raw, dict):
        version = raw.get("version")
        if version != 1:
            raise FlightDataError(f"{path} 的 version 必须是 1，收到 {version!r}")
        cases = raw.get("cases")
        if not isinstance(cases, list):
            raise FlightDataError(f"{path} 的 cases 必须是列表，收到 {type(cases).__name__}")
    else:
        raise FlightDataError(f"{path} 的顶层必须是列表或含 cases 的映射")
    if not cases:
        raise FlightDataError(f"{path} 必须至少含一条用例")
    return [parse_case(item, today=today) for item in cases]
