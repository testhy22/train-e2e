# 第一阶段交付：目录结构 · 文件职责 · 数据契约 · 验收对应表

> 本文件只做结构与契约确认，不含实现代码。确认后再进入第二阶段逐文件填充。
> 时间：2026-10-31 ｜ 状态：待确认（目标站点一项需要你先定，见第 6 节）

---

## 1. 目录结构与文件职责

```
train-e2e/
├── config/
│   └── settings.py           # 唯一的配置出口：BASE_URL（可被环境变量覆盖）、
│                             # 超时常量、浏览器启动参数、报告/证据目录路径。
│                             # 规则：其他文件不得出现硬编码 URL 或路径。
├── pages/
│   ├── base_page.py          # 通用能力，只放三样：导航、截图、有界等待。
│   │                         # 规则：不放业务方法、不放具体定位器。
│   └── train_search_page.py  # 查询页 POM。对外暴露业务语义方法：
│                             #   goto() / fill_route(from, to) / pick_date(date)
│                             #   / submit() / read_results() -> list[dict]
│                             # 所有 locator 与交互细节封闭在本文件内。
├── test_data/
│   └── search_cases.yaml     # 用例数据（契约见第 3 节）。不得出现定位器与断言逻辑。
├── tests/
│   └── test_train_search.py  # 只做三件事：读数据、编排 POM 步骤、断言。
│                             # 全文不得出现 locator / click / fill。
├── utils/
│   └── data_loader.py        # 读 YAML → list[Cases]；解析日期偏移 → 具体日期；
│                             # 校验数据合法性（日期非过去、城市非空）；
│                             # 生成 parametrize 的 ids。
├── reports/                  # 运行时生成，不入库
│   ├── screenshots/
│   ├── trace/
│   └── html/                 # pytest-html 输出
├── conftest.py               # 根级 fixture：browser context（带 trace 配置）、
│                             # page、失败截图钩子、数据加载 fixture、CLI 选项。
├── pytest.ini                # 注册 mark、报告插件参数、默认 --reruns、
│                             # 禁止用 -p no:cacheprovider 掩盖状态。
├── requirements.txt          # 版本锁定（含 pytest-rerunfailures，见 5.D2）
├── .gitignore                # 补：reports/、__pycache__/、.pytest_cache/、.playwright/
├── .github/workflows/ci.yml  # 装依赖 → 装浏览器（带缓存）→ 跑 → 上传 reports/
└── README.md                 # 运行方式、数据格式、证据位置、CI 说明
```

相对你给的结构，**只加了一个文件 `.gitignore`**（理由见 5.D4），其余无增删。

---

## 2. 分层边界（哪一层允许出现什么）

| 层 | 文件 | 允许 | 禁止 |
|---|---|---|---|
| 业务层 | `tests/` | 读数据、调 POM 方法、断言 | `page.`、`locator`、`click`、`fill`、任何 URL |
| 对象层 | `pages/` | locator、交互、等待、取值 | 断言、pytest 依赖、读 YAML |
| 工具层 | `utils/` | 解析、校验、纯函数 | IO 到浏览器、pytest 依赖 |
| 基础层 | `config/`、`conftest.py` | 常量、fixture、钩子 | 业务语义、具体页面元素 |

这条边界的实际检验方式：把 `pages/` 整个目录删掉，`tests/` 应该只剩 `import` 报错，而不是还能跑；
反过来 `pages/` 里不应该出现 `assert`。

---

## 3. 数据契约（你给的版本需要改两处）

你给的结构：

```yaml
- id: sh-bj-1031
  from_city: 上海
  to_city: 北京
  depart_date: "2026-10-31"
  expected_keyword: 车次
```

问题在 `depart_date`：**2026-10-31 就是今天**。车次查询的语义是「未来可取的车次」，
日期一旦落到当天且已过末班车，结果必然为空。实测证据：2026-10-31 22:44 查上海→北京当日，
上游返回空数组（3 字节）、退出码 0。这组数据今天跑必红，明天跑是「过去日期」，更红。

建议改成偏移式，日期由 loader 在运行时计算：

```yaml
- id: sh-bj-d7
  from_city: 上海
  to_city: 北京
  depart_offset_days: 7          # 今天 +7 天；loader 算成绝对日期并注入用例
  expected_keyword: 车次
  min_results: 1                  # 结果条目数下界（替代单关键词断言，见 5.D3）
```

loader 的职责因此增加两条：把偏移算成 `YYYY-MM-DD`；若算出的日期落在过去（时钟漂移、
数据被长期复用），**该条 skip 并给出原因**，既不算通过也不算出错。

---

## 4. 验收标准逐条对应

| 你的验收标准 | 落在哪 | 如何自证 |
|---|---|---|
| 3 组数据各自生成独立用例，名字对应 id | `utils/data_loader.py` + `parametrize(ids=...)` | `pytest --collect-only -q` 输出三条，名字即 id |
| 搜索后必须出现 `expected_keyword`，否则失败 | `tests/` 断言 + `read_results()` 返回值 | 造一条不存在的城市组合，看它变红 |
| 失败时 `reports/screenshots/` 有截图 | `conftest.py` 的 `pytest_runtest_makereport` 钩子 | 故意断言失败，检查文件存在且非 0 字节 |
| 失败时 `reports/trace/` 有可回放 trace | `context` 的 `tracing.start` 参数 | `playwright show-trace` 打开文件（**当前配置下产不出，见 5.D2**） |
| CI 能装依赖、装浏览器、跑、上传产物 | `.github/workflows/ci.yml` | 本地用 `act` 或推一次分支看 run |

---

## 5. 规格里 5 处必须改的地方（否则做不到或会假绿）

**D1 · 数据日期是定时炸弹**：见第 3 节。这是最硬的一条，按原样交付第一天就是红的。

**D2 · `trace=on-first-retry` 在当前配置下永远产不出 trace。**
Playwright 只在**发生重试**时记录第一个失败的 trace。而 pytest 默认不重试，
`requirements.txt` 里也没有 `pytest-rerunfailures`。所以「失败时 trace/ 下应有可回放文件」
这条验收标准，按字面实现**无法满足**——这不是实现不努力，是配置互相矛盾。
两条路选一条：
- 简单：`trace="retain-on-failure"`（失败必有 trace，代价是成功用例不记）
- 保留原意：加 `pytest-rerunfailures` + `--reruns 1 --reruns-delay 2`，此时 `on-first-retry` 才有意义，
  但 CI 时长翻倍、且「重试后通过」会掩盖不稳定用例（要用 `--only-rerun` 或单独统计 flaky 率）

我倾向选第一条，并在 README 写明：trace 只保留失败用例。

**D3 · `expected_keyword: 车次` 是弱断言，会假绿。**
只要整页文本里出现「车次」二字就算过——页头、筛选器、空状态提示里都可能出现。
改成：断言结果集合**非空**且条目数 ≥ `min_results`，并抽检条目字段形态（车次号匹配 `^[GDCZTK]\d+$`）。
关键词保留，但降级为辅助条件。

**D4 · 结构缺 `.gitignore`。**
规格要求「`reports/` 不提交静态文件」，但没给忽略规则。截图 + trace 很容易到几十 MB，
一次误提交就永久进历史（后续要清得重写历史）。补一个。

**D5 · 「禁止 sleep」与「模拟真人输入」互相打架，需要写准。**
`press_sequentially(text, delay=80)` 本质就是 sleep，只是封装在 Playwright 里。
规则应表述为：**禁止用裸 `time.sleep` 做同步等待**（等待一律用 `expect` / 自动等待）；
为触发联想下拉而设置的输入节奏是允许的，但必须有上限（单字符 ≤100ms、整串 ≤3s）。

---

## 6. 需要你先定的一件事：目标站点

`BASE_URL` 指向哪里，决定了这套验收标准能不能真跑通：

| 选项 | 能不能满足全部验收 | 代价 |
|---|---|---|
| **A. 本地 Mock 站**（我建：城市输入 + 联想下拉 + 日期选择 + 异步结果列表 + 可注入失败） | 能，且可离线、可 CI、可复现 | 需要多写一个静态站（约 200 行），但它同时也是「注入故障验证失败证据」的靶子 |
| B. 公开练习站（saucedemo / the-internet 等） | 部分能 | 没有「输入触发联想下拉」这个场景，你第 7 条工程规则覆盖不到 |
| C. 携程火车票页 | 不做 | 见下 |

C 我不做，理由和上一轮一致：OTA 的用户协议禁止自动化访问与抓取，而这份规格里「城市输入必须触发联想下拉、
不要依赖 Enter 盲确认」这类要求的落点就是绕开风控。技术上它也确实脆——页面改版、风控策略更新都会让你维护成本持续上升，
而这套 POM 结构本身并不依赖携程才能演示。

选 A 的话，同样的结构、同样的验收标准、同样的 trace 与截图，全部可在本机与 CI 复现；
将来真要接你自己的站或内部系统，只改 `config/settings.py` 里的 BASE_URL 和 `pages/` 下的定位器。
