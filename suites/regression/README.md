# suites/regression —— 轨道 A（容器靶站）

**当前状态：空骨架，等 M1-3。**

这里放的是**不依赖外网、可以随便跑**的回归用例：目标是一个本地 docker 起的靶站
（`docker/target-app`，M1-3 落地）。与 `suites/thirdparty` 的分工：

| | `suites/thirdparty`（轨道 B） | `suites/regression`（轨道 A） |
|---|---|---|
| 被测对象 | 真实第三方站点 | 自建靶站（FastAPI + SQLite） |
| 网络 | 需要外网，随时可能被拦 | 不需要外网 |
| 结果 | 可能 `blocked`（xfail） | 只允许 pass / fail，**不设 blocked 口径** |
| 触发 | 每日一次 / 手动 | PR + nightly |
| 门禁 | `tools/blocked_gate.py` | 无（真失败就是失败） |

约定：

1. **不在这里写会联网的用例。** 一旦要访问第三方，它属于轨道 B。
2. 靶站的数据由工厂造、由用例自己清，不许依赖「跑之前先手工灌数据」。
3. 目录结构对齐轨道 B：`tests/` + `pages/`（页面对象）+ `data_factories/`。
