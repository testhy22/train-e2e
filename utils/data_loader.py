"""读取 YAML 用例数据，把「偏移天数」解析成「具体日期」，并做前置校验。

设计约束（对应裁决 D1）：
1. 偏移量下限 +3、上限 +15（预售期），越界直接报错，不静默夹取。
2. 解析出的日期若落在过去，抛错而不是 skip —— 在真页面测试里「过去日期」意味着
   时钟或时区异常，skip 会把它藏起来。
3. parametrize 的 id 同时带偏移量与解析后的日期，报告里能一眼看出跑的是哪一天。

本模块不依赖 pytest，可单独导入与单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from config import settings

# 下限 +3：避开「今天/明早」临售紧张导致的空结果干扰。
# 上限来自 settings.MAX_PRESALE_DAYS（预售期唯一事实源），此处不重复写数字。
MIN_OFFSET_DAYS = 3
MAX_OFFSET_DAYS = settings.MAX_PRESALE_DAYS

REQUIRED_FIELDS = ("id", "from_city", "to_city", "depart_offset_days")


class DataError(ValueError):
    """测试数据不合法。"""


@dataclass(frozen=True)
class Case:
    id: str
    from_city: str
    to_city: str
    depart_offset_days: int
    depart_date: str          # YYYY-MM-DD，由偏移量解析得到
    min_results: int
    expected_keyword: str     # 仅用于日志与报告，不参与 pass/fail

    @property
    def param_id(self) -> str:
        """pytest 报告里的用例名：sh-bj-T+7-2026-11-07"""
        return f"{self.id}-T+{self.depart_offset_days}-{self.depart_date}"


def _as_int(value, field: str, case_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError(f"{case_id}: {field} 必须是整数，收到 {value!r}")
    return value


def _as_text(value, field: str, case_id: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DataError(f"{case_id}: {field} 必须是字符串，收到 {value!r}")
    text = value.strip()
    if not text and not allow_empty:
        raise DataError(f"{case_id}: {field} 不能为空")
    return text


def resolve_date(offset_days: int, today: date) -> str:
    """偏移量 → YYYY-MM-DD。越界与「落在过去」都在这里拦。"""
    if not MIN_OFFSET_DAYS <= offset_days <= MAX_OFFSET_DAYS:
        raise DataError(
            f"depart_offset_days 必须在 +{MIN_OFFSET_DAYS}..+{MAX_OFFSET_DAYS} 之间，"
            f"收到 {offset_days:+d}"
        )
    resolved = today + timedelta(days=offset_days)
    if resolved < today:
        raise DataError(f"解析出的日期 {resolved} 早于今天 {today}，检查系统时钟或时区")
    return resolved.isoformat()


def load_cases(path: str | Path, today: date | None = None) -> list[Case]:
    """读取用例文件。today 只用于测试注入，生产环境取本机当天。"""
    file = Path(path)
    if not file.is_file():
        raise DataError(f"用例文件不存在：{file}")

    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DataError(f"YAML 解析失败：{exc}") from exc

    if not isinstance(raw, list) or not raw:
        raise DataError("用例文件的顶层必须是非空列表")

    base_day = today or datetime.now().date()
    cases: list[Case] = []
    seen: set[str] = set()

    for index, item in enumerate(raw):
        position = f"第 {index + 1} 条"
        if not isinstance(item, dict):
            raise DataError(f"{position}用例不是键值对")
        for field in REQUIRED_FIELDS:
            if field not in item:
                raise DataError(f"{position}用例缺字段 {field}")

        case_id = _as_text(item["id"], "id", position)
        if not case_id.replace("-", "").replace("_", "").isalnum():
            raise DataError(f"{position}用例 id 只能用字母数字与 - _：{case_id!r}")
        if case_id in seen:
            raise DataError(f"用例 id 重复：{case_id}")
        seen.add(case_id)

        offset = _as_int(item["depart_offset_days"], "depart_offset_days", case_id)
        min_results = _as_int(item.get("min_results", 1), "min_results", case_id)
        if min_results < 1:
            raise DataError(f"{case_id}: min_results 必须 >= 1，收到 {min_results}")

        cases.append(
            Case(
                id=case_id,
                from_city=_as_text(item["from_city"], "from_city", case_id),
                to_city=_as_text(item["to_city"], "to_city", case_id),
                depart_offset_days=offset,
                depart_date=resolve_date(offset, base_day),
                min_results=min_results,
                expected_keyword=_as_text(
                    item.get("expected_keyword", ""), "expected_keyword", case_id, allow_empty=True
                ),
            )
        )

    return cases


def param_ids(cases: list[Case]) -> list[str]:
    """供 pytest.mark.parametrize(ids=...) 使用。"""
    return [c.param_id for c in cases]
