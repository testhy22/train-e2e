"""酒店搜索用例数据：把「偏移天数 + 晚数」解析成具体日期。

与 `utils/data_loader.py`（火车票）分开是刻意的：两者的可变字段完全不同
（一边是区间+预售期，一边是城市+晚数+房间住客），硬塞进一个 dataclass 会造出
一半字段恒为 None 的结构。共用的是「偏移量而非写死日期」这条原则。

统一入口（定位默认路径、版本包装）在 `utils/case_data.py`，本模块只负责解析与校验。

本模块不依赖 pytest，可单独导入与单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

# 工程边界，不是业务口径。⚠️[未查实] 携程酒店可订窗口上限未实测，
# 这里取 60 天只是为了避免查询远期数据、不当作「携程最多只能订 60 天」的依据。
MIN_OFFSET_DAYS = 1
MAX_OFFSET_DAYS = 60

REQUIRED_FIELDS = ("id", "city", "checkin_offset_days", "nights")


class HotelDataError(ValueError):
    """酒店用例数据不合法。"""


@dataclass(frozen=True)
class HotelCase:
    id: str
    city: str
    checkin_offset_days: int
    nights: int
    rooms: int
    adults: int
    children: int
    checkin: date
    checkout: date
    name: str = ""
    tags: tuple[str, ...] = ()
    expected: dict[str, Any] | None = None

    @property
    def people(self) -> int:
        """「N位」= 成人 + 儿童（页面用「X间, Y位」表达）。"""
        return self.adults + self.children

    @property
    def param_id(self) -> str:
        """报告里的用例名，**全 ASCII**：sh-2n1r1a-T+7-2026-11-08

        不再拼「2晚-1间1位」这类中文：pytest 会把中文 id 写成 `\\u665a` 转义，
        junit 和 CI 日志里读不出来。语义已经编码在 id 里（2n1r1a = 2晚1间1大人）。
        """
        return f"{self.id}-T+{self.checkin_offset_days}-{self.checkin.isoformat()}"


def _require_int(raw: dict, key: str, default: int | None = None) -> int:
    value = raw.get(key, default)
    if value is None:
        raise HotelDataError(f"用例 {raw.get('id')!r} 缺少字段 {key}")
    if not isinstance(value, int) or isinstance(value, bool):
        raise HotelDataError(f"用例 {raw.get('id')!r} 的 {key} 必须是整数，收到 {value!r}")
    return value


def parse_case(raw: dict, today: date | None = None) -> HotelCase:
    today = today or date.today()
    case_id = str(raw.get("id", "")).strip()
    if not case_id:
        raise HotelDataError("用例缺少 id")

    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise HotelDataError(f"用例 {case_id!r} 缺少字段 {field}")

    city = str(raw["city"]).strip()
    if not city:
        raise HotelDataError(f"用例 {case_id!r} 的 city 为空")

    offset = _require_int(raw, "checkin_offset_days")
    if not MIN_OFFSET_DAYS <= offset <= MAX_OFFSET_DAYS:
        raise HotelDataError(
            f"用例 {case_id!r} 的 checkin_offset_days={offset} 越界"
            f"（{MIN_OFFSET_DAYS}…{MAX_OFFSET_DAYS}）"
        )

    nights = _require_int(raw, "nights")
    if nights < 1:
        raise HotelDataError(f"用例 {case_id!r} 的 nights 必须 >= 1，收到 {nights}")

    rooms = _require_int(raw, "rooms", 1)
    adults = _require_int(raw, "adults", 1)
    children = _require_int(raw, "children", 0)
    if rooms < 1 or adults < 1 or children < 0:
        raise HotelDataError(f"用例 {case_id!r} 的房间/住客取值不合法：{rooms}/{adults}/{children}")

    checkin = today + timedelta(days=offset)
    return HotelCase(
        id=case_id,
        city=city,
        checkin_offset_days=offset,
        nights=nights,
        rooms=rooms,
        adults=adults,
        children=children,
        checkin=checkin,
        checkout=checkin + timedelta(days=nights),
        name=str(raw.get("name", "")).strip(),
        tags=tuple(str(t) for t in (raw.get("tags") or [])),
        expected=dict(raw.get("expected") or {}),
    )


def _cases_of(raw: object, path: Path) -> list:
    """取用例列表。同时接受两种顶层写法：

    - 裸列表（历史写法，`docs/PLAN-M0-M1.md` 时期的文件）
    - `version: 1` + `cases:` 包装（现在 `data/` 下的写法）

    包装的意义是给以后留位置（suite / marker / 数据版本），不是为了好看。
    版本号不认识就报错，不猜。
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        version = raw.get("version")
        if version != 1:
            raise HotelDataError(f"{path} 的 version 必须是 1，收到 {version!r}")
        cases = raw.get("cases")
        if not isinstance(cases, list):
            raise HotelDataError(f"{path} 的 cases 必须是列表，收到 {type(cases).__name__}")
        return cases
    raise HotelDataError(f"{path} 的顶层必须是列表或含 cases 的映射")


def load_cases(path: Path, today: date | None = None) -> list[HotelCase]:
    if not path.is_file():
        raise HotelDataError(f"找不到用例数据：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = _cases_of(raw, path)
    if not cases:
        raise HotelDataError(f"{path} 必须至少含一条用例")
    return [parse_case(item, today=today) for item in cases]
