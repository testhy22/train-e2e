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
from utils import data_loader, flight_data, hotel_data

DATA_VERSION = 1
DEFAULT_DATA_DIR = settings.PROJECT_ROOT / "data"

HOTEL_CASES = Path("hotels") / "search_cases.yaml"
TRAIN_CASES = Path("trains") / "smoke_cases.yaml"
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


def case_ids(cases: list) -> list[str]:
    """供 `pytest.mark.parametrize(ids=...)` 使用。全部是 ASCII。"""
    return [c.param_id for c in cases]


def describe(base: str | Path | None = None) -> str:
    """一行数据版本摘要，写进验收报告：suite=hotels marker=hotels cases=3 version=1"""
    root = Path(base) if base else data_dir()
    parts: list[str] = []
    for relative, fallback_suite in ((HOTEL_CASES, "hotels"), (TRAIN_CASES, "trains")):
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
