"""多源下载器：并发测速 → 选最快 → 自适应超时 → 校验体积/哈希。

**为什么要自己写，而不是在命令里堆 curl 参数**（2026-11-01 的两次真实翻车）：

  1. `gh-proxy.com` 返回 **HTTP 200，但只下到 1.58 MB**（真包 30.4 MB），
     `curl` 退出码 0、`-w` 显示 200 —— 按退出码判成功的人会以为下好了，
     直到 `zipfile` 报 `BadZipFile`。**「HTTP 200 + 退出码 0」不构成下载成功的证据。**
  2. 反过来，给所有下载写死 15 秒超时同样错：30 MB 在 1 MB/s 下要 27 秒，
     15 秒截止会让**能成功的源全部判失败**。超时必须按体积和实测速度算。

因此本脚本的判据是三件事，缺一不算成功：
    体积 > 0 且 与 Content-Length 一致（容器不报长度时跳过该检查）
    + 能通过可选的 sha256 校验
    + 压缩包能打开（`--verify-zip`）

用法：
    python tools/smart_download.py <url> <输出文件> [--sha256 <值>] [--verify-zip]
    python tools/smart_download.py <url> <输出文件> --mirrors ghfast.top,gh-proxy.com

已知可用的直连（不必走镜像，见 AGENTS.md 第 10 条）：
    Allure CLI  https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/<v>/allure-commandline-<v>.zip
    JRE         https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jre/x64/windows/...
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# 已知公共镜像前缀（前缀 + 原始 https URL）。只用于 GitHub 域名，其余源不加前缀：
# 给 Maven / 清华这类本来就快的源套一层代理，只会多一跳失败点。
DEFAULT_MIRRORS = ("", "https://ghfast.top/", "https://gh-proxy.com/", "https://ghproxy.net/")

UA = {"User-Agent": "smart-download/1.0 (+project test harness)"}


def _probe(url: str, timeout: float = 6.0) -> tuple[str, float, int]:
    """测一次：返回 (url, 秒, 字节数)。失败返回秒=inf。"""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            size = int(resp.headers.get("Content-Length") or 0)
            if resp.status >= 400:
                return url, float("inf"), 0
        return url, time.perf_counter() - start, size
    except Exception:                                   # noqa: BLE001
        return url, float("inf"), 0


def pick_source(url: str, mirrors: tuple[str, ...]) -> tuple[str, int]:
    """并发测速，返回 (最快可用 url, 期望字节数)。

    并发而不是串行：串行测速本身就会把「慢源」的时间成本加到总时长里，
    而我们要的恰恰是**尽快知道谁不可用**。
    """
    candidates = [url if not m else m + url for m in mirrors] or [url]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(candidates))) as pool:
        results = list(pool.map(_probe, candidates))
    usable = [r for r in results if r[1] != float("inf")]
    for candidate, cost, size in sorted(results, key=lambda r: r[1]):
        mark = "可用" if cost != float("inf") else "不可用"
        print(f"  测速 {mark}  {cost if cost == float('inf') else round(cost, 2)}s  {size/1048576:.1f}MB  {candidate[:70]}")
    if not usable:
        raise SystemExit("所有源都测不通：先检查网络/代理，别原地重试同一个 URL。")
    best = min(usable, key=lambda r: r[1])
    return best[0], best[2]


def download(url: str, dest: Path, expected: int, sha256: str | None) -> Path:
    """下载并校验。超时按「体积 / 保守速度」估，再乘 3 倍余量。"""
    # 保守速度取 200 KB/s：本机实测最慢的可用源约 1 MB/s，取更低的兜底值，
    # 目的是「慢但能成」不被误杀，而不是精确计时。
    need = max(60, int(expected / 200_000 * 3)) if expected else 300
    part = dest.with_suffix(dest.suffix + ".part")
    resume = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={resume}-"} if resume else {})})
    print(f"下载 {url[:70]}（预计最长 {need}s，续传起点 {resume} 字节）")
    with urllib.request.urlopen(req, timeout=need) as resp, open(part, "ab") as fh:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
    part.replace(dest)

    size = dest.stat().st_size
    if expected and size != expected:
        raise SystemExit(
            f"体积不符：期望 {expected} 字节，实际 {size} 字节（{dest}）。\n"
            "  这正是公共镜像「HTTP 200 但只给了半截」的形态，不要当成成功。"
        )
    if sha256:
        got = hashlib.sha256(dest.read_bytes()).hexdigest()
        if got.lower() != sha256.lower():
            raise SystemExit(f"sha256 不符：期望 {sha256}，实际 {got}")
        print(f"sha256 校验通过：{got[:16]}…")
    else:
        print("⚠️ 未提供 sha256，仅按体积判定（正式资产请务必带哈希）")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="多源测速 + 自适应超时 + 校验的下载器")
    parser.add_argument("url")
    parser.add_argument("output")
    parser.add_argument("--sha256", default=None)
    parser.add_argument("--verify-zip", action="store_true", help="下载后尝试用 zipfile 打开")
    parser.add_argument("--mirrors", default=None, help="逗号分隔的镜像前缀；留空=只测原址")
    args = parser.parse_args()

    if args.mirrors is not None:
        mirrors = tuple(m.strip() for m in args.mirrors.split(",") if m.strip())
    elif "github.com" in args.url:
        mirrors = DEFAULT_MIRRORS
    else:
        # 非 GitHub 源不加镜像：Maven / 清华镜像本身就快，套代理只会多一个失败点
        mirrors = ("",)

    print(f"目标：{args.output}")
    best, size = pick_source(args.url, mirrors)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    download(best, dest, size, args.sha256)

    if args.verify_zip:
        try:
            with zipfile.ZipFile(dest) as z:
                bad = z.testzip()
                if bad:
                    raise SystemExit(f"压缩包内文件损坏：{bad}")
            print("zip 完整性：通过")
        except zipfile.BadZipFile:
            raise SystemExit("不是有效 zip：极可能下载被截断（见上文体积校验）")

    print(f"完成：{dest}（{dest.stat().st_size/1048576:.2f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
