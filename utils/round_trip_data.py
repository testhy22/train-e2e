"""往返用例数据：解析 YAML → `RoundTripCase`（含日期解析）。

为什么单独一个模块，不塞进 `utils/data_loader.py`：
  `data_loader.Case` 是**单程**契约（一个 depart_date、一条 URL、一个 min_results），
  往返多出 nights / return_date / train_types / prefer_window 四个字段。
  往同一个 dataclass 里加可选字段，会让单程那 27 项单测都要跟着处理"这个字段可能是 None"，
  换来的只是省一个文件。酒店（hotel_data）与机票（flight_data）已经是这个先例。

两条硬约束（都是踩过的）：
1. **出发日**必须落在 +3..+15（预售期）。越界抛错，不静默夹取 —— 夹取会把
   「数据写错」变成「跑了一条别人看不出问题的用例」。
2. **返程日**只要求晚于出发日，**不设上限**。依据：2026-11-02 用户截图里
   `dDate=2026-11-06&rDate=2026-12-09`（相差 33 天）能被页面正常接受并渲染出
   去程 56 / 返程 58 条。所以"返程也受 15 天预售期"是错的假设，不写进去。
   代价是数据里 nights 写大了可能返程未开售、结果 0 条 —— 这属于数据问题，
   由用例红出来，不由 loader 猜。

本模块不依赖 pytest、不触网，可单独导入与单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from config import settings

# 与单程共用同一套边界常数，避免两处各写一份数字（预售期唯一事实源在 settings）。
MIN_OFFSET_DAYS = 3
MAX_OFFSET_DAYS = settings.MAX_PRESALE_DAYS

# 车次字头闭集。写 D 之类的单字母，不写 "G/D" 这种带斜杠的写法 —— 后者要额外解析。
TRAIN_TYPE_LETTERS: frozenset[str] = frozenset("GDCZTK")

# 城市组合分类（只作标签与报告分组，不参与断言）
CITY_SCOPES: frozenset[str] = frozenset({"same_city", "cross_city", "long_haul"})

# 出发时段偏好 → 左闭右开小时区间。软指标，只统计不判定。
WINDOW_HOURS: dict[str, tuple[int, int]] = {
    "early": (0, 6),
    "morning": (6, 12),
    "afternoon": (12, 18),
    "night": (18, 24),
}

REQUIRED_FIELDS = ("id", "from_city", "to_city", "nights")


class DataError(ValueError):
    """往返用例数据不合法。"""


@dataclass(frozen=True)
class RoundTripCase:
    id: str
    from_city: str
    to_city: str
    city_scope: str
    train_types: tuple[str, ...]
    prefer_window: str
    depart_date: str            # YYYY-MM-DD
    return_date: str            # YYYY-MM-DD
    nights: int
    min_results_each_leg: int
    depart_offset_days: int     # 解析后的真实偏移（weekday 锚点也换算成它）
    anchor: str                 # "offset" | "weekday"，报告里区分"这天的来源"
    name: str = ""
    expected_keyword: str = ""
    tags: tuple[str, ...] = ()
    expected: dict[str, Any] = field(default_factory=dict)

    @property
    def param_id(self) -> str:
        """pytest 报告里的用例名：rt-hk-sz-weekend-T+4-2026-11-06~11-08"""
        return f"{self.id}-T+{self.depart_offset_days}-{self.depart_date}~{self.return_date[5:]}"


# ---------------------------------------------------------------- 日期解析


def resolve_offset(offset_days: int, today: date) -> str:
    """偏移量 → YYYY-MM-DD。区间与"落在过去"都在这里拦。"""
    if not MIN_OFFSET_DAYS <= offset_days <= MAX_OFFSET_DAYS:
        raise DataError(
            f"depart_offset_days 必须在 +{MIN_OFFSET_DAYS}..+{MAX_OFFSET_DAYS}（预售期），"
            f"收到 {offset_days:+d}"
        )
    resolved = today + timedelta(days=offset_days)
    if resolved < today:
        raise DataError(f"解析出的出发日 {resolved} 早于今天 {today}，检查系统时钟或时区")
    return resolved.isoformat()


def resolve_weekday(weekday: int, today: date) -> tuple[str, int]:
    """找出「不早于 +3 天的下一个该星期几」，返回 (日期, 偏移天数)。

    weekday 用 Python 口径：0=周一 … 6=周日。

    为什么要这一步：写 `depart_offset_days: 4` 在今天可能是周三，明天就变成周四 ——
    「周五去周日回」这个语义靠 offset 表达不出来。而按星期几锚定，
    用例在任何一天执行都真的是周末票。

    越界处理（预售期 15 天）：如果算出来的日子超出预售期，就**往前退一周**
    （仍然是同一个星期几）。退完还 <+3 天就报错 —— 那时说明这个 anchor 在当前日期下无解，
    该改数据，不该让 loader 悄悄挪。
    """
    if not 0 <= weekday <= 6:
        raise DataError(f"depart_weekday 必须是 0(周一)..6(周日)，收到 {weekday}")

    earliest = today + timedelta(days=MIN_OFFSET_DAYS)
    candidate = earliest + timedelta(days=(weekday - earliest.weekday()) % 7)
    if (candidate - today).days > MAX_OFFSET_DAYS:
        candidate -= timedelta(days=7)

    offset = (candidate - today).days
    if not MIN_OFFSET_DAYS <= offset <= MAX_OFFSET_DAYS:
        raise DataError(
            f"depart_weekday={weekday} 在今天 {today} 无解：最近的该星期几是 T+{offset}，"
            f"不在 +{MIN_OFFSET_DAYS}..+{MAX_OFFSET_DAYS} 内"
        )
    return candidate.isoformat(), offset


# ---------------------------------------------------------------- 字段校验（逐条）


def _text(value: Any, field_name: str, case_id: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DataError(f"{case_id}: {field_name} 必须是字符串，收到 {value!r}")
    text = value.strip()
    if not text and not allow_empty:
        raise DataError(f"{case_id}: {field_name} 不能为空")
    return text


def _int(value: Any, field_name: str, case_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError(f"{case_id}: {field_name} 必须是整数，收到 {value!r}")
    return value


def _train_types(value: Any, case_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise DataError(f"{case_id}: train_types 必须是非空列表，收到 {value!r}")
    out: list[str] = []
    for item in value:
        letter = str(item).strip().upper()
        if letter not in TRAIN_TYPE_LETTERS:
            raise DataError(
                f"{case_id}: train_types 里 {item!r} 不是合法字头"
                f"（只允许 {''.join(sorted(TRAIN_TYPE_LETTERS))}）"
            )
        if letter not in out:
            out.append(letter)
    return tuple(out)


def _cases_of(raw: object, file: Path) -> list:
    """取用例列表，同时接受裸列表与 `version: 1` + `cases:` 包装。"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if raw.get("version") != 1:
            raise DataError(f"{file}: version 必须是 1，收到 {raw.get('version')!r}")
        cases = raw.get("cases")
        if not isinstance(cases, list):
            raise DataError(f"{file}: cases 必须是列表，收到 {type(cases).__name__}")
        return cases
    raise DataError(f"{file}: 顶层必须是列表或含 cases 的映射")


# ---------------------------------------------------------------- 入口


def parse_case(item: dict, today: date) -> RoundTripCase:
    """单条用例 → RoundTripCase。today 可注入，便于确定性单测。"""
    for field_name in REQUIRED_FIELDS:
        if field_name not in item:
            raise DataError(f"用例缺字段 {field_name}：{item.get('id') or item}")

    case_id = _text(item["id"], "id", "?", allow_empty=False)
    if not case_id.replace("-", "").replace("_", "").isalnum() or not case_id.isascii():
        raise DataError(f"{case_id}: id 只能用 ASCII 字母数字与 - _")

    nights = _int(item["nights"], "nights", case_id)
    if nights < 1:
        raise DataError(f"{case_id}: nights 必须 >= 1（往返至少要住 1 晚），收到 {nights}")

    has_offset = "depart_offset_days" in item
    has_weekday = "depart_weekday" in item
    if has_offset == has_weekday:
        raise DataError(
            f"{case_id}: depart_offset_days 与 depart_weekday 必须**二选一**，"
            f"当前 offset={has_offset} weekday={has_weekday}"
        )

    if has_offset:
        depart_date = resolve_offset(_int(item["depart_offset_days"], "depart_offset_days", case_id), today)
        depart_offset_days = _int(item["depart_offset_days"], "depart_offset_days", case_id)
        anchor = "offset"
    else:
        depart_date, depart_offset_days = resolve_weekday(
            _int(item["depart_weekday"], "depart_weekday", case_id), today
        )
        anchor = "weekday"

    depart = date.fromisoformat(depart_date)
    return_date = (depart + timedelta(days=nights)).isoformat()

    city_scope = _text(item.get("city_scope", "cross_city"), "city_scope", case_id)
    if city_scope not in CITY_SCOPES:
        raise DataError(f"{case_id}: city_scope 必须是 {sorted(CITY_SCOPES)} 之一，收到 {city_scope!r}")

    prefer_window = _text(item.get("prefer_window", "morning"), "prefer_window", case_id)
    if prefer_window not in WINDOW_HOURS:
        raise DataError(
            f"{case_id}: prefer_window 必须是 {sorted(WINDOW_HOURS)} 之一，收到 {prefer_window!r}"
        )

    min_results = _int(item.get("min_results_each_leg", 1), "min_results_each_leg", case_id)
    if min_results < 1:
        raise DataError(f"{case_id}: min_results_each_leg 必须 >= 1，收到 {min_results}")

    return RoundTripCase(
        id=case_id,
        from_city=_text(item["from_city"], "from_city", case_id),
        to_city=_text(item["to_city"], "to_city", case_id),
        city_scope=city_scope,
        train_types=_train_types(item.get("train_types", list(TRAIN_TYPE_LETTERS)), case_id),
        prefer_window=prefer_window,
        depart_date=depart_date,
        return_date=return_date,
        nights=nights,
        min_results_each_leg=min_results,
        depart_offset_days=depart_offset_days,
        anchor=anchor,
        name=str(item.get("name", "")).strip(),
        expected_keyword=_text(item.get("expected_keyword", ""), "expected_keyword", case_id,
                               allow_empty=True),
        tags=tuple(str(t) for t in (item.get("tags") or [])),
        expected=dict(item.get("expected") or {}),
    )


def load_cases(path: str | Path, today: date | None = None) -> list[RoundTripCase]:
    """读取往返用例文件。today 只用于测试注入，生产环境取本机当天。"""
    file = Path(path)
    if not file.is_file():
        raise DataError(f"用例文件不存在：{file}")

    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DataError(f"YAML 解析失败：{exc}") from exc

    items = _cases_of(raw, file)
    if not items:
        raise DataError("往返用例文件里没有任何用例")

    base_day = today or datetime.now().date()
    cases: list[RoundTripCase] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise DataError(f"第 {index + 1} 条用例不是键值对")
        try:
            case = parse_case(item, base_day)
        except DataError as exc:
            raise DataError(f"第 {index + 1} 条：{exc}") from exc
        if case.id in seen:
            raise DataError(f"用例 id 重复：{case.id}")
        seen.add(case.id)
        cases.append(case)
    return cases


def param_ids(cases: list[RoundTripCase]) -> list[str]:
    return [c.param_id for c in cases]
