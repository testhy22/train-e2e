"""探针：携程火车票「中转」入口与结果结构（只读，不改表单、不提交搜索）。

背景（为什么必须先探测）：
  用户方案里的定位器全是推演 —— `text=中转`、`input[placeholder='出发城市']`、
  `.transfer-list-item`、`text=中转方案推荐`。而本仓库 2026-11-01 的实测记录里，
  单程结果列表**本来就混着中转卡**（`上海虹桥 → 北京南转 → 亦庄 / 换乘 16 分`），
  也就是说「中转方案」可能是列表内的一种条目，未必是独立模块。

  两种形态的代码写法完全不同：
    · 独立入口 ⇒ 需要先切「中转」再搜索，断言独立模块；
    · 列表内混排 ⇒ 不需要额外入口，断言「列表里存在换乘卡」即可。
  猜错方向的后果是整条轨道建在错的前提上。

做法：三个只读快照，不点任何筛选项、不改表单、不提交搜索。
  1. 首页 `https://trains.ctrip.com/`：确认「单程/往返/中转」这一组单选的真实 DOM 形态；
  2. 中转结果页 `?ticketType=2&…`（URL 口径来自 2026-11-02 用户截图 2 的真实地址）：
     抓「中转方案推荐」标题、卡片容器、卡片条目结构；
  3. 单程结果页同参数对比：判断 `.list-item` 在中转页是否被复用（往返页已踩过一次）。

为什么有头：KNOWN-LIMITATIONS K-05 实测无头下 trains 子域被 WAF 拦（432），
  无头探测拿不到 DOM。

输出：reports/probe/transfer_<时间戳>.json（reports/ 未跟踪，不入库）。

    HEADED=1 python tools/probes/probe_transfer.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
STATE = Path("config/auth/ctrip_state_trains.json")
LIST_BASE = "https://trains.ctrip.com/webapp/train/list"
HOME = "https://trains.ctrip.com/"

TODAY = date.today()
D_DATE = TODAY + timedelta(days=4)

# 扫「中转」相关文案与 class 的通用脚本：只抓最内层可见元素，避免父容器重复命中。
SCAN_JS = r"""
(keywords) => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const chain = (el, n) => {
    const out = [];
    let cur = el;
    for (let i = 0; i < n && cur; i++, cur = cur.parentElement) {
      out.push({ tag: cur.tagName, cls: String(cur.className || "").slice(0, 90), id: cur.id || "" });
    }
    return out;
  };
  const hits = [], byClass = [];
  for (const el of document.querySelectorAll("*")) {
    const cls = String(el.className || "");
    if (/transfer|zhongzhuan|change/i.test(cls) && el.offsetHeight) {
      byClass.push({
        tag: el.tagName, cls: cls.slice(0, 90), text: norm(el.innerText).slice(0, 120),
        chain: chain(el, 3),
      });
      if (byClass.length > 40) break;
    }
  }
  for (const el of document.querySelectorAll("*")) {
    const t = norm(el.innerText);
    if (!t || t.length > 80) continue;
    if (!keywords.some((k) => t.includes(k))) continue;
    if (!el.offsetHeight) continue;
    // 只要最内层：子元素里还有同样关键字的，说明它是容器，跳过
    const childHas = Array.from(el.children).some((c) =>
      keywords.some((k) => norm(c.innerText).includes(k)));
    if (childHas) continue;
    hits.push({
      tag: el.tagName, cls: String(el.className || "").slice(0, 90), text: t.slice(0, 80),
      chain: chain(el, 4),
      clickable: !!el.closest("a,button,label,[role='tab'],[class*='tab'],[class*='radio']"),
    });
    if (hits.length > 60) break;
  }
  return { hits, byClass };
}
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result: dict = {"stamp": stamp, "today": TODAY.isoformat()}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(STATE),
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()

        # ---------- 快照 1：首页，找「中转」入口
        page.goto(HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        result["home_url"] = page.url
        result["home_title"] = page.title()
        result["home_scan"] = page.evaluate(SCAN_JS, ["中转", "换乘"])
        # 首页是否有 tab / radio 这类可切换形态
        result["home_tabs"] = page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              const out = [];
              for (const el of document.querySelectorAll(
                  "[role='tab'], [class*='tab'], [class*='radio'], label, input[type='radio']")) {
                if (!el.offsetHeight && el.tagName !== "INPUT") continue;
                const t = norm(el.innerText || el.value || el.getAttribute("aria-label"));
                if (!t || t.length > 30) continue;
                out.push({
                  tag: el.tagName, cls: String(el.className || "").slice(0, 70),
                  type: el.getAttribute("type") || "", text: t,
                });
                if (out.length > 50) break;
              }
              return out;
            }
            """
        )

        # ---------- 快照 2：中转结果页（ticketType=2，口径来自用户截图 2 的真实地址）
        url = LIST_BASE + "?" + urlencode(
            {
                "ticketType": "2",
                "dStation": "汉口",
                "aStation": "深圳",
                "dDate": D_DATE.isoformat(),
            }
        )
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        result["transfer_url"] = page.url
        result["transfer_title"] = page.title()
        result["transfer_scan"] = page.evaluate(SCAN_JS, ["中转", "换乘"])
        # 「中转方案推荐」这个标题的真实元素
        result["transfer_heading"] = page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              const out = [];
              for (const el of document.querySelectorAll("*")) {
                if (norm(el.innerText) !== "中转方案推荐" || !el.offsetHeight) continue;
                out.push({
                  tag: el.tagName, cls: String(el.className || "").slice(0, 90),
                  parent_tag: el.parentElement ? el.parentElement.tagName : "",
                  parent_cls: el.parentElement ? String(el.parentElement.className || "").slice(0, 90) : "",
                });
              }
              return out;
            }
            """
        )
        # 卡片容器：找「连续 ≥2 个都含『换乘』的子元素」的父容器，child 数最贴近的那个最准
        result["transfer_cards"] = page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              const cands = [];
              for (const el of document.querySelectorAll("*")) {
                if (!el.offsetHeight) continue;
                const kids = Array.from(el.children).filter((c) => c.offsetHeight);
                if (kids.length < 2) continue;
                const hit = kids.filter((c) => norm(c.innerText).includes("换乘"));
                if (hit.length < 2) continue;
                cands.push({
                  tag: el.tagName, cls: String(el.className || "").slice(0, 90),
                  children: kids.length, transfer_children: hit.length,
                  first_child_tag: kids[0].tagName,
                  first_child_cls: String(kids[0].className || "").slice(0, 90),
                  sample: norm(hit[0].innerText).slice(0, 260),
                });
              }
              cands.sort((a, b) => (a.children - b.children) || (b.transfer_children - a.transfer_children));
              return cands.slice(0, 6);
            }
            """
        )
        # 中转卡内部：一条卡里有哪些 class（判断是 .mid/.checi 那套复用，还是独立结构）
        result["transfer_card_inner"] = page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              let card = null;
              for (const el of document.querySelectorAll(".card-white, [class*='transfer']")) {
                if (!el.offsetHeight) continue;
                const t = norm(el.innerText);
                if (t.includes("换乘") && t.includes("第一程")) { card = el; break; }
              }
              if (!card) return null;
              const chain = [];
              let cur = card;
              for (let i = 0; i < 4 && cur; i++, cur = cur.parentElement) {
                chain.push({ tag: cur.tagName, cls: String(cur.className || "").slice(0, 90) });
              }
              const parts = [];
              for (const el of card.querySelectorAll("*")) {
                const t = norm(el.innerText);
                if (!t || t.length > 40 || !el.offsetHeight) continue;
                const childHas = Array.from(el.children).some((c) => norm(c.innerText) === t);
                if (childHas) continue;
                parts.push({ tag: el.tagName, cls: String(el.className || "").slice(0, 60), text: t });
                if (parts.length > 30) break;
              }
              return { chain, text: norm(card.innerText).slice(0, 400), parts };
            }
            """
        )

        # ---------- 快照 3：单程同参数对比（判断 .list-item 是否被中转页复用）
        url_single = LIST_BASE + "?" + urlencode(
            {
                "ticketType": "0",
                "dStation": "汉口",
                "aStation": "深圳",
                "dDate": D_DATE.isoformat(),
            }
        )
        page.goto(url_single, wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        result["single_url"] = page.url
        result["single_items"] = page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              const items = Array.from(document.querySelectorAll(".card-white.list-item"))
                .filter((el) => el.offsetHeight);
              const texts = items.map((el) => norm(el.innerText));
              return {
                total: items.length,
                with_transfer: texts.filter((t) => t.includes("换乘") && t.includes("转")).length,
                sample: texts.slice(0, 2),
              };
            }
            """
        )

        result["final_url"] = page.url
        context.close()
        browser.close()

    path = OUT / f"transfer_{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[probe] 写入 {path}")

    # ---------- 控制台摘要
    print("\n=== 首页：含「中转」的可见元素 ===")
    for h in result["home_scan"]["hits"][:8]:
        print(f"  {h['tag']}.{h['cls'][:40]!r} text={h['text']!r} clickable={h['clickable']}")
    if not result["home_scan"]["hits"]:
        print("  （无）")
    print("\n=== 首页：单程/往返/中转 那组控件 ===")
    for t in result["home_tabs"][:20]:
        if t["text"] in ("单程", "往返", "中转") or t["type"] == "radio":
            print(f"  {t['tag']}[{t['type']}] cls={t['cls'][:50]!r} text={t['text']!r}")

    print("\n=== 中转结果页：『中转方案推荐』标题 ===")
    for h in result["transfer_heading"]:
        print(f"  {h['tag']}.{h['cls'][:50]!r} 父={h['parent_tag']}.{h['parent_cls'][:40]!r}")
    if not result["transfer_heading"]:
        print("  （未找到该标题）")

    print("\n=== 中转结果页：候选卡片容器（children / 含换乘的子元素数）===")
    for c in result["transfer_cards"]:
        print(f"  {c['tag']}.{c['cls'][:50]!r} {c['children']}子/{c['transfer_children']}含换乘 "
              f"首子={c['first_child_tag']}.{c['first_child_cls'][:30]!r}")
    inner = result["transfer_card_inner"]
    if inner:
        print("\n=== 一条中转卡的文本 ===")
        print(" ", inner["text"][:240])
        print("  链：", " > ".join(f"{x['tag']}.{x['cls'][:28]}" for x in inner["chain"]))
        print("  内部片段：")
        for p in inner["parts"][:12]:
            print(f"    {p['tag']}.{p['cls'][:34]!r} {p['text']!r}")
    else:
        print("\n（没抓到含『换乘』+『第一程』的卡）")

    si = result["single_items"]
    print(f"\n=== 单程同参数对比：.card-white.list-item 共 {si['total']} 条，含换乘 {si['with_transfer']} 条 ===")


if __name__ == "__main__":
    main()
