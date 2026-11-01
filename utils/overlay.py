"""关闭站点自己的营销浮层（目前只有 www.ctrip.com 底部领券条）。

为什么必须显式关，以及为什么主因不是「挡点击」而是「假绿」：

  2026-11-01 用户截图证据（clipboard-2026-11-01T08-42-48）：首页底部固定浮层
  「携程旅行 / 周周享低价 / 领券更划算」带一张**大二维码**。它造成两个后果：

    1. 盖住页面底部，把「站点有浮层」变成「我们点不到」——失败原因被污染；
    2. 任何「二维码可见」类断言（登录用例最典型）都会命中这张**营销码**，
       用例变绿，但一个登录相关的断言都没验证到。**这条才是主要理由。**

  第 2 条在本轮之前只是推测，现在有画面证据了。

边界（与本工程既有做法一致，不是反检测）：
  只替用户点掉浮层自带的「×」，站点看到的结果与用户手动关闭完全一致；
  不改 UA、不注入脚本、不拦请求、不改任何断言。

失败语义：
  找不到浮层就静默返回空列表。浮层是站点随时可加可撤的东西，**不该由它决定用例红绿**；
  真挡住操作时，失败的仍然是被挡的那一步（有截图），不是这里。
"""

from __future__ import annotations

from playwright.sync_api import Page

# 只认这两个锚点文本，刻意不写「领券」单字：
# 截图里页面中部另有一条「携程商旅助力企业 / 免费注册领券包」横幅，
# 单字锚点会把它一起关掉——那是另一个元素，关掉会改变页面结构，
# 后续断言若依赖它就变成我们自己改出来的失败。
PROMO_ANCHORS: tuple[str, ...] = ("周周享低价", "领券更划算")

# 关闭按钮候选。**全部 ⚠️[未查实]**：截图里只看得到「×」图形，看不到 DOM。
# 顺序按稳定性排：语义标签 > 通用类名。点不到就交给下一个候选，
# 全部失败只记录不抛——下次跑会从 reports 的日志里看到未命中，再补候选。
CLOSE_CANDIDATES: tuple[str, ...] = (
    '[aria-label="关闭"]',
    '[title="关闭"]',
    '[class*="close"]',
    '[class*="Close"]',
)

# 从锚点往上找几层祖先。浮层的「×」通常不在锚点同一层，
# 而在共同祖先的另一个子树里，实测经验值 6 层足够覆盖常见结构。
MAX_ANCESTOR_HOPS = 6


def _anchor_visible(page: Page, anchor: str) -> bool:
    """锚点文本当前是否可见。关闭前后都靠它判定，避免把「点了没关掉」记成成功。"""
    try:
        locator = page.get_by_text(anchor, exact=False)
        return any(
            locator.nth(i).is_visible() for i in range(min(locator.count(), 3))
        )
    except Exception:
        return False


def dismiss_promo_overlays(page: Page, *, tag: str = "") -> list[str]:
    """关掉当前页面上命中的营销浮层，返回人类可读的处理记录。

    记录会由调用方写进报告 section —— 关了几个、用哪个选择器关的，
    都必须可追溯，否则「浮层到底有没有被关」只能靠猜。
    """
    done: list[str] = []

    for anchor in PROMO_ANCHORS:
        locator = page.get_by_text(anchor, exact=False)
        try:
            if locator.count() == 0:
                continue
            # 只处理可见的那一个：不可见的同名节点可能在模板里预埋着
            node = None
            for index in range(min(locator.count(), 3)):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    node = candidate
                    break
            if node is None:
                continue
        except Exception:
            continue

        clicked = ""
        cursor = node
        for _ in range(MAX_ANCESTOR_HOPS):
            try:
                cursor = cursor.locator("xpath=..")
                if cursor.count() == 0:
                    break
            except Exception:
                break

            for selector in CLOSE_CANDIDATES:
                try:
                    button = cursor.locator(selector).first
                    if button.count() == 0 or not button.is_visible():
                        continue
                    button.click(timeout=2000)
                    clicked = selector
                    break
                except Exception:
                    continue
            if clicked:
                break

        if not clicked:
            done.append(f"{anchor}: 找到锚点但没找到可见的关闭按钮（候选未命中）")
            continue

        # 关完要复核，不然「点了但没关掉」会被记成成功
        still_there = _anchor_visible(page, anchor)
        done.append(
            f"{anchor}: 已点击 {clicked}；复核={'仍可见' if still_there else '已消失'}"
        )

    if tag and done:
        done.insert(0, f"[{tag}]")
    return done
