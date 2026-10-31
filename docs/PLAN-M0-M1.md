# M0 / M1 执行计划（待确认，未动代码）

> 状态：**设计已定，等确认后执行**。本文件不含任何代码改动。
> 编写时间：2026-10-31 23:40 (CST)
> 依据实测：`docker` daemon 未运行；项目非 git 仓库；31 条用例 = 27 数据层 + 1 smoke + 3 查询；
> 六个新依赖 dry-run 全部可解析。

---

## 0. 一条必须先说的事实：当前的「绿」是假绿

```
31 collected
  tests/test_data_loader.py   27   ← 纯单元，不碰浏览器
  tests/test_smoke.py          1   ← 打开页面 → blocked → xfail
  tests/test_train_search.py   3   ← 真实查询 → blocked → xfail
上次运行：27 passed, 4 xfailed  ⇒ 退出码 0
```

**有效 UI 验证数 = 0。** 27 条 passed 全部是 YAML 解析与偏移量校验，和「携程页面能不能查」无关。
这不是 bug，是设计缺陷：`blocked → xfail` 让 100% 拦截也能得到绿色退出码。

§2 的 blocked 门禁就是为这条事实设计的：**它会把今天这种状态直接判红**。

---

## 1. 双轨目录与 CI job 划分

### 1.1 两条轨道的定义

| | 轨道 A：回归套件 | 轨道 B：第三方监控套件 |
|---|---|---|
| 目标 | 验证自己的被测系统 | 观察站点可达性与查询可用性 |
| 被测对象 | 本地容器化靶站（自建，端口固定） | `https://trains.ctrip.com` |
| 门禁地位 | **真门禁**：红即拦 | **不进回归门禁**：只做趋势与可用性判断 |
| 触发 | PR / push / nightly | 每日 1 次 schedule + 手动 dispatch |
| flake 验收 | 连续 3 次 + `-n 2` + 乱序，全绿 | 不做 3 次验收，改「连续 N 天 blocked 率」 |
| 失败语义 | blocked 不存在（隔离环境） | blocked 一等，走 xfail + 门禁判定 |
| 数据 | 数据工厂自建自清，每测试唯一 | 只读，`search_cases.yaml` 静态 |
| 证据 | 截图 + trace + 容器日志 | 截图 + HTML + URL + trace(仅真失败) |

### 1.2 目标目录树（M1 结束时的形态）

```
train-e2e/                          # git 仓库根（M0-1）
├── .github/workflows/
│   ├── regression.yml              # 轨道 A：PR/push/nightly
│   ├── thirdparty-monitor.yml      # 轨道 B：schedule + dispatch
│   └── quality.yml                 # ruff + mypy（仅改动文件）
├── docker/
│   ├── compose.yaml                # profiles: ci / local
│   └── target-app/
│       ├── Dockerfile
│       ├── app.py                  # FastAPI：/healthz /api/queries /search
│       ├── seed.py                 # 幂等播种（固定基线数据）
│       └── templates/…
├── suites/
│   ├── regression/                 # 轨道 A
│   │   ├── conftest.py
│   │   ├── pytest.ini
│   │   ├── tests/                  # test_search_flow.py / test_isolation_probe.py
│   │   ├── pages/                  # base_page.py / search_page.py
│   │   ├── components/             # city_picker.py / date_picker.py / result_list.py
│   │   ├── data_factories/         # query_factory.py（唯一 id + workerId）
│   │   ├── api_clients/            # target_api.py（预置/清理走 API）
│   │   └── fixtures/               # env.py（显式 fixture）
│   └── thirdparty/                 # 轨道 B（= 现有 tests/ 迁移而来）
│       ├── conftest.py
│       ├── pytest.ini
│       ├── tests/                  # test_smoke.py / test_train_search.py / test_calendar_widget.py
│       ├── pages/                  # base_page.py / train_search_page.py
│       └── test_data/              # search_cases.yaml
├── shared/                         # 两套共用（M2 再迁，M1 暂留原位）
│   # config/settings.py、utils/data_loader.py → 迁到这里
├── tools/
│   ├── blocked_gate.py             # ★ 新增：blocked 门禁判定
│   ├── ci_blocked_report.py        # 现有：只输出 annotation
│   └── probes/                     # 现有 10 个探针脚本
├── ops/
│   └── blocked-ledger.jsonl        # ★ 新增：跨 run 累计（独立分支 ops/metrics）
├── docs/                           # 本文件 / FRAMEWORK-AUDIT / KNOWN-LIMITATIONS / locator-evidence
├── reports/                        # 忽略（截图 / trace / html）
├── conftest.py                     # 仓库级通用 hook
├── pytest.ini                      # 只放全局公共项
├── requirements.txt / requirements-dev.txt
└── README.md
```

**M1 不迁 `config/` 和 `utils/`**：它们是两套共用模块，迁到 `shared/` 会牵动全部 import，属于 M2 范围。
M1 只做「轨道 B 物理搬家 + 轨道 A 从零新建」，共用一个 `conftest.py`。

### 1.3 CI job 划分

| Job | 文件 | 触发 | 超时 | 做什么 | 门禁 |
|---|---|---|---|---|---|
| `regression` | regression.yml | `pull_request`、`push: main`、`schedule 0 19 * * *`(03:00 CST) | 25 min | compose up --wait → seed → `pytest suites/regression -n 2 -p randomly` → down -v | **红即拦 PR** |
| `regression-flake` | regression.yml（同文件，`workflow_dispatch` 专用 job） | 手动 | 45 min | 连跑 3 次 `-n 2 -p randomly`，任一次红即红 | 红即拦 |
| `thirdparty-smoke` | thirdparty-monitor.yml | `schedule 30 0 * * *`(08:30 CST)、`workflow_dispatch` | 12 min | `pytest suites/thirdparty -m smoke` + blocked_gate | blocked_gate 退出码 0/1 |
| `thirdparty-full` | thirdparty-monitor.yml | 仅 `workflow_dispatch`（带 `full=true`） | 15 min | 3 条查询用例 | 同上 |
| `quality` | quality.yml | PR / push | 6 min | ruff check + mypy，仅对 `git diff --name-only` 的文件 | 红即拦 |

三条硬约束：

1. **轨道 B 任何 job 都不在 `pull_request` 上触发**。真站点测试挂 PR 会被限流，且会把无关 PR 拖红。
2. **轨道 A 不依赖任何外部网络**（除 Docker Hub 拉基础镜像，CI 里用 `docker/build-push-action` cache 或预构建镜像规避）。
3. **两条轨道共享 code，不共享 job**：`conftest.py` 里的截图 hook 由两套 suite 各自的 `conftest.py` 继承，不用 `if 'thirdparty' in str(item.path)` 这类分支。

### 1.4 触发时间对照

| 时间 (CST) | 事件 |
|---|---|
| 03:00 | 轨道 A nightly（against 容器靶站） |
| 08:30 | 轨道 B 每日一次 smoke（避开整点高峰，降低被 WAF 关注概率） |
| 随时 | PR → 轨道 A + quality |

---

### 1.5 xfail / skip 三分语义（你追加要求 2 的落地）

现状：`tests/test_train_search.py:34` 用 `with blocked_guard(), widget_guard():` 把**风控拦截**和**日历唤不起**
放在同一个 try 里，两者都走 xfail。结果是"框架自己的控件问题"被记成了"对方拦我"，
既掩盖了 K-01 这个已知限制，也让 blocked 统计口径不纯。

| 情形 | 当前行为 | M1 改为 | 统计口径 |
|---|---|---|---|
| 风控拦截 | `xfail("blocked by anti-bot: …")` | `xfail("blocked: <signal>")` + png + `page.content()` + `url.txt` | 计入 `blocked` |
| 控件唤不起（日历 K-01） | 也走 xfail ⇒ **无限期 xfail** | ✅ 已改为 `skip("known-limit: K-01 …")`，条目在 `docs/KNOWN-LIMITATIONS.md` | 计入 `skipped`，**不进分母** |
| 真实断言失败 | fail | fail（不变） | 计入 `failed` |

配套改动（M1-2 内完成）：

- 主用例不再调用 `select_date()`，改走 URL 预置：`/webapp/train/list?...&dDate=YYYY-MM-DD`。
- 日历单独留一条 `test_calendar_widget.py`，标记 skip，只做冒烟，不参与回归判定。
- ⚠️[未查实] 直连 URL 带 `dDate` 是否生效：现有证据是「UI 操作后 URL 出现 dDate 且结果页抬头日期一致」，
  **不是**「手工构造 URL 直达」。动代码前先用 1 次只读探针确认这一条，确认不了就不改这条路径。

---

## 2. blocked 门禁：阈值设计

### 2.1 为什么现有的 annotation 不够

`tools/ci_blocked_report.py` 只会输出 `::warning::`，**退出码恒为 0**。这意味着：
拦截率从 0% 涨到 100%，CI 全程绿色。这是「长期全绿但什么都没验证」的标准形态。

### 2.2–2.4 指标 / 判定表 / 退出码 → 见 `docs/BLOCKED-GATE.md`

**指标定义、判定表（G0–G6）、退出码不在本文件维护。** 唯一规范来源是
[`BLOCKED-GATE.md`](BLOCKED-GATE.md)，此处只保留三条决策背景：

1. **为什么需要独立门禁**：旧的 `tools/ci_blocked_report.py` 只输出 `::warning::`、
   退出码恒为 0 —— 拦截率从 0% 涨到 100%，CI 全程绿色。该脚本已删除，
   由 `tools/blocked_gate.py` 取代。
2. **口径只吃 `-m ui`**：2026-10-31 那次「31 条 vs 4 条」的统计错误，已固化为可执行测试
   `tools/tests/test_blocked_gate.py::test_全量31条junit必须被拒收`（混入全量 junit 必须 exit 2）。
   本文件不再复述那张对照表，避免两处分叉。
3. **阈值全部 provisional**：0.3 / 0.5 / 3 次 / 7 天是拍的，ledger 攒够 14 天再校准，
   校准依据写回规范 §6；校准前门禁输出自带 `[provisional]` 标记。

### 2.5 ledger 持久化（关键：跨 run 才能判 G3/G4）

每行一条 JSON，追加到 `ops/blocked-ledger.jsonl`：

```json
{"ts":"2026-10-31T23:40:00+08:00","run_id":"gh-1234567","total":4,"blocked":4,
 "effective_pass":0,"failed":0,"ratio":1.0,"verdict":"RED","gate":"G1",
 "top_signal":"waf_block(whaleguard)"}
```

存储位置三个方案，我推荐 A：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）** | CI 提交到独立分支 `ops/metrics`，主分支只放结果摘要 | 可审计、跨 run 累加、不污染主分支历史 | 需要 `contents: write` 权限 |
| B | Actions cache | 无需权限 | **会丢**（7 天不用即清），G3/G4 会突然失忆 |
| C | 只存 artifact | 零风险 | 无法跨 run 计算，G3/G4 直接不可实现 |

方案 A 的噪音控制：只在 `thirdparty-*` job 里提交，且同一个 run 只提交 1 次（`git commit --allow-empty` 才跳过）。

### 2.6 告警渠道

默认 **GitHub Issue**：标题 `[thirdparty] blocked 率异常 YYYY-MM-DD`，命中 G3/G4 时自动创建或更新同一 issue（用固定 label `blocked-alert` 去重）。
理由：无需新凭据、CI 内 `gh` CLI 即可、历史可查。
企微 webhook 作为可选项，需要你给 URL 才启用（§4 问题 2）。

---

## 3. M0 / M1 任务清单（每步一次提交）

### M0 · git 基线（4 步，约 20 分钟）

| 步骤 | 动作 | 验证方式（命令 + 期望） |
|---|---|---|
| M0-1 | `git init -b main`，仓库根 = 项目目录 | `git rev-parse --show-toplevel` → 项目绝对路径 |
| M0-2 | 补 `.gitignore`：`.env`、`reports/*`（保留 `.gitkeep`）、`*.zip`、`install.log`、`.venv/`、`__pycache__/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/` | `git status --porcelain \| grep -c reports` → **0** |
| M0-3 | 敏感信息扫描：绝对路径 `G:/`、邮箱、`sk_`/`AKIA`、手机号 | `git grep -nE "G:/|sk_live|AKIA|[a-z0-9._%+-]+@[a-z0-9.-]+" -- . ':!docs'` → 空（或仅剩可解释项） |
| M0-4 | 首次提交（含 docs/locator-evidence 的 WAF 截图作为证据） | `git log --oneline` → 1 条；`git ls-files \| wc -l` 记录基线数 |

**仓库根的决策**：不放在 `<workspace>` 根（那里有 16 个临时目录、大量产物与非本项目文件），
仓库根 = `<project-root>/`。理由：`git init` 到工作区根会把 15 个无关项目纳入版本控制，
且 `reports/` 这类产物会在每次 `git status` 里制造噪音。

**M0 的通过标准：`git status` 干净且不含任何 reports 内容。不满足不进 M1。**

### M1 · 试点（6 步，每步一个 commit + 一次验证）

| 步骤 | 动作 | 对应原则 | 验证方式（命令 + 期望） |
|---|---|---|---|
| **M1-1** 依赖 ✅ | 6 个已装并锁版本：`xdist 3.8.0` `randomly 5.0.0` `ruff 0.17.3` `mypy 2.4.1` `Faker 40.44.0` `timeout 2.4.0`。拆分时 `pytest-timeout` 放进 **runtime** 依赖——`pytest.ini` 里有 `timeout=180`，缺插件时该项被静默忽略只发 warning，等于「以为挂了超时其实没挂」 | 环境可复现 | `pytest suites/thirdparty --collect-only` → **32 collected**（原 31 + 新增 K-01 用例） |
| **M1-2** 轨道拆分 ✅ | `git mv tests suites/thirdparty/tests`；`suites/regression/` 空骨架；三条浏览器用例加 `pytest.mark.ui`；`scope` 属性由 conftest 的 `pytest_collection_modifyitems` 统一盖；reason 前缀标准化（`blocked:` / `known-limit:`）；`tools/blocked_gate.py` + `tools/ledger.py` + `tools/ledger_append.py` 落地 | 分层 / 口径干净 | `pytest suites/thirdparty -m ui` → **4 xfailed + 1 skipped（27 deselected）**，与搬家前逐条一致（多出的 1 条是 K-01） |
| **M1-3** 靶站容器化 | `docker/target-app`（FastAPI + SQLite）：`/healthz`、`POST /api/queries`（预置）、`DELETE /api/queries/{id}`（清理）、`/search` 页含城市联想下拉 + 日期参数 + 结果列表；`compose.yaml` 双 profile | 环境隔离、数据播种 | `docker compose --profile ci up -d --wait` → `curl -sf localhost:8801/healthz` 200；**重复执行 seed 两次，行数不变**（幂等）；`down -v` 后 `docker compose ps` 为空 |
| **M1-4** 回归试点一条流程 | `pages/search_page.py` + `components/city_picker.py` + `data_factories/query_factory.py`（`uuid4`+`workerId`）+ `fixtures/env.py` 自建自清 | POM、数据工厂、断言在用例层 | `pytest suites/regression -q` → 1 passed；**跑完 DB 中该工厂前缀的行数 = 0**（自清验证） |
| **M1-5** 并行 + 乱序 | 加第 2 条用例（隔离探针：两 worker 同时写不同 id） | 无共享可变数据 | `pytest suites/regression -n 2 -p randomly` 连跑 3 次 → 全绿；每次结束 DB 行数回到基线 |
| **M1-6** 门禁与质量 | `item._base_page` → 显式 fixture；`tools/blocked_gate.py` + ledger；三个 workflow；ruff/mypy 只对 diff 文件；blocked 时补 `page.content()` + png + url.txt；xfail/skip reason 前缀标准化 | 可运维性、失败可诊断 | `python tools/blocked_gate.py reports/junit.xml` 在造出的 4 种假报告上分别得 0/0/1/1（绿/黄/红）；`ruff check $(git diff --name-only)` 通过；`mypy` 通过 |

> **一处偏离原计划**：原本要给每个 suite 建独立 `pytest.ini`。实测否决了它——
> 子目录存在 `pytest.ini` 时 pytest 会把 rootdir 定到该子目录，**项目根的 `conftest.py` 不再被加载**，
> 结果是 `blocked_guard` / `base_page` 这些 fixture 全部消失（实测报错
> `fixture 'f' not found`，删掉子目录 ini 后立刻通过）。
> 因此全工程只保留根 `pytest.ini`，suite 隔离靠 `testpaths` + 调用路径实现。

### M1 的验收标准（你自己列的 13 条怎么落）

| 你的验收项 | M1 是否覆盖 | 落地方式 |
|---|---|---|
| 任意单条独立跑通过 | ✅ M1-4/5 | `pytest suites/regression -q -k <一条>` |
| 乱序通过 | ✅ M1-5 | `-p randomly` |
| 并行 2 worker 无冲突 | ✅ M1-5 | `-n 2` + 唯一 id |
| 定位器集中在页面对象 | ✅ M1-4 | `grep -rn "locator(" suites/*/tests` → 0 |
| 页面对象无业务断言 | ✅ M1-4 | `grep -rn "^\s*assert " suites/*/pages` → 0 |
| 数据工厂唯一、自建自清 | ✅ M1-4 | 跑完查 DB 计数 |
| 环境冷启动可复现、结束销毁 | ✅ M1-3 | `up --wait` + `down -v` |
| 播种幂等 | ✅ M1-3 | seed 连跑两次计数不变 |
| 无未脱敏生产数据 | ✅（靶站全合成数据） | — |
| 无固定 sleep | ✅ | `grep time.sleep \| grep -v human_delay` → 0 |
| **连续 3 次无 flake** | ✅ 改成靶站口径 | `regression-flake` job |
| CI 失败有诊断 | ✅ M1-6 | 截图 + trace + 容器日志 artifact |

### 时间与顺序的约束

M1-3 依赖 **Docker daemon 运行**（实测当前未启动）。M1-3 之前的所有步骤都不依赖它，可以先跑。
若 Docker 无法启动，M1-3 阻塞，M1-4/5 改用「靶站进程直跑（uvicorn 本地端口）」临时替代，
但**必须标注为临时方案**，因为那放弃了「环境冷启动可复现」这条验收。

---

## 4. 五个回填信息

| # | 问题 | 我的结论 | 依据 |
|---|---|---|---|
| 1 | 被测系统是否有可控本地/测试环境 | **本项目没有**。现有三个项目（price-pipeline / train-batch / train-e2e）分别是数据管道、车次查询工具、E2E 骨架，**都不带 Web UI**，没有可测的被测系统。所以靶站只能自建 | `ls <workspace>/2026-10-31-*/` 实测；三项目源码均为 CLI/库 |
| 2 | 监控频率与告警渠道 | 频率建议每日 08:30 CST 一次。渠道默认 GitHub Issue（零新凭据）；企微需你给 webhook | — |
| 3 | CI 是否允许 schedule、最低频率 | 允许。GitHub Actions 最小间隔 5 分钟，但 cron 有 5–30 分钟延迟；建议**每日 1 次**，不选整点。私有仓库注意：schedule 消耗分钟数，一个月约 30×12 = 360 分钟 | — |
| 4 | 内网 pip 源可用性 | **已实测可装**（阿里云源 dry-run 解析成功）：`xdist 3.8.0`、`randomly 5.0.0`、`ruff 0.17.3`、`mypy 2.4.1`、`Faker 40.44.0`、`timeout 2.4.0`。清华源仍 403（curl 同 URL 是 200），继续用阿里云 | `pip install --dry-run` 退出码 0 |
| 5 | 三项目是否同仓库 | **都不是 git 仓库**（16 个目录逐一 `rev-parse` 全部 false）。所以：train-e2e 单独建仓；`packages/testkit` **M1 不建**，等两套 suite 稳定后（M3）再评估抽取 | 实测 |

### 靶站的功能范围（需要你确认，问题 1）

自建靶站只覆盖验证框架所必需的最小面，**不模拟真实票务业务**：

1. `GET /healthz` → 200 + `{"status":"ok"}`（CI 健康检查）
2. `POST /api/queries` → 建一条查询（`from/to/date/owner`），返回 `id`
3. `DELETE /api/queries/{id}` → 清理
4. `GET /search?from=&to=&date=&qid=` → 页面：城市联想下拉（异步接口）、结果列表（按 `qid` 隔离，只返回本 qid 的数据）
5. `GET /__fail?mode=timeout|500|empty` → 故障注入（用于验收「失败有证据」「三种失败语义」）

**只有这 5 个能力**。不加登录、不加支付、不加日历控件。理由：日历是真实站点的难点，靶站复刻它只会让框架为了一个控件变形。

---

## 5. 风险与需要你拍板的点

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | Docker daemon 未运行（实测 `npipe` 连不上） | M1-3 直接阻塞 | 需启动 Docker Desktop；或你同意先用本地 uvicorn 顶替（放弃冷启动验收） |
| R2 | 靶站是自建的，**验证不了真实业务正确性** | 只能证明"框架能用"，不能证明"携程能查" | README / FRAMEWORK-AUDIT 里明确区分两轨，轨道 B 只做可用性监控 |
| R3 | blocked 门禁把今天的运行判红（G1） | 交付即红 | 这是设计意图，不是缺陷。先让红出现，再决定是加告警还是降低访问频率 |
| R4 | `-p randomly` 与数据工厂必须配对 | 第一次启用就会暴露共享数据问题 | 这正是验收信号，不允许用 `--randomly-dont-reset-seed` 绕过 |
| R5 | ledger 提交到 `ops/metrics` 分支需要 `contents: write` | 权限不足则 G3/G4 失效 | 若不允许写权限，G3/G4 降级为"不可用"并在报告里明写，不假装有 |
| R6 | 现有 `detect_block` 只认 `whaleguard` 一种 WAF 页 | 换一种短路页又漏判 | M1-6 把 body 正则抽成可配置列表（`BLOCK_BODY_PATTERNS`），新增特征只需加一行 |

### 需要你确认（4 项，会直接改变 M1 的交付形态）

1. **Docker**：允许我启动 Docker Desktop 吗？（不启动则 M1-3 换 uvicorn 临时方案）
2. **靶站范围**：上面 5 个能力够不够，要不要加"日期控件"（我建议不加）
3. **ledger 落点**：接受 `ops/metrics` 独立分支 + `contents: write` 吗？若否，G3/G4 判为不可用
4. **远端仓库**：`testhy22/train-e2e` 私有仓库，还是只本地 `git init` 不推远端？推的话我需要 SSH over 443 的既有配置

M1 的代码改动全部落在这四点的答案范围内。这四点未定之前，我只做 M0（git 基线不依赖任何一项）。
