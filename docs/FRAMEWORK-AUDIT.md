# 自动化测试框架：现状审计与目标设计（阶段 0 + 阶段 1）

审计对象：`<project-root>`（Playwright + Python + pytest + POM）
审计时间：2026-10-31 23:2x ｜ 方式：只读扫描 + 实际运行
审计者视角：SDET / 测试架构

> 结论先行：这套工程在「分层、POM、失败取证、blocked 语义化」四件事上已经达标；
> 缺口集中在**数据隔离、并行、环境容器化、独立可跑性验证**四项，且缺口是结构性的，不是补几行代码能解决的。

---

## 1. 现状审计表

| # | 问题 | 证据（文件:行 / 命令输出） | 影响 | 优先级 |
|---|---|---|---|---|
| A1 | 测试数据是**共享只读 YAML**，靠"每条用例各查各的区间"回避冲突，没有数据工厂 | `test_data/search_cases.yaml`；`utils/data_loader.py:77 load_cases()` | 目标站一旦按账号/IP 计次，无法给每条用例生成唯一查询；并行时无隔离标识 | P1 |
| A2 | **无并行能力**：`pytest-xdist` 未安装 | `python -c "find_spec('xdist')"` → MISSING | 3 组数据串行跑，CI 时间线性增长；也无法验证"并行 2 worker 无冲突" | P1 |
| A3 | **无容器化**：无 Dockerfile / compose，直接在本机浏览器跑 | `ls Dockerfile docker-compose.yml` → 无；本机 Docker 29.9.2 / Compose v5.6.1 已装但未使用 | 环境漂移：本机 chromium-1248 与 CI 的镜像内核可能不同版本，线上红了无法在本地复现 | P1 |
| A4 | **无 git 仓库**，改动无版本可回溯 | `git rev-parse` → 非 git 仓库 | 「小步提交、每步可验证」无法落实；出问题无法定位是哪次改动引入 | P0 |
| A5 | **定位器与目标站强耦合**：`.list-item`、`div.search-date` 都是携程特征 | `pages/train_search_page.py:33-52` | 站点改版即红；已在 README §6 集中登记，可替换，但无契约测试兜底 | P2 |
| A6 | **无 lint / typecheck 门禁** | 无 ruff / mypy 配置；`requirements.txt` 仅 5 项 | 低级错误（未用变量、类型不匹配）靠人眼；`conftest.py` 里 `request.node._base_page` 这类动态属性无静态检查 | P2 |
| A7 | **fixture 用了动态属性**：`item._base_page`、`item._blocked_signal` | `conftest.py:63,86` | type: ignore 掩盖了拼写错误的风险；换人接手不知道这两个属性从哪来 | P3 |
| A8 | **断言与取证耦合在 conftest 钩子里**，blocked 判定分散在 `base_page.detect_block()` 与 `widget_guard` | `pages/base_page.py:120-170`；`conftest.py:96-140` | 新增一种拦截形态要改 base_page；新增一种控件异常要改 conftest，两处容易漏 | P2 |
| A9 | **没有"乱序运行"验证**，用例之间事实上无依赖，但没有机制保证未来不引入 | 无 `pytest-randomly`，无相关 CI 步骤 | 一旦有人写了依赖顺序的用例，只有上线后才炸 | P2 |
| A10 | **trace 缺口**：blocked 走 xfail 不产 trace | 实测 `find reports/trace -name '*.zip'` → 0 个；因为 `retain-on-failure` 只对真实失败生效 | 被拦时只有截图没有 DOM 快照，排查"是拦了还是页面没加载"要多花时间 | P3 |
| A11 | **UI 文案/结构变更的隔离只做到一半**：定位器集中在 POM，但断言里的文案（"共 N 车次"）在 POM 里也是正则 | `train_search_page.py:47 RESULT_COUNT_RE` | 文案变化要改 POM，这条本身合规（断言在测试层），可接受 | P3 |
| A12 | **无敏感数据处理规范**：当前无账号、无凭据，但 `BASE_URL` 可被注入任意地址 | `config/settings.py:52 base_url()` 仅校验前缀 | 误把内网地址/带 token 的 URL 写进 CI 日志 | P3 |

**审计过程中发现并已修复的一处**（记录在此，避免被当成"一直是对的"）：

| 审计项 | 审计前 | 审计后 | 怎么发现的 |
|---|---|---|---|
| 测试文件是否有散落定位器 | `tests/test_smoke.py:23`、`tests/test_train_search.py:41` 各有 `page.locator("body")` | 改为 `base_page.body_text()` / `train.body_text()`，`grep` 结果为 0 | 写验收表时按证据门逐条 grep 自查，发现结论与代码不符 |

补充事实（用于判断，不构成问题）：- 已达标项：POM 分层（`pages/` 与 `tests/` 分离）、定位器集中为类属性、页面对象无业务断言
  （`pages/train_search_page.py` 全部方法只返回状态）、无裸 sleep（全目录 grep 仅注释命中）、
  失败自动截图 + URL/title 写报告（实测 16 张截图落盘）。
- 三个测试项目并存：`2155-price-pipeline`（unittest + 变异门禁）、`2240-train-batch`（断言层 + 变异门禁）、
  `2250-train-e2e`（POM + E2E）。**三者共享的规范尚未抽取**，是推广时的第一优先级。

---

## 2. 目标架构与目录树

```
ctrip-train-e2e/
├── config/
│   ├── settings.py            # 唯一配置出口（站点/超时/视口/常量/证据目录）
│   └── env.py                 # 【新】环境档位：local / ci / docker，只负责解析环境变量
├── pages/                     # 对象层：只操作 + 取状态，零断言
│   ├── base_page.py
│   └── train_search_page.py
├── components/                # 【新】可复用组件对象（被多个页面共享的 UI 块）
│   ├── city_selector.py       # 城市输入 + 联想下拉
│   └── date_calendar.py       # 日历 widget（含"唤不起"的显式异常）
├── fixtures/                  # 【新】pytest fixture，按职责拆分，不再堆在 conftest.py
│   ├── browser.py             # 上下文/启动参数/tracing
│   ├── evidence.py            # 失败截图、URL、trace 归集
│   └── guards.py              # blocked_guard / widget_guard
├── data_factories/            # 【新】唯一数据生成入口
│   ├── base.py                # 唯一标识：<prefix>-<utc时间戳>-<workerId>-<序号>
│   └── search_case.py         # 查询用例数据（区间+偏移+期望下界）
├── api_clients/               # 【新】API 层：预置/清理/健康检查（与 UI 混合框架的钩子）
│   └── health.py              # 环境健康检查（站点可达性，用于 CI 前置门禁）
├── seeders/                   # 【新】环境播种（幂等）
│   └── seed.py
├── test_data/                 # 静态基线数据（只读，不含可变状态）
│   └── search_cases.yaml
├── tests/
│   ├── test_smoke.py
│   ├── test_train_search.py
│   └── test_data_loader.py
├── tools/
│   ├── probes/                # 定位器探针（一次性，不参与运行）
│   └── ci_blocked_report.py
├── docker/                    # 【新】容器化
│   ├── Dockerfile             # 与 requirements.txt 同版本锁定 playwright
│   └── docker-compose.yml     # 独立 project name、健康检查、退出即销毁
├── docs/
│   ├── locator-evidence/
│   └── FRAMEWORK-AUDIT.md
├── conftest.py                # 只保留 pytest_plugins 与钩子，fixture 全部下沉 fixtures/
├── pytest.ini
├── requirements.txt
└── .github/workflows/ci.yml
```

分层职责（一句话一条）：

| 层 | 只做一件事 | 不允许 |
|---|---|---|
| `tests/` | 编排步骤 + 断言 | 出现定位器、出现 sleep、出现数据构造细节 |
| `pages/` | 页面级操作与状态读取 | 业务断言、跨页面流程编排 |
| `components/` | 可复用 UI 块（城市选择器、日历） | 知道"这是火车票页" |
| `fixtures/` | 提供实例、管生命周期、取证 | 写业务逻辑 |
| `data_factories/` | 生成唯一、隔离的数据 | 读测试文件、依赖执行顺序 |
| `seeders/` | 幂等播种基础数据 | 依赖上一次运行残留 |
| `api_clients/` | 预置/清理/健康检查 | 承担 UI 断言 |

---

## 3. 关键规范

### 3.1 POM 规范
- 定位器一律写成类属性（大写常量），集中在类顶部，附一行注释说明探测依据。
- 方法命名：动作 `select_city/submit`，取状态 `result_items/url_depart_date`。
- **页面对象里出现 `assert` 即视为违规**（例外：`expect(...).to_be_visible()` 这类等待，不算业务断言）。
- 页面对象抛两类异常就够：`BlockedByAntiBot`（对方拒绝）、`WidgetUnavailable`（控件不响应）。
  业务失败一律靠返回值 + 测试层断言。

### 3.2 定位器规范
- 优先级：`get_by_role` > `get_by_placeholder` / `get_by_label` > `get_by_text` > 稳定 id > 属性选择器 > 层级 CSS。
- 禁止：`nth-child`、超过 3 层的后代选择器、`//div[@class='x']/div[2]` 这类位置 XPath。
- 现有 `.list-item`、`.widget-calendar-depart-wrap` 属"属性选择器"档，可接受但与站点强耦合，
  必须在 `docs/locator-evidence/` 留探测依据，改版时按依据重探。

### 3.3 断言规范
- 三层：结构（条目数/日期生效）→ 内容（车次号形态与覆盖率）→ 辅助（关键词，只进报告）。
- 数字类断言必须写清"下界还是期望值"（`min_results` 是下界，末班车时段只剩 1 条属正常）。
- 断言失败信息必须能独立读懂：带区间、日期、实际值、期望值。

### 3.4 数据工厂规范
- 唯一标识格式：`{prefix}-{utc:%Y%m%d%H%M%S}-w{worker_id}-{seq}`，`worker_id` 取自 `PYTEST_XDIST_WORKER`。
- 工厂只负责"造数据"，不负责"用数据"；用完即弃，不写回静态 YAML。
- 静态 YAML 只放**不随时间变化**的基线（区间、期望下界），日期一律用偏移量在运行时解析。

### 3.5 清理规范
- 每个用例 `yield` 前自建、`yield` 后自清；清理失败不许吞异常（要打成 warning 进报告）。
- 不允许"全局 teardown 一把清"，那样并行时互相踩。

### 3.6 环境隔离规范
- 每次运行一个独立 `COMPOSE_PROJECT_NAME`（`e2e-{github_run_id}`），结束 `down -v`。
- 容器内 browser 版本由 `docker/Dockerfile` 锁死，**与 requirements.txt 的 playwright 版本同源**。
- 健康检查不通过 → 直接 fail（环境问题不该伪装成用例失败）。

### 3.7 敏感数据规范
- 凭据只从环境变量读，代码里 `grep` 不到；CI 用 secrets。
- 报告/截图落地前过滤 `Authorization`、`Cookie`、URL 里的 token 参数。
- 生产样本一律脱敏为只读副本；本工程当前不含任何生产数据。

---

## 4. 分阶段迁移计划

| 阶段 | 动作 | 可验证的产出 | 风险控制 |
|---|---|---|---|
| M0 | `git init` + 首次提交（含 .gitignore） | `git log` 有基线；`git status` 干净 | 先提交再改，任何一步可回滚 |
| M1 | fixture 下沉 `fixtures/`，`conftest.py` 只留钩子 | `conftest.py` < 30 行；测试输出与 M0 完全一致 | 纯搬迁，行为不变 |
| M2 | 引入 `pytest-xdist`，跑 `-n 2` | 2 worker 全绿；用例名带 worker 标识 | 数据是只读 YAML，天然可并行 |
| M3 | 抽 `components/`（city_selector、date_calendar） | POM 文件变短；组件可被第二个页面复用 | 纯重构，行为不变 |
| M4 | 引入数据工厂 + 唯一标识 | 每条用例日志有唯一 id；`-n 2` 仍绿 | 目标站按 IP 计次，需控制并发上限 |
| M5 | Dockerfile + compose（独立 project name） | 冷启动一次跑通；`down -v` 后无残留 | 镜像层缓存，避免每次重下浏览器 |
| M6 | CI 加 lint/typecheck（ruff + mypy） | 首次运行允许基线告警，第二次起必须为 0 | 分两步：先只报不拦，再拦 |
| M7 | CI 加乱序（`pytest-randomly`）+ 重复 3 次 | 3 次结果一致，无 flake | 单独 job，不阻塞主 job |

**不做的事**：不一次性重写。每一步都以"测试输出与上一步一致（除新增能力外）"为验收。

---

## 5. 验收清单与量化标准

| 验收项 | 量化标准 | 当前状态 |
|---|---|---|
| 单条用例独立可跑 | `pytest -k <id>` 单独跑通过（或被拦 xfail） | ✅ 已验证（`-k sh-bj`） |
| 乱序运行 | 打乱顺序后结果集合不变 | ❌ 未验证（无该机制） |
| 并行 2 worker 无冲突 | `-n 2` 全绿，无共享状态告警 | ❌ 未验证（xdist 未装） |
| 文案/结构小变更只改 POM | 改 1 个类属性即可让全部用例恢复 | ⚠️ 结构上满足，未做演练 |
| 测试文件无散落定位器 | `grep -rn "locator(\|get_by_" tests/` 结果为 0 | ✅ 已扫描 |
| 页面对象无业务断言 | `grep -rn "assert " pages/` 为 0（`expect` 不计） | ✅ 已扫描 |
| 数据工厂生成唯一数据 | 每条用例有唯一 id，无共享可变数据 | ❌ 无工厂 |
| 环境冷启动可复现、结束自销毁 | compose 起→跑→down 一条命令 | ❌ 无容器 |
| 播种幂等 | 连续播种两次，数据量不变 | ❌ 无种子脚本 |
| 无未脱敏生产数据 | 全仓库无真实账号/凭据/订单 | ✅ 当前无 |
| 无固定 sleep | `grep -rn "time.sleep\|wait_for_timeout"` 仅命中注释 | ✅ 已验证 |
| 失败有诊断 | 截图 + URL + title 必落盘；trace 仅真实失败 | ⚠️ blocked 无 trace |
| CI 连续 3 次无 flake | 同一 commit 三次运行结论一致 | ❌ 未跑（受站点风控限制，见风险 R1） |

---

## 6. 风险、假设、待确认

**风险**
- R1：目标站点的风控使「连续 3 次无 flake」这条验收在真页面上**短期做不到**——
  实测约十余次访问后即返回 `whaleguard block`。缓解：把 flake 验收改为"同一天内 3 次，
  间隔 ≥ 2 小时"，或先在容器化的本地靶站上做 flake 验收（这也回答了下述问题 Q3）。
- R2：容器化后 IP 仍是同一出口，风控策略不变；容器解决的是"环境一致"，不解决"访问被拒"。

**假设**
- 假设 1：目标站点允许低频率人工等价访问（打开页面、查一次车次），不允许批量采集。
- 假设 2：CI 跑在 GitHub 托管 runner 上，出口 IP 与本地不同，被拦概率不同。

**需要你确认（≤5）**
1. 这套框架的目标是「验证自己的站点/被测系统」还是「长期监控第三方站点」？
   前者应立即引入容器化的本地靶站；后者必须接受"随时被拦"并只做低频探测。
2. 允许引入哪些新依赖？当前缺 `pytest-xdist` / `pytest-randomly` / `ruff` / `mypy` / `faker`。
3. 是否接受把 smoke 探测（只打开页面、不交互）作为 CI 的唯一常驻任务，
   功能用例只在手动 dispatch 时跑？
4. 容器化的目标是"CI 环境一致"还是"本地也能一键起"？两者 Dockerfile 设计不同。
5. 是否需要把三个测试项目（price-pipeline / train-batch / train-e2e）的公共规范抽成共享包？
   抽的话按 git submodule 还是 pip 私有包？
