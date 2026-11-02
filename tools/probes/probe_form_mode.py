"""探针：首页表单「单程 / 往返 / 中转」三态的 DOM 差异（只读，不提交搜索）。

起因（2026-11-02 用户裁决）：
  往返与中转两条轨道此前都是**改 URL 直达结果页**，用户要求改成
  「点按钮 + 填输入框 + 点搜索」的真实交互，只有回到一级菜单才允许用 URL。
  ⇒ 必须先知道：三态切换项长什么样、切换后表单多了/少了哪些控件。

本探针只做三件事：
  1. 打印三态切换项的候选（tag/class/父级链），并给每个候选项打 data-probe 标记
     —— 后续用真实鼠标点，不用 JS .click()，避免"能点但事件没绑上"的假通过；
  2. 每次点击后重新快照：全页 <input> 清单（id/type/placeholder/值）、日期块 outerHTML；
  3. 最后点回「单程」，确认表单复原（证明三态是互斥切换，不是叠加）。

    HEADED=1 python tools/probes/probe_form_mode.py

产物：reports/probe/form_mode_<时间戳>.json + stdout 摘要。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
STATE = Path("config/auth/ctrip_state_trains.json")
HOME = "https://trains.ctrip.com/"

# 快照：三态候选 + input 清单 + 日期区结构
SNAPSHOT_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const desc = (el) => ({
    tag: el.tagName,
    cls: String(el.className || "").slice(0, 90),
    id: el.id || "",
    kids: el.children.length,
    text: norm(el.innerText).slice(0, 60),
  });
  const chain = (el, n) => {
    const out = [];
    let cur = el;
    for (let i = 0; i < n && cur; i++, cur = cur.parentElement) out.push(desc(cur));
    return out;
  };

  // 1) 三态候选：文本恰好等于「单程/往返/中转」的可见元素，取最内层的那一批
  const modes = {};
  for (const want of ["单程", "往返", "中转"]) {
    const all = Array.from(document.querySelectorAll("div,span,label,a,button,li,p"))
      .filter((el) => el.offsetHeight && norm(el.innerText) === want);
    // 越内层越可能是真正的可点元素：按后代数升序取前 3 个
    all.sort((a, b) => a.querySelectorAll("*").length - b.querySelectorAll("*").length);
    const picked = all.slice(0, 3);
    picked.forEach((el, i) => el.setAttribute("data-probe", `${want}#${i}`));
    modes[want] = {
      total: all.length,
      items: picked.map((el) => ({
        ...desc(el),
        probe_attr: el.getAttribute("data-probe"),
        chain: chain(el, 4),
        // 选中态可能落在 class 或 aria 上，两处都记
        aria_checked: el.getAttribute("aria-checked"),
        input_checked: (() => {
          const r = el.querySelector("input[type=radio],input[type=checkbox]");
          return r ? r.checked : null;
        })(),
      })),
    };
  }

  // 2) 全页 input 清单：id 是定位器的第一手依据
  const inputs = Array.from(document.querySelectorAll("input")).map((el) => ({
    id: el.id || "",
    type: el.type || "",
    name: el.name || "",
    cls: String(el.className || "").slice(0, 50),
    placeholder: el.placeholder || "",
    value: (el.value || "").slice(0, 30),
    visible: !!el.offsetHeight,
    readonly: !!el.readOnly,
    chain: chain(el.parentElement, 3).map((x) => `${x.tag}.${x.cls}`.slice(0, 70)),
  }));

  // 3) 往返/中转态里"多出来的东西"：找文本含「返回」「中转城市」的块
  const hints = [];
  for (const kw of ["返回", "中转城市", "指定"]) {
    const hit = Array.from(document.querySelectorAll("div,span,label"))
      .filter((el) => el.offsetHeight && norm(el.innerText).includes(kw) && norm(el.innerText).length < 40)
      .slice(0, 4);
    hints.push({ kw, found: hit.length, samples: hit.map((el) => ({ ...desc(el), chain: chain(el, 3) })) });
  }

  return { title: document.title, url: location.href, modes, inputs, hints };
}
"""


def snap(page) -> dict:
    return page.evaluate(SNAPSHOT_JS)


def brief(label: str, data: dict, prev_ids: set[str] | None = None) -> None:
    print(f"\n{'=' * 78}\n[{label}] url={data['url']}\n{'=' * 78}")
    for want, rec in data["modes"].items():
        print(f"  「{want}」候选 {rec['total']} 个：")
        for item in rec["items"]:
            parent = item["chain"][1] if len(item["chain"]) > 1 else {}
            print(
                f"    {item['probe_attr']:<8} <{item['tag']}.{item['cls'][:38]}> "
                f"id={item['id'][:18]!r} kids={item['kids']} "
                f"aria={item['aria_checked']} radio={item['input_checked']}"
            )
            print(f"             父: <{parent.get('tag')}.{str(parent.get('cls'))[:38]}> id={parent.get('id')!r}")
    ids = {i["id"] for i in data["inputs"] if i["id"]}
    print(f"  带 id 的 input 共 {len(ids)} 个：{sorted(ids)}")
    if prev_ids is not None:
        added, removed = ids - prev_ids, prev_ids - ids
        print(f"  ※ 与上一态相比：新增 {sorted(added) or '(无)'}｜消失 {sorted(removed) or '(无)'}")
    for hint in data["hints"]:
        if hint["found"]:
            print(f"  关键词「{hint['kw']}」命中 {hint['found']} 处：")
            for s in hint["samples"]:
                print(f"    <{s['tag']}.{s['cls'][:40]}> {s['text'][:40]!r}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    records: dict[str, dict] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(STATE),
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.goto(HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        base = snap(page)
        records["initial"] = base
        brief("初始态", base)
        base_ids = {i["id"] for i in base["inputs"] if i["id"]}

        # 依次点「往返」「中转」，每次点完重新快照
        prev = base_ids
        for mode in ("往返", "中转"):
            target = page.locator(f'[data-probe="{mode}#0"]').first
            if not target.count():
                print(f"\n!! 「{mode}」没有可点的候选，跳过")
                continue
            target.click()
            page.wait_for_timeout(2500)
            data = snap(page)
            records[mode] = data
            brief(f"点「{mode}」之后", data, prev)
            prev = {i["id"] for i in data["inputs"] if i["id"]}

        # 点回「单程」，确认表单复原
        back = page.locator('[data-probe="单程#0"]').first
        if back.count():
            back.click()
            page.wait_for_timeout(2000)
            data = snap(page)
            records["back_to_oneway"] = data
            brief("点回「单程」之后", data, prev)

        path = OUT / f"form_mode_{stamp}.json"
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
        browser.close()

    print(f"\n[probe] 写入 {path}")


if __name__ == "__main__":
    main()
