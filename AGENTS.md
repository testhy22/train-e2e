# AGENTS.md — 本仓库给 AI Agent 的硬性红线

> 作用：这些是**踩过坑之后**定下来的规矩，不是风格偏好。违反任意一条都会重演一次已经付出过代价的故障。
> 与 `docs/KNOWN-LIMITATIONS.md` 分工：那边记「现象与证据」，这里记「今后必须怎么做」。

## 一、导入与 pytest 结构

1. **禁止 `from conftest import xxx`**。`conftest.py` 由 pytest 自动发现，同名文件可以有多个，
   把它当普通模块导入时，解析结果取决于**收集顺序**，不是代码结构。
   实测（2026-11-01）：`pytest tools/tests suites/e2e` 一起跑时报
   `ImportError: cannot import name 'check_waf_block' from 'conftest'`，指向的是
   `tools/tests/conftest.py`；分目录单独跑则一切正常 —— 典型的「分着跑绿、合起来红」。
   - 公共函数 → `utils/*.py`（本项目既有分层就是 `utils/`，不要为它另开 `tools/utils/`，
     那会让「工具模块在哪」出现两个答案）。
   - 依赖 pytest 上下文的 → 写成 `@pytest.fixture`。
   - 兼容期可保留 conftest 里的同名转发函数，但新代码一律直接导入真实模块。

2. **提交前至少跑一次跨目录全量收集**：`pytest --collect-only -q tools/tests suites/*`。
   只跑单个目录永远发现不了上面的同名冲突。

## 二、依赖与版本

3. **禁止假设「索引列表末尾 = 最新版本」**。PyPI 的 simple 页面排序不可依赖；
   实测（2026-11-01）`allure-pytest` 列表尾部是 2.9.45，而真正的稳定最新是 **2.16.2**，
   并且 2.9.x 与 pytest 9 不兼容 —— 拿错版本会得到「装成功了但全挂」。
   取版本一律用 `python scripts/check_latest.py <包名>`（SemVer 排序 + 排除预发布）。

4. **依赖必须 `==` 锁死**，且**会影响「命令能不能跑起来」的插件放 `requirements.txt`
   而不是 `requirements-dev.txt`**。`allure-pytest` 就是例子：`pytest.ini` 的 addopts 带
   `--alluredir`，缺插件时 pytest 直接以 `unrecognized arguments` 退出。

5. **升级/降级插件后必须跑一次真实用例**，不能只看 `pip list` 或 `--version`。
   兼容性问题只在 setup 阶段才炸。

## 三、报告与证据

6. **Allure 多文件报告不能用 `file://` 打开**：数据靠 XHR 读 `data/*.json`，浏览器会拒，
   页面显示 `500 Failed to fetch` —— 看起来像报告坏了，其实是打开方式不对。
   给人看的那份用 `reports/allure-report-single/index.html`（单文件，双击即可）；
   留 `history` 的那份是 `reports/allure-report/`（趋势续接靠它，不能删）。

7. **不许 `--clean-alluredir`**：history 存在结果目录里，清了趋势图每次只有一根柱子。

8. **失败证据三件套优先级**：截图（视口）> 页面诊断文本 > trace。
   trace 默认关（`E2E_TRACE=1` 才录），因为整场一个 context 录下来是几十 MB。

## 四、下载与网络

9. **禁止对同一个慢源反复硬等**。单次尝试失败即换源，不要原地重试同一个 URL。
   实测（2026-11-01）：`gh-proxy.com` 返回 **HTTP 200 但只有 1.58 MB**（真包 30 MB），
   zipfile 打开直接 `BadZipFile` —— 「200」不等于「下载成功」，必须校验体积/哈希/可解压。

10. **二进制包优先走非 GitHub 渠道**（按可行性排序，均已实测）：
    - Allure CLI → Maven 中央仓库 `repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/`
      （30.4 MB / 27 s / 1.1 MB/s，且**提供 `.sha256`**）；
    - JRE → 清华 Adoptium 镜像 `mirrors.tuna.tsinghua.edu.cn/Adoptium/`（43.8 MB / 15 MB/s）；
    - Python 包 → 清华 PyPI 镜像（本机 pip 解析异常时，直接取 `packages/.../*.whl` 本地安装）。
    GitHub 直连只作为最后手段，且必须带 `curl --ssl-no-revoke`（本机 schannel 吊销检查会失败）。

11. **下载脚本统一调用 `tools/smart_download.py`**，不要在命令里自由发挥 curl 组合。
    该脚本做四件事：多源并发测速 → 按体积自适应超时 → 断点续传 → 体积/哈希校验。
    **不要**给它写死 15 秒超时：30 MB 的包在 1 MB/s 下需要 30 s，写死 15 s 等于永远失败。

## 五、收尾

12. 改动了 `conftest.py` / `pytest.ini` / 依赖版本后，**必须**跑一次
    `pytest tools/tests` + 目标套件，并在回复里给出实测数字（passed 数 + 耗时）。
13. 报告类改动要给出可核对的三项：统计数字、业务分层是否成型、环境信息是否落盘。

## 六、破坏性工具的测试纪律（2026-11-02 起）

14. **凡会删/改文件的东西（`utils/cleaner.py`、迁移脚本、批处理），验证一律在隔离目录里做**，
    不得在生产目录上试。本工程的合格示例：`tools/probes/probe_cleaner_bench.py` ——
    在项目根内自建 `_tmp_cleaner_bench/`，造数据 → 跑 → 删自己造的目录，
    `reports/` 全程不写。（为什么不能放系统临时目录：`cleaner._guard_target` 要求目标解析后
    仍在项目根之内，`tempfile.TemporaryDirectory()` 会被直接拒绝 —— 这不是 bug，是防误删红线。）

15. **先 `dry-run` 再真跑**：`E2E_CLEAN_DRY_RUN=1` 只统计不删。另外两个开关的语义别记反：
    `E2E_CLEAN_HOURS=72` 是**抬高阈值少删**，`E2E_CLEAN_DISABLE=1` 是**整个跳过**并会连带
    让被测的 4 条 `test_cleaner` 变红（2026-11-02 踩过，负阈值 `-1` 更糟：所有文件都算过期）。

16. **同名覆盖 = 假绿灯**：pytest 不检查同名 `test_` 函数，后者覆盖前者、报告照样全绿。
    已由 `tools/tests/test_no_duplicate_defs.py` 用 AST 兜住（含判据自证用例）；
    手工追加测试时也别只看"跑绿了"，要看 passed 数对不对。（2026-11-02 实际发生：追加时重复了两组参数化。）

