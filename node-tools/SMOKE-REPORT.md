# 携程登录 + 三频道冒烟验收报告（脱敏版）

- 执行时间：2026-11-02 12:32 ～ 12:41（GMT+8）
- 执行环境：Windows，Node.js v22，Playwright 1.64.1，系统 Edge 有头模式（最大化）
- 入口命令：`node ctrip_login_smoke.mjs --choice 2`
- 登录方式：手机微信扫码（本机未安装微信客户端，快捷登录仅保留兼容路径）

> 本报告为脱敏版，可入库。原始证据（含二维码与页面截图的 PNG、user-data 持久化 profile、
> auth-state.json、config/auth/ctrip_state.json）仅保留在开发机本地，**不入库**。

## 一、登录判定链

| 阶段 | 观测结果 |
|---|---|
| 登录入口 | 首页 `登录` → passport 登录页（客户端渲染） |
| 水合锚点 | 等待 `a.icon-new-wechat` 可见后才操作，避免误判改版 |
| 前置动作 | 勾选协议 `input#checkboxAgreementInput`（force 点击 + 回读 `is_checked`） |
| 二维码载体 | **popup 形态**（`open.weixin.qq.com/connect/qrconnect`），当前站点不走 iframe |
| 二维码元素 | `img.js_qrcode_img` 可见 → 截图本地 `qr-code.png` |
| 扫码结果 | 扫码后约 8 秒授权窗口自动关闭 |
| 成功判定 | 2s 轮询组合信号，命中 **URL 离开 passport + cticket cookie** 双信号 |
| 状态落盘 | `auth-state.json` 与 `config/auth/ctrip_state.json` 双写成功（cookies 计数一致） |
| 登录态复用 | 后续 3 次重跑均自动跳过菜单，未再次扫码 |

iframe 形态兼容性：当前站点未走 iframe，故用**本地夹具**（真实二维码图片 + `file://` 页面内的
`iframe[src*="qrconnect"]`）验证 `frameLocator` 分支可命中并成功截图，证明该分支非纸面兼容。

## 二、三频道冒烟结果

| 频道 | 状态 | 验收依据（语义信号） | 落地 URL |
|---|---|---|---|
| 酒店 | PASS | 结果列表容器 `.hotel-list` 可见 | `hotels.ctrip.com/hotels/list?city=2&checkin=...` |
| 机票 | PASS | 航班时间文本（`HH:mm` 形态）可见，不依赖卡片类名 | `flights.ctrip.com/online/list/round-sha-pek0?depdate=...` |
| 火车票 | PASS | 车次行 `.list-item` 可见 | `trains.ctrip.com/webapp/train/list?dStation=上海&aStation=北京` |

无 BLOCKED（未触发 whaleguard / 滑块等风控拦截）。

## 三、本次排查修复的四个根因

1. **passport 表单水合时序**：点「登录」后仅等 `domcontentloaded` 即查找协议框/微信入口，
   表单尚未渲染 → 误报「站点改版、入口未命中」。改为等待水合锚点。
2. **火车票城市面板改版**：旧结构 `div.*-station-picker-wrap > ul.widget-search-bd` 已不存在，
   新结构为 `ul.widget-city-bd`（字母索引 `ul.widget-city-hd`）；底部热门路线区存在同名城市文本，
   必须用容器作用域 + 精确文本匹配隔离。同时**移除失败路径中的 force 点击**（会把面板重新点开、
   遮罩复现）。
3. **机票日历遮挡搜索按钮**：`span.date-m` 覆盖 `button.search-btn` 中心点，Escape 关不掉。
   改为不依赖弹层类名的通用遮挡检测（`elementFromPoint` 命中目标按钮中心）+ 点表单空白复核，
   最多 4 轮。
4. **机票结果页类名变更**：往返结果页已无 `flight-item`，验收改为语义信号（真实航班时间文本），
   骨架屏不含独立时间文本，不会假绿。

## 四、运行方式

```bash
cd node-tools
node ctrip_login_smoke.mjs --choice 2   # 默认路径：微信扫码（本机唯一可用方式）
node ctrip_login_smoke.mjs --choice 1   # 兼容路径：微信快捷登录（需本机微信客户端）
node ctrip_login_smoke.mjs --dry-run    # 仅自检：启动浏览器 + 登录态判定，不算登录验收
```

登录态有效时脚本会先探测并自动跳过菜单，直接执行三频道冒烟。
