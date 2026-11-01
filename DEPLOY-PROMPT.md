# 给同事的一键部署提示词（可直接复制）

> 本文件是给**同事**用的：让他把自己电脑上的 AI 编程助手（WorkBuddy / Claude Code / Codex 等）
> 打开，把下面「【部署提示词】」整段复制粘贴进去执行即可。仓库地址已替你填好，
> 「目标目录 / 操作系统 / 已装运行时」等属于同事本机的部分，让他自己替换。

---

## 项目备注（给同事看，粘贴时可保留也可删）

- **这是什么**：`ctrip-train-e2e` 是一个 **Playwright + pytest 的浏览器驱动 E2E 测试项目**，
  **不是 Web 服务、也不是纯 CLI 库**。
- 因此提示词第 6 步「验证」请按**测试项目**理解：启动后没有 HTTP 地址可 curl，
  验证方式是跑 `python -m pytest` 并确认控制台出现 `N passed`（被风控的用例会显示 `XFAIL`，
  属于正常「一等结果」，不算失败）。
- 需要联网访问目标站点；登录态是**真人扫码**产生的 `storage_state` 文件，CI 里没有、也不该有。

---

## 【部署提示词】（复制下面全部内容给 AI 助手）

你是一名有 5 年经验的 DevOps 工程师。请帮我在本地电脑上部署并运行下面这个 GitHub 项目。

【项目信息】
- 仓库地址：https://github.com/testhy22/train-e2e.git
- 目标目录：<替换为你想克隆到的本地路径，例如 ~/projects/train-e2e>

【执行规则】
1. 先克隆仓库到目标目录。如果目标目录已存在同名文件夹，停下来问我，不要覆盖。
2. 克隆后，先阅读 README.md 和根目录下的依赖声明文件，识别技术栈（Node.js / Python / Go 等）和包管理器。
3. 检查并安装依赖。优先使用项目锁文件对应的命令（package-lock.json → npm ci，pnpm-lock.yaml → pnpm install，requirements.txt → pip install -r，pyproject.toml → pip install -e . 等）。不要跳锁文件直接 npm install / pip install。
4. 处理环境变量：如果存在 .env.example，复制为 .env，并把我接下来提供的变量值填入。变量值我暂时没有的，留空并在最后告诉我哪些需要我手动补充。绝对不要把 .env 提交到 git。
5. 启动项目。按照 README 或 package.json scripts / pyproject scripts 中的 dev 或 start 命令启动。启动后等待，直到控制台出现明确的“服务已就绪”信号（例如 Listening on http://localhost:xxxx、Server running on port xxxx、Uvicorn running on …）。
   —— 本项目是 Playwright + pytest 测试工程，没有常驻 Web 服务；对应的“启动并验证”是执行 `python -m pytest`，等待控制台出现 `N passed` / `XFAIL` 汇总行。
6. 验证：如果项目是 Web 服务，用 curl 或浏览器访问启动日志中给出的地址，确认返回 200 或预期页面；如果项目是 CLI / 库，运行一个最小示例确认能 import / 执行。
   —— 本项目请运行 `python -m pytest` 一次，确认用例能收集并执行、控制台出现通过汇总（数字随用例数变化，被风控的会记为 XFAIL 而非失败）。
7. 每一步执行后，告诉我当前状态。遇到报错时，先判断是依赖缺失、版本不兼容还是环境变量缺失，给出具体修复命令，不要笼统地说“出错了”。
8. 全部完成后，输出一份简洁的“本地运行摘要”：项目路径、使用的启动命令、服务地址（如有）、需要我手动补充的环境变量列表。

【我本地的已知条件】
- 操作系统：<Windows / macOS / Linux>
- 已安装的运行时：<例如 Node.js 20 / Python 3.11 / 两者都有>
- 我是否已有该项目的 .env 值：<是 / 否，如果是“是”请在这里粘贴变量>

现在开始执行第 1 步。

---

## 给同事补充的两点提醒（粘贴时一并带上）

**第一，先确认 README 里的版本要求。**
很多“拉下来跑不起来”是因为 Node 或 Python 版本不对。让同事在克隆后先看一眼 README 或
package.json 的 engines 字段，不匹配就先切换版本，再继续后面的步骤。
（本项目 README「〇、技术栈与版本要求」写明：Python 3.12 实测，依赖要求 3.9+；
运行前需 `python -m playwright install chromium` 装浏览器二进制。）

**第二，环境变量是最常见的卡点。**
如果你们项目需要 API Key、数据库连接串等敏感信息，不要在聊天记录或提示词正文里明文粘贴。
让同事在本地手动编辑 .env，或者通过系统环境变量注入。上面提示词第 4 步已经要求 AI 把缺失变量
单独列出来，而不是猜测填充——这一点在执行时要严格守住。
（本项目的 `.env.example` 已随仓库提交，变量都有代码内默认值，绝不含真实密钥；`.env` 本身被忽略，不会入库。）
