"""自动发现采集器：从被测页面自身提取「正常值域」，写进候选池。

**两条铁律（2026-11-02 用户裁决，改动前先读完）：**

1. **兜底优先于采集。** collect 全程 try/except 包裹：任何异常只记入
   返回值的 ``collect_errors``，**不抛出、不影响 passed/failed、不阻断用例**。
   采集器一个 bug 不许把 L1 搞红 —— 验收项就包含「故意喂错 selector 仍全绿」。
2. **只读 DOM，零新增导航、零额外请求。** 采集挂在用例内「结果页已断言」的
   确定时刻（显式调用式，hook 式已否决 —— teardown 时页面停在末尾状态，
   采到的是静默错数据，比空值更糟）。

hint 形态：参数化 JS 提取器（page.evaluate）。为什么不是 CSS 选择器：
携程筛选面板按「组标题」（车型/坐席/星级…）组织，CSS 无法按文本选组；
JS 提取器在页面内一次遍历完成「选组 + 取条目 + 规范化」，仍是只读 DOM。

selector 来源：**实测**（tools/probes/probe_dom_filters.py 两轮探针，
输出 reports/probe/dom_filters_*.txt，2026-11-02）。站点改版导致提取器
失配时返回空列表并记 not_present —— 这是覆盖报告的一部分，不是错误；
修之前先重跑探针。

快照存储范围（用户裁决）：只存 selector/提取器标识 + 采集到的值列表 +
脱敏 URL（去掉非白名单 query），**不存 HTML 原文**；可复现性靠「跑 probe
复现该值呈现」。

用法（测试内显式调用）::

    from utils import discover
    if discover.enabled():
        discover.collect(page, site="trains", source_case=case.id)
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from utils import candidate_pool

#: 环境开关：DISCOVER=1 才采集，默认关 —— 关闭时与现状逐字节零差异。
ENV_FLAG = "DISCOVER"

#: 闸 3：每轮（一个 pytest 进程）每维度最多进池条数，防单轮爆炸。
PER_DIMENSION_LIMIT = 3

#: 闸 3 的进程级计数：round_counts 必须跨 collect 调用共享，否则「每轮」退化成
#: 「每用例」，一个 13 条的 L1 会把同一维度灌到 13×3 条（2026-11-02 Run B 实测踩过）。
_ROUND_COUNTS: dict[str, int] = {}


def _round_count(dimension: str) -> int:
    return _ROUND_COUNTS.get(f"{_round_id()}:{dimension}", 0)


def _bump_round_count(dimension: str) -> None:
    key = f"{_round_id()}:{dimension}"
    _ROUND_COUNTS[key] = _ROUND_COUNTS.get(key, 0) + 1

#: 每个提取器最多读取的节点数（预算 0.5s/条）。
_MAX_NODES = 40

#: 明确不是「值」的页面通用按钮文案，提取时剔除。
_BLACKLIST = {"更多", "重置", "展开", "收起", "清空", "全部重置", "确定"}

#: 私有区图标字符（携程图标字体，如 \ue60c）—— 出现在值文本里一律剥掉。
_PUA = re.compile(r"[\ue000-\uf8ff]")
_WS = re.compile(r"\s+")


def normalize(value: str) -> str:
    """值规范化：剥私有区图标、去首尾、折叠空白。候选值以页面可读文本为准。"""
    return _WS.sub(" ", _PUA.sub("", value)).strip()


def enabled() -> bool:
    return os.getenv(ENV_FLAG, "").strip() == "1"


def _round_id() -> str:
    """轮次标识：pytest 进程启动时刻（模块首次导入即定，全进程一致）。"""
    global _ROUND
    try:
        return _ROUND
    except NameError:
        _ROUND = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        return _ROUND


# ---------------------------------------------------------------- 脱敏与规范化

_QUERY_ALLOW = ("city", "dep", "arr", "cabin")  # 允许保留的 query 键前缀


def sanitize_url(url: str) -> str:
    """URL 脱敏：保留 scheme+host+path；query 只留白名单前缀键。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    kept = []
    for chunk in parts.query.split("&"):
        if not chunk:
            continue
        key = chunk.split("=", 1)[0].lower()
        if any(key.startswith(p) for p in _QUERY_ALLOW):
            kept.append(chunk)
    query = f"?{'&'.join(kept)}" if kept else ""
    return f"{parts.scheme}://{parts.netloc}{parts.path}{query}"


# ---------------------------------------------------------------- hint 接口
#
# 统一两种形态（2026-11-02 用户裁决），对外只有 collect 一个入口，调用点不感知差异：
#
#   Hint = {
#     "type": "css" | "js",
#     "selector":   str        # type=css：query_selector_all 用的选择器
#     "extractor":  str        # type=js：参数化 JS 函数体（只读 DOM）
#     "args":       dict       # type=js：传给函数体的参数（如 group_title）
#     "dimension":  str
#     "expected_shape": "list[str]"   # 形状不符记 collect_errors，不抛异常
#   }
#
# 为什么火车票用 js 形态：筛选面板按「组标题」（车型/坐席…）组织，
# CSS 无法按文本选组。JS 提取器**必须参数化**（参数走 args），同一函数体
# 可复用于不同维度；禁止为某站写死一段不可复用的 JS。
# JS 提取器铁律：只读 DOM、不发请求、不触发滚动/点击、不等待，拿到值立即返回。

#: 期望形状（目前只有一种；将来加 dict 形态时在这里登记）
SHAPES = {"list[str]"}

#: 通用「标题分组」JS 提取器。args：
#:   group_title  组标题前缀（如「车型」「坐席」「星级」）
#:   group_sel    组容器选择器
#:   title_sels   组内标题元素选择器候选（按序试）
#:   item_sel     条目选择器（在组容器内 query）
_JS_GROUPED = """(args) => {
    const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    const { group_title, group_sel, title_sels, item_sel } = args;
    for (const group of document.querySelectorAll(group_sel)) {
        let title = '';
        for (const sel of title_sels) {
            const el = group.querySelector(sel);
            if (el) { title = norm(el.innerText); break; }
        }
        if (!title.startsWith(group_title)) continue;
        const out = [];
        for (const item of group.querySelectorAll(item_sel)) {
            const t = norm(item.innerText);
            if (t) out.push(t);
        }
        return out;
    }
    return null;
}"""

#: 酒店筛选组提取器（酒店面板结构与火车不同：标题在 .filter-item-title 内，
#: 条目是 .filter-container 下的叶子节点）。args：group_title。
_JS_HOTEL_GROUP = """(args) => {
    const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    const { group_title } = args;
    for (const wrap of document.querySelectorAll('.filter-main-items .filter-content-wrap')) {
        const tEl = wrap.querySelector('.filter-item-title p, .filter-item-title-text');
        if (!tEl || !norm(tEl.innerText).startsWith(group_title)) continue;
        const out = [];
        for (const el of wrap.querySelectorAll('.filter-container *')) {
            if (el.childElementCount !== 0) continue;
            if (el.closest('.filter-item-title')) continue;
            const t = norm(el.innerText);
            if (t && t.length <= 24) out.push(t);
        }
        return out;
    }
    return null;
}"""

#: 排序条目（火车）：.sort-left li .tit
_JS_TRAIN_SORT = """(args) => {
    const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    return [...document.querySelectorAll(args.item_sel)]
        .map((el) => norm(el.innerText)).filter(Boolean);
}"""

#: 排序条目（机票）：.sortbar-v2 li.sort-item > span。
#: 排除「更多排序」折叠项 —— 它的下拉选项由独立的 sort_more hint 收
#: （.sortbar-options .sortbar-option，实测见 reports/probes/dom_filters_flights.txt）。
_JS_FLIGHT_SORT = """(args) => {
    const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    return [...document.querySelectorAll(args.item_sel)]
        .map((el) => norm(el.innerText))
        .filter(Boolean)
        .filter((t) => !t.startsWith('更多排序'));
}"""

#: 筛选维度名（机票）：.filterbar-v2 li .filter-key
_JS_FLIGHT_KEYS = _JS_FLIGHT_SORT  # 同一提取器，item_sel 不同 —— 参数化的意义


def _hint(dimension: str, *, js: str = "", args: dict | None = None,
          selector: str = "") -> dict:
    """构造统一形态的 hint。css 形态传 selector；js 形态传 js+args。"""
    if selector and not js:
        return {"type": "css", "selector": selector, "extractor": "", "args": {},
                "dimension": dimension, "expected_shape": "list[str]"}
    if js and not selector:
        return {"type": "js", "selector": "", "extractor": js,
                "args": dict(args or {}), "dimension": dimension,
                "expected_shape": "list[str]"}
    raise ValueError(f"hint {dimension!r}: selector 与 extractor 必须二选一")


#: 每站采集点清单。容器/条目选择器全部经 tools/probes/probe_dom_filters.py
#: 实测（2026-11-02，输出 reports/probes/dom_filters_*.txt）；提取器本身的
#: 有效性由 tools/probes/probe_hint_validate.py 实跑验证。
HINTS: dict[str, list[dict]] = {
    "trains": [
        _hint("train_type", js=_JS_GROUPED, args={
            "group_title": "车型", "group_sel": ".filter-list .screen-right-bd",
            "title_sels": [".tit"], "item_sel": "ul.select-type li"}),
        _hint("seat", js=_JS_GROUPED, args={
            "group_title": "坐席", "group_sel": ".filter-list .screen-right-bd",
            "title_sels": [".tit"], "item_sel": "ul.select-type li"}),
        _hint("depart_station_opt", js=_JS_GROUPED, args={
            "group_title": "出发车站", "group_sel": ".filter-list .screen-right-bd",
            "title_sels": [".tit"], "item_sel": "ul.select-type li"}),
        _hint("arrive_station_opt", js=_JS_GROUPED, args={
            "group_title": "到达车站", "group_sel": ".filter-list .screen-right-bd",
            "title_sels": [".tit"], "item_sel": "ul.select-type li"}),
        _hint("sort", js=_JS_TRAIN_SORT, args={"item_sel": ".sort-left li .tit"}),
    ],
    "hotels": [
        _hint("price_band", js=_JS_HOTEL_GROUP, args={"group_title": "价格"}),
        _hint("star", js=_JS_HOTEL_GROUP, args={"group_title": "星级"}),
        _hint("rating", js=_JS_HOTEL_GROUP, args={"group_title": "评分"}),
        _hint("room_type", js=_JS_HOTEL_GROUP, args={"group_title": "房型"}),
        _hint("pay_type", js=_JS_HOTEL_GROUP, args={"group_title": "支付方式"}),
    ],
    "flights": [
        _hint("sort", js=_JS_FLIGHT_SORT, args={"item_sel": ".sortbar-v2 li.sort-item > span"}),
        _hint("sort_more", js=_JS_FLIGHT_KEYS, args={"item_sel": ".sortbar-options .sortbar-option"}),
        _hint("filter_keys", js=_JS_FLIGHT_KEYS, args={"item_sel": ".filterbar-v2 li .filter-key"}),
    ],
}


# ---------------------------------------------------------------- 采集

def collect(page, site: str, source_case: str) -> dict:
    """在当前页面状态上执行采集。永不抛异常。

    返回 ``{"found": [...], "not_present": [...], "collect_errors": [...],
    "elapsed_ms": ...}``，并把通过四道闸的条目写入候选池。
    调用方忽略返回值也完全安全。

    兜底边界：捕获 ``Exception``；``BaseException``（KeyboardInterrupt /
    SystemExit）按设计穿透，不视为采集失败。
    """
    result: dict = {"found": [], "not_present": [], "collect_errors": [],
                    "elapsed_ms": 0.0}
    t0 = time.perf_counter()
    try:
        _collect_inner(page, site, source_case, result)
    except Exception as exc:  # noqa: BLE001 —— 铁律 1：兜底优先于采集
        result["collect_errors"].append(f"uncaught: {type(exc).__name__}: {exc}")
    finally:
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def _extract(page, hint: dict) -> object:
    """按 hint 类型分发提取。css → query_selector_all 文本；js → evaluate。
    只读，不等待；异常上抛由调用方兜底。"""
    if hint["type"] == "css":
        nodes = page.query_selector_all(hint["selector"])
        return [n.inner_text() for n in nodes[:_MAX_NODES]]
    return page.evaluate(hint["extractor"], hint["args"])


def _collect_inner(page, site: str, source_case: str, result: dict) -> None:
    hints = HINTS.get(site, [])
    if not hints:
        result["collect_errors"].append(f"no hints registered for site {site!r}")
        return

    url = sanitize_url(page.url)
    seen_in_page: set[tuple[str, str]] = set()
    known = candidate_pool.existing_keys()  # 闸 2 的一次性快照

    for hint in hints:
        dimension = hint["dimension"]
        try:
            raw = _extract(page, hint)
        except Exception as exc:  # noqa: BLE001 —— 提取器失配只记不抛
            result["collect_errors"].append(f"{dimension}: {type(exc).__name__}: {exc}")
            continue
        # expected_shape 校验：形状不符记 collect_errors，不抛异常
        if raw is not None and not isinstance(raw, list):
            result["collect_errors"].append(
                f"{dimension}: shape mismatch（期望 {hint['expected_shape']}，"
                f"得到 {type(raw).__name__}）"
            )
            continue
        if raw is None or not raw:
            result["not_present"].append(dimension)
            continue
        values = [v for v in (normalize(str(x)) for x in raw) if v and v not in _BLACKLIST]
        if not values:
            result["not_present"].append(dimension)
            continue

        for value in values[:_MAX_NODES]:
            key = (dimension, value)
            if key in seen_in_page:
                continue
            seen_in_page.add(key)
            if (site, dimension, value) in known:  # 闸 2：去重
                continue
            if _round_count(dimension) >= PER_DIMENSION_LIMIT:  # 闸 3（进程级）
                continue
            _bump_round_count(dimension)
            result["found"].append({
                "value": value,
                "site": site,
                "dimension": dimension,
                "source_case": source_case,
                "source_selector": (
                    hint["selector"] if hint["type"] == "css"
                    else f"js:{hint['dimension']}"
                ),
                "url": url,
                "collected_at": datetime.now(timezone.utc).astimezone()
                .isoformat(timespec="seconds"),
                "round_id": _round_id(),
                "est_cost_s": None,
                "status": "candidate",
                "batch_id": "",
                "promoted_to": "",
            })

    if result["found"]:
        candidate_pool.append(result["found"])


def coverage_note(result: dict, site: str, source_case: str) -> str:
    """一次采集结果压成一行人可读文本，供 add_report_section 用。"""
    bits = [f"耗时 {result.get('elapsed_ms', '?')}ms"]
    if result["found"]:
        bits.append(
            "新候选: " + ", ".join(f"{e['dimension']}={e['value']}" for e in result["found"])
        )
    if result["not_present"]:
        bits.append("未呈现: " + ", ".join(result["not_present"]))
    if result["collect_errors"]:
        bits.append("采集异常: " + "; ".join(result["collect_errors"]))
    return f"[discover:{site}:{source_case}] " + ("; ".join(bits) if bits else "无采集点")
