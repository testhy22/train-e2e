# 本地可见调试与用例数据

两份东西放一起，因为它们是同一类东西：**只影响本地怎么跑，不影响 CI 判定**。

- 上半：把浏览器窗口显示出来（`HEADED` / `HUMAN` / `SLOWMO` / `HUMAN_SEED`）
- 下半：用例数据放哪、怎么覆盖（`data/`、`CASE_DATA_DIR`）

门禁、账本、threshold 不在这里 —— 那些看 `docs/BLOCKED-GATE.md`。

---

## 一、一键入口

```bash
scripts/run-headed.sh suites/hotels -m hotels
scripts/run-headed.sh suites/thirdparty -m ui
```

脚本固定 `HEADED=1 HUMAN=1`，其余 pytest 参数原样透传，运行前会打印
`HEADED / HUMAN / HUMAN_SEED / SLOWMO / 目标 suite / 完整命令`。

## 二、四个开关

| 变量 | 作用 | 默认 |
|---|---|---|
| `HEADED=1` | 有头启动，窗口真实显示在本机桌面 | 无头 |
| `HUMAN=1` | 拟人节奏：逐字符输入、鼠标分步移动、动作之间随机停顿 | **未设时跟随 `HEADED`**（有头开、无头关） |
| `HUMAN_SEED` | 固定停顿序列，便于复现时序问题 | 每次随机 |
| `SLOWMO` | **额外**的全局固定延迟（毫秒），只在单个动作仍看不清时用 | 不启用 |

`--slowmo` 与拟人节奏会**叠加**，所以默认别一起用：拟人已经让操作看得清，
固定间隔只会把节奏变机械。还看不清再 `SLOWMO=300`（或 `800`）。

酒店已接拟人；**火车票的页面对象还没接**，所以那条线想看慢动作要靠 `SLOWMO`：

```bash
SLOWMO=800 scripts/run-headed.sh suites/thirdparty -m ui
```

## 三、Linux 上没有显示服务时

脚本检测到 `DISPLAY` 与 `WAYLAND_DISPLAY` 都为空时，直接报
「当前环境看不到窗口」并 `exit 3`，**不用 xvfb 冒充可见** ——
那样窗口不会真的出现，只会让人一直等一个不存在的窗口。

CI 是另一条路径：它本来就没有人看窗口，用 `xvfb-run -a` 只是给 Chromium
提供一块虚拟屏让它能以有头模式启动（trains 在无头下整站 432，见 K-05）。

## 四、与 CI 的差异

| 场景 | 浏览器 | 拟人 | 说明 |
|---|---|---|---|
| 本地 `scripts/run-headed.sh` | 有头 | 开 | 只为人工看清过程 |
| CI `hotels` | headless | 恒关（`HUMAN=0`） | 酒店在无头下正常，见 K-05 的范围说明 |
| CI `ui`（trains） | `xvfb-run -a` + `HEADED=1` | 恒关（`HUMAN=0`） | trains 无头下整站被 432 拦，见 K-05 |

`HUMAN=0` 在 CI 里是**必须显式写**的：`HEADED=1` 会自动开启拟人，不写就等于开了。

拟人只调**时间**，不碰 User-Agent / 自动化标记 / stealth 脚本；也**不因为被拦就调慢重试** ——
被拦照旧记 `blocked` → `xfail`。三条边界写在 `utils/humanize.py` 文件头。

---

## 五、用例数据

```
data/
  hotels/search_cases.yaml     酒店四字段用例
  trains/smoke_cases.yaml      火车票冒烟用例
```

统一由 `utils/case_data.py` 加载，测试文件里不写任何数字：

```python
from utils.case_data import case_ids, load_hotel_search_cases

CASES = load_hotel_search_cases()
@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
```

### 文件形状

```yaml
version: 1        # 必填，只认 1；不认识的版本直接报错，不猜
suite: hotels     # 信息性字段，写进验收输出
marker: hotels    # 与 pytest.ini 注册的 marker 对齐
cases:
  - id: sh-2n1r1a # **ASCII slug**，中文放 name
    name: 上海-2晚1间1位
    city: 上海
    checkin_offset_days: 7
    nights: 2
    rooms: 1
    adults: 1
    children: 0
    tags: [smoke, sh]
    expected:
      city_display: 上海, 中国
```

三条约定：

1. **日期用偏移量，不写死。** 写死的日期今天能跑、明天就是过去时，
   用例会红得没有信息量。
2. **`expected` 只放「无法由输入直接推出」的预期。** 把 `nights/rooms/adults`
   再抄一份只会让两个数字日后各自漂移；一致性由 `tools/tests/test_case_data.py` 守。
3. **数据里不放**：选择器、URL、等待时长、`HEADED`/`HUMAN`、任何「重试/绕过」字段。
   那些属于代码或配置，不属于用例。

### 覆盖数据目录

```bash
CASE_DATA_DIR=/tmp/cases python -m pytest suites/hotels -m hotels
```

用环境变量而不是 `--data-dir`：参数化在 import 期就要拿到用例，
那时 pytest 的命令行选项还没解析完。`scripts/run-headed.sh` 会自然透传环境变量。

### 数据质量单测

```bash
python -m pytest tools/tests/test_case_data.py -q
```

它守的东西：id 唯一且是 ASCII、必填字段、tags/name 非空、日期与晚数自洽、
`adults >= rooms`（页面上加房间会自动补住客，这条不满足时 `set_rooms` 直接拒绝）、
火车票的 offset 落在预售期内、没有手机号/身份证形状的字符串、版本号正确。

**数据写错应该在开浏览器之前就红**，而不是表现成「某条 UI 用例莫名失败」。
