# TODO / 挂起清单

> 归档时间：2026-11-01 19:27（`3e9064b` 推送后）
> 状态约定：**全部冻结挂起** —— 本轮不修、不动代码、不开分支。
> 谁要解冻，先看每条的「解冻条件」；没满足就别开工，否则会重做一遍已经判过的事。
> 编号是稳定标识，禁止重编号、禁止合并成「其他优化」这种口袋条目。

---

## 1. K-06 的两组对照实验 —— 冻结，等下个迭代

| 项 | 内容 |
|---|---|
| 条目 | 有头跑酒店曾停在 `passport.ctrip.com` 登录墙并 180s 超时，同一提交随后两次跑绿（95.2s / 106s）⇒ **1 红 / 2 绿，非稳定复现** |
| 详情 | `docs/KNOWN-LIMITATIONS.md` → K-06（含取证时间、截图、junit 缺失的说明） |
| 缺的两组对照 | ① `pytest ... --durations=10` 定位慢在哪一步；② 去掉 `browser_context_args` 里的 `permissions: []` 再跑一次（排除 geolocation 预拒绝相关） |
| 为什么必须做对照 | K-06 的根因至今 `⚠️[未查实]`。「停在登录墙」只是窗口当时的**可见状态**，不是因果证据 —— 反证是 K-04：匿名态酒店列表本来就会被 passport 接管，而 headless 三次同样落登录墙仍全绿 |
| 解冻条件 | ① K-06 **再次复现**（红）；或 ② 打算把「登录墙重定向」写进任何结论/文档之前。**没拿到两组对照前不允许写「已修复」**，也不允许把它当「对方页面行为」归档 |
| 命令入口 | 见 PR #1 §6；跑法 `scripts/run-headed.sh suites/hotels -m hotels`（`HEADED=1 HUMAN=1`） |
| 验收标准 | 两组对照各有一次完整运行记录（含 `--durations` 输出/junit），能指认具体慢在哪一步；或连续 20 次跑绿，改记为「已观察、暂不可复现」（记录保留，K-06 不删除） |

## 2. CI workflow 的启用 —— 冻结，等 CI 专项

| 项 | 内容 |
|---|---|
| 现状 | `.github/workflows/ci.yml` 里 **hotels job 仍是注释态**（第 151 行 `#   hotels:`）；workflow 本体处于 `disabled_manually` |
| 影响面 | 酒店轨道目前**没有 CI 门禁**，只有本地 `scripts/run-headed.sh` 跑。也就是说「酒店绿」是本地绿，不是门禁绿 |
| 为什么冻结而不是现在开 | 开之前要先解决「无头态 WAF/登录墙导致的不稳定」与 `auth` 轨道的 skip 语义（K-05 / K-07），否则开起来就是长期黄灯，黄灯会被忽略 —— 被忽略的闸门等于没有 |
| 解冻条件 | 进入 CI 专项优化时统一定：hotels job 的门禁地位（真门禁 / 只做趋势）、超时预算、是否依赖 `config/auth/`（K-07 结论：CI 不持有凭据） |
| 验收标准 | workflow 从 `disabled_manually` 恢复为 active 且首次 push 有完整 run 记录；hotels job 取消注释后连续 3 次 run 结论一致（不是红绿摇摆） |
| 关联 | `docs/PLAN-M0-M1.md` 的 job 划分（`regression` / `thirdparty-*` / `quality`）是当初的设计稿，落地程度未逐条核对 |

## 3. Docker 阻塞 —— 冻结，等容器化阶段

| 项 | 内容 |
|---|---|
| 现状实测 | `docker info` 报 `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine … daemon is running` —— **daemon 未运行**（2026-11-01 19:26 复核）。Docker 29.9.2 / Compose v5.6.1 已装但未使用（`docs/FRAMEWORK-AUDIT.md` A3） |
| 被它挡住的事 | 容器化靶站（`regression` 轨道的被测对象）无法启动 ⇒ 需要靶站的用例只能在 mock/静态数据层验证，端到端开不了 |
| 影响面 | 只影响「轨道 A：回归套件」的容器化路线；轨道 B（第三方监控，`trains.ctrip.com`）不依赖容器，已能跑 |
| 解冻条件 | ① 启动 Docker Desktop 使 daemon 可用；② 进入 `docs/FRAMEWORK-AUDIT.md` 的 M5（Dockerfile + compose）之前 |
| 验收标准 | compose 冷启动一次跑通；`down -v` 后无残留容器/网络/卷（独立 project name） |
| 备注 | 这是**环境阻塞**，不是代码缺陷。别把它写成「容器化已完成/已放弃」 |

## 4. 火车票翻月技术债 —— 按原裁决留在 K-09，本条只做索引

| 项 | 内容 |
|---|---|
| 债务内容 | `TrainSearchPage._goto_month` 只支持**向后**翻（`›`，最多 3 次），且月份标签**不带年份**；跨年、往回翻都没实现 |
| 详情 | `docs/KNOWN-LIMITATIONS.md` → K-09「未覆盖」行，原文记录在此，不重复描述 |
| 为什么现在不修 | 本期火车票数据全部落在当月，未触发；探针也未取证（没有真实跨年场景的页面快照，改了等于照猜写代码） |
| 解冻条件 | 出现跨月/跨年的火车票测试数据，或用例执行顺序变化导致目标月不在当前可见范围（K-09 已证明「先远后近」就会翻车） |
| 验收标准 | 与机票 `select_date()` 同等的强约束：翻月后**回读 input 必须等于目标日期**，不等则抛错并说明原因；月份定位带年份限定。改完补一条跨年数据进 `data/` |
| 裁决 | 用户 2026-11-01 明确：**作为已知技术债记录在 K-09，后续版本再迭代**，本轮不动 |

---

## 5. 清理器删旧产物 4 个/秒，全量 pytest 在 sessionstart 静默卡 6~10 分钟 —— 冻结，等基础设施专项

| 项 | 内容 |
|---|---|
| 债务内容 | `utils/cleaner.py` 的 `for path in target.rglob(): path.unlink()` 在本机实测 **249 ms/文件（≈4 个/秒）**；`reports/` 里 24h 口径的过期文件 2383 个 ⇒ `[推算]` 约 594s。期间日志只有开头一行，看起来像挂死 |
| 详情 | `docs/KNOWN-LIMITATIONS.md` → K-12（含 200 / 2000 两档基准数据、已排除项、三种建议修法） |
| 取证脚本 | `tools/probes/probe_cleaner_bench.py`（**在项目内自建临时目录，全程不碰 `reports/`**；跑法 `BENCH_N=200 python tools/probes/probe_cleaner_bench.py`） |
| 为什么现在不修 | 与往返轨道无因果关系；混进业务 PR 会让人分不清是「新用例红了」还是「清理器把会话拖死了」 |
| 临时规避（已验证） | `E2E_CLEAN_HOURS=72`（6845 个文件 3s 返回，门禁正常跑完）。`E2E_CLEAN_DRY_RUN=1` 亦可；**不要用 `E2E_CLEAN_DISABLE=1`**，它会让被测的 4 条 `test_cleaner` 全红 |
| 解冻条件 | ① 本地全量门禁再次被它拖到无法判断结果；或 ② 要把清理逻辑挪出 `pytest_sessionstart` 之前 |
| 验收标准 | 补一条**进度日志**（每 N 个文件一行）先解决「分不清卡死还是在跑」；改动后 `test_cleaner` 4 条 + 基准脚本复测，速率与 `skipped` 计数都要有数字，不接受「感觉快了」 |
| 根因状态 | ⚠️[未查实]：249 ms/文件 的来源未定位（杀软实时扫描 / G: 盘文件系统 / 元数据同步三选一） |

---

## 附：本轮已完成、不要重开的判断

这几条是上一轮明确裁决过「不按模板做」的，若有人重新提，直接引用：

- **登录态采集**保持 `new_context` + `storage_state`，不用 `launch_persistent_context` + 改 `Preferences` —— 目的是保持 state 文件纯净可审计。
- **不加任何指纹伪装参数**（`--disable-blink-features=AutomationControlled` 等）—— K-05 已证拦截与指纹无关，且项目硬约定不伪装身份。
- **Trace 只在失败时录制**（`E2E_TRACE=1` 开关）—— 默认全开会产生几十 MB/次的体积。
- **清理器保持极度保守**（24h、`PermissionError` 跳过、项目外拒绝）—— 防误删优先于清得干净。
