"""把一条门禁记录追加到 `ops/metrics` 分支，然后**从远端读回来校验**。

三条硬约束（本期新增，写在代码里而不是只写文档）：

1. **每次提交必须有父提交。** 做法是每次先 fetch 当前 tip，在 worktree 里
   基于该 tip 提交，再快进 push。禁止「孤儿 commit + force push」：
   那样 git 历史里每天都是互不相关的快照，出事时无法回答「这条记录是在哪次提交之后加的」。
2. **只 append。** 不做行轮转、不改历史行。重复写入用 `ts|scope` 幂等键挡住。
3. **推完必须读回。** 用 `gh api .../contents/... --jq .content | base64 -d` 取回远端内容，
   逐行跑 schema 校验；读不回或不合法就报错退出（CI 变红）。
   只相信 push 的退出码等于相信「我说我推了」。

用法：
    python tools/ledger_append.py reports/gate-record.json
    python tools/ledger_append.py reports/gate-record.json --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import ledger

BRANCH = "ops/metrics"
LEDGER_PATH = "ops/blocked-ledger.jsonl"
LOCK_STALE_SECONDS = 600
MAX_ATTEMPTS = 3
GIT_IDENTITY = (
    "-c", "user.name=blocked-ledger-bot",
    "-c", "user.email=blocked-ledger-bot@users.noreply.github.com",
)


class AppendError(RuntimeError):
    pass


# ---------------------------------------------------------------- git 小工具

def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if check and proc.returncode != 0:
        raise AppendError(f"git {' '.join(args)} 失败（{proc.returncode}）：\n{proc.stdout}\n{proc.stderr}")
    return proc


def remote_slug(repo: Path) -> str:
    """从 origin 的 URL 里取 owner/repo，给 gh api 用。"""
    url = git(repo, "remote", "get-url", "origin").stdout.strip()
    url = url.removesuffix(".git")
    for sep in ("github.com:", "github.com/"):
        if sep in url:
            return url.split(sep, 1)[1].strip("/")
    raise AppendError(f"看不懂 origin URL：{url}")


# ---------------------------------------------------------------- 锁

class FileLock:
    """跨进程互斥。CI 侧另有 concurrency.group 兜一层，本地跑也不会互相踩。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                age = time.time() - self.path.stat().st_mtime
                if age > LOCK_STALE_SECONDS:
                    print(f"发现超过 {LOCK_STALE_SECONDS}s 的陈旧锁（{age:.0f}s），清掉后重试")
                    self.path.unlink(missing_ok=True)
                    continue
                raise AppendError(f"另一个写入者持有锁 {self.path}（{age:.0f}s），本次不写")
        raise AppendError(f"拿不到锁 {self.path}")

    def __exit__(self, *exc_info) -> None:
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------- 主流程

def gh_read_back(slug: str) -> str:
    """读回远端账本原文。用 gh api + base64 解，与文档里给人工核对的那条命令一致。"""
    proc = subprocess.run(
        ["gh", "api", f"repos/{slug}/contents/{LEDGER_PATH}?ref={BRANCH}", "--jq", ".content"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode != 0:
        raise AppendError(f"读回失败：gh api 退出 {proc.returncode}\n{proc.stderr}")
    return base64.b64decode(proc.stdout).decode("utf-8")


def verify_remote(slug: str, expected_last_key: str) -> list[dict]:
    text = gh_read_back(slug)
    records: list[dict] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AppendError(f"读回的远端账本第 {index} 行不是 JSON：{exc}") from exc
        ledger.validate_record(record, line_no=index)     # 结构化校验逐行过
        records.append(record)
    keys = [ledger.record_key(r) for r in records]
    if keys[-1:] != [expected_last_key]:
        raise AppendError(
            f"远端最后一条的幂等键是 {keys[-1:] or '(空)'}，期望 {expected_last_key}；"
            "说明这次 push 没有真正落到远端"
        )
    if len(keys) != len(set(keys)):
        duplicated = sorted({k for k in keys if keys.count(k) > 1})
        raise AppendError(f"远端存在重复记录（幂等键重复）：{duplicated}")
    return records


def append(repo: Path, record: dict, *, dry_run: bool) -> int:
    key = ledger.record_key(record)
    slug = remote_slug(repo)

    if dry_run:
        print(f"[dry-run] 待追加：{key}  verdict={record['verdict']} gate={record['gate'] or '(无)'}")
        return 0

    lock = repo / ".git" / "blocked-ledger.lock"
    with FileLock(lock):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            worktree: Path | None = Path(tempfile.mkdtemp(prefix="ledger-wt-"))
            try:
                git(repo, "fetch", "--quiet", "origin",
                    f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
                tip = git(repo, "rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.strip()
                git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), tip)

                target = worktree / LEDGER_PATH
                existing = ledger.load_ledger(target)      # 坏行就在这里炸，不往上叠
                if key in ledger.iter_keys(existing):
                    print(f"幂等：{key} 已存在，不重复追加")
                    return 0

                with open(target, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(ledger.dumps_record(record) + "\n")

                git(worktree, "add", LEDGER_PATH)
                message = (
                    f"ops(ledger): {record['ts']} {record['scope']} {record['verdict']}\n\n"
                    f"blocked={record['blocked']}/{record['counted']} ratio={record['ratio']:.2%}\n"
                    f"gate={record['gate'] or '(无)'} run={record['run']}"
                )
                git(worktree, *GIT_IDENTITY, "commit", "--quiet", "-m", message)

                parent = git(worktree, "rev-parse", "HEAD^").stdout.strip()
                if parent != tip:
                    raise AppendError(f"提交的父提交 {parent[:8]} 不是 fetch 到的 tip {tip[:8]}，拒绝推送")

                push = git(worktree, "push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", check=False)
                if push.returncode != 0:
                    print(f"第 {attempt}/{MAX_ATTEMPTS} 次推送被拒（tip 可能刚被别人推进），重来\n{push.stderr.strip()}")
                    continue

                git(worktree, "worktree", "remove", "--force", str(worktree), check=False)
                worktree = None            # 已清理，避免 finally 再删一次
                records = verify_remote(slug, key)
                print(f"已追加并读回校验通过：远端 {len(records)} 行，最后一条 {key}")
                return 0
            finally:
                if worktree is not None and worktree.exists():
                    git(repo, "worktree", "remove", "--force", str(worktree), check=False)
                    shutil.rmtree(worktree, ignore_errors=True)
        raise AppendError(f"连续 {MAX_ATTEMPTS} 次推送失败，本次不写（不做 force push）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把门禁记录追加到 ops/metrics 分支")
    parser.add_argument("record", type=Path, help="blocked_gate.py --record-out 产出的 JSON")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        ledger.validate_record(record)
        return append(args.repo, record, dry_run=args.dry_run)
    except (ledger.SchemaError, AppendError, OSError) as exc:
        print(f"追加账本失败：{exc}")
        if os.getenv("GITHUB_ACTIONS") == "true":
            print(f"::error::ledger 追加失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
