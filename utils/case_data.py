"""用例数据的统一入口：定位文件、校验版本包装、委派解析。

只统一三件事，其余一律不重复实现：

1. **目录** —— `data/`，可用环境变量 `CASE_DATA_DIR` 覆盖。
   用环境变量而不是 `pytest_addoption`：参数化在 import 期就要拿到用例，
   那时 `--data-dir` 还没解析完。`scripts/run-headed.sh` 会自然透传环境变量。
2. **包装** —— `version: 1` + `suite` + `marker` + `cases:`。版本不认识就报错，不猜。
3. **入口名** —— `load_hotel_search_cases()` / `load_train_smoke_cases()` / `case_ids()`。

解析与字段校验留在 `utils/hotel_data.py` 与 `utils/data_loader.py`：
它们各有既有的边界检查（偏移区间、id 唯一、id 不含路径分隔符、日期不落过去），
且火车票那套有 27 项单测保护。在这里重写第二套只会让两边慢慢分叉。

为什么不合成一个 dataclass：酒店是「城市 + 晚数 + 房间住客」，火车票是
「区间 + 预售期」，可变字段完全不同，硬合会造出一半字段恒为 None 的结构。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from config import settings
from utils import (
    case_rotation,
    data_loader,
    flight_data,
    hotel_data,
    round_trip_data,
    transfer_data,
)

DATA_VERSION = 1
DEFAULT_DATA_DIR = settings.PROJECT_ROOT / "data"

HOTEL_CASES = Path("hotels") / "search_cases.yaml"
TRAIN_CASES = Path("trains") / "smoke_cases.yaml"
TRAIN_ROUND_TRIP_CASES = Path("trains") / "round_trip_cases.yaml"
TRAIN_TRANSFER_CASES = Path("trains") / "transfer_cases.yaml"
FLIGHT_CASES = Path("flights") / "search_cases.yaml"


def data_dir() -> Path:
    """用例数据根目录。`CASE_DATA_DIR` 优先，便于本地/CI 指向同一份或临时数据。"""
    raw = os.getenv("CASE_DATA_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_DATA_DIR


def _meta(path: Path) -> dict[str, Any]:
    """读包装头（不含 cases），并校验版本。

    文件不存在或版本不对都直接抛 —— 参数化期间拿到一份空列表比报错更难查。
    """
    if not path.is_file():
        raise FileNotFoundError(f"用例数据不存在：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} 必须是 version/cases 包装，收到 {type(raw).__name__}")
    version = raw.get("version")
    if version != DATA_VERSION:
        raise ValueError(f"{path} 的 version 必须是 {DATA_VERSION}，收到 {version!r}")
    if not isinstance(raw.get("cases"), list):
        raise TypeError(f"{path} 的 cases 必须是列表，收到 {type(raw.get('cases')).__name__}")
    return {k: v for k, v in raw.items() if k != "cases"}


def _resolve(explicit: str | Path | None, relative: Path) -> Path:
    return Path(explicit) if explicit else data_dir() / relative


def load_hotel_search_cases(
    path: str | Path | None = None, today: date | None = None
) -> list[hotel_data.HotelCase]:
    """酒店搜索用例。默认 `data/hotels/search_cases.yaml`。"""
    target = _resolve(path, HOTEL_CASES)
    _meta(target)          # 先校验包装，再交给解析器（解析器也兼容裸列表）
    return hotel_data.load_cases(target, today=today)


def load_train_smoke_cases(
    path: str | Path | None = None, today: date | None = None
) -> list[data_loader.Case]:
    """火车票冒烟用例。默认 `data/trains/smoke_cases.yaml`。"""
    target = _resolve(path, TRAIN_CASES)
    _meta(target)
    return data_loader.load_cases(target, today=today)


def load_flight_cases(
    path: str | Path | None = None, today: date | None = None
) -> list[flight_data.FlightCase]:
    """机票搜索用例。默认 `data/flights/search_cases.yaml`（2026-11-01 新增）。"""
    target = _resolve(path, FLIGHT_CASES)
    _meta(target)
    return flight_data.load_cases(target, today=today)


def load_round_trip_cases(
    path: str | Path | None = None, today: date | None = None
) -> list[round_trip_data.RoundTripCase]:
    """火车票**往返**用例（**全量**）。默认 `data/trains/round_trip_cases.yaml`。

    注意：这是全量 9 条，供数据校验与单测使用。**用例参数化请用
    `round_trip_cases_for_run()`** —— 它每次只给 1 条（轮换 + 随机，用户 2026-11-03 裁决）。

    与 `load_train_smoke_cases` 分开：往返多出 nights/return_date/train_types，
    且入口是首页表单（点「往返」+ 两个日历），两条轨道的数据契约不同。
    """
    target = _resolve(path, TRAIN_ROUND_TRIP_CASES)
    _meta(target)
    return round_trip_data.load_cases(target, today=today)


def load_transfer_cases(
    path: str | Path | None = None, today: date | None = None
) -> list[transfer_data.TransferCase]:
    """火车票**中转**用例（全量）。默认 `data/trains/transfer_cases.yaml`（2026-11-02 新增）。

    与往返分开：中转的阈值是「方案条数下界 + 两段车次号覆盖率下界」，
    往返是「每栏条数 + 车次号覆盖率」，字段完全不同，硬合会造出一半恒为 None 的结构。
    """
    target = _resolve(path, TRAIN_TRANSFER_CASES)
    _meta(target)
    return transfer_data.load_cases(target, today=today)


# ---------------------------------------------------------------- 每功能本次运行集
#
# 用户 2026-11-03 裁决：数据准备多条，**每次随机挑 1 条**跑 —— 省时间，靠多次运行覆盖全量。
# 挑选策略（轮换 + 随机、为什么不用纯随机、怎么复现）全写在 `utils/case_rotation.py`，
# 这里只做委派，不重复实现第二套。
#
# 为什么在 loader 层裁、而不是 pytest `-k` 过滤：参数化在 import 期就要拿到用例列表，
# `-k` 是收集之后才过滤 —— "这次到底跑了哪些"只存在于命令行里，报告与门禁都看不见。
#
# 为什么保留全量数据文件：数据是资产（往返 9 条覆盖 3 种城市组合 / 6 个车型字头 /
# 3..12 天跨度 / 3 个时段），删掉就再也回不来；"跑多少条"是**运行策略**，不是数据本身。


def take_quota(kind: str, cases: list) -> list:
    """本次运行集：钉 id > 全量 > 轮换 + 随机。开关与算法见 `utils/case_rotation.py`。"""
    return case_rotation.pick(kind, cases)


def hotel_cases_for_run(
    path: str | Path | None = None, today: date | None = None
) -> list[hotel_data.HotelCase]:
    """酒店的**本次运行集**：默认 1 条（数据共 4 条，跑 4 次覆盖一轮）。

    与 `load_hotel_search_cases()` 并存：那个是**全量**，供数据校验与单测使用；
    用例参数化改用本函数，才能落实「每次每功能只跑 1 条」（2026-11-03 裁决）。
    """
    return take_quota("hotels", load_hotel_search_cases(path, today))


def flight_cases_for_run(
    path: str | Path | None = None, today: date | None = None
) -> list[flight_data.FlightCase]:
    """机票的**本次运行集**：默认 1 条（数据共 5 条，跑 5 次覆盖一轮）。"""
    return take_quota("flights", load_flight_cases(path, today))


def round_trip_cases_for_run(
    path: str | Path | None = None, today: date | None = None
) -> list[round_trip_data.RoundTripCase]:
    """往返的**本次运行集**：默认 1 条（轮换挑的），`E2E_CASE_QUOTA=3` 或 `E2E_FULL=1` 放开。"""
    return take_quota("round_trip", load_round_trip_cases(path, today))


def transfer_cases_for_run(
    path: str | Path | None = None, today: date | None = None
) -> list[transfer_data.TransferCase]:
    """中转的**本次运行集**：默认 1 条（数据共 3 条，跑 3 次覆盖一轮）。"""
    return take_quota("transfer", load_transfer_cases(path, today))


def train_smoke_cases_for_run(
    path: str | Path | None = None, today: date | None = None
) -> list[data_loader.Case]:
    """单程的**本次运行集**：默认 1 条（数据共 3 条，跑 3 次覆盖一轮）。"""
    return take_quota("train_smoke", load_train_smoke_cases(path, today))


def case_ids(cases: list) -> list[str]:
    """供 `pytest.mark.parametrize(ids=...)` 使用。全部是 ASCII。"""
    return [c.param_id for c in cases]


def describe(base: str | Path | None = None) -> str:
    """一行数据版本摘要，写进验收报告：suite=hotels marker=hotels cases=3 version=1"""
    root = Path(base) if base else data_dir()
    parts: list[str] = []
    for relative, fallback_suite in (
        (HOTEL_CASES, "hotels"),
        (TRAIN_CASES, "trains"),
        (TRAIN_ROUND_TRIP_CASES, "trains_round_trip"),
        (TRAIN_TRANSFER_CASES, "trains_transfer"),
    ):
        target = root / relative
        if not target.is_file():
            parts.append(f"{fallback_suite}: 缺失")
            continue
        meta = _meta(target)
        cases = yaml.safe_load(target.read_text(encoding="utf-8"))["cases"]
        parts.append(
            f"suite={meta.get('suite', fallback_suite)} "
            f"marker={meta.get('marker', '-')} "
            f"cases={len(cases)} version={meta['version']}"
        )
    return " | ".join(parts)
