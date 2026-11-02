/**
 * ctrip_login_smoke.mjs —— 携程 E2E：双登录分支 + 登录态持久化 + 三频道搜索冒烟（Node 版）
 *
 * 用法（本机不能安装微信，登录走手机微信扫码，默认统一用 --choice 2）：
 *   node ctrip_login_smoke.mjs --choice 2      # 推荐入口：微信扫码登录（手机扫，本机无需微信）
 *   node ctrip_login_smoke.mjs                 # 交互式：菜单二选一（明确推荐扫码）
 *   node ctrip_login_smoke.mjs --choice 1      # 快捷登录兼容路径（本机不可用，需二次确认，快速失败）
 *   node ctrip_login_smoke.mjs --dry-run       # 自检：只验证浏览器启动 + 登录态判定，不算登录验收
 *
 * 设计依据（全部来自本仓库 2026-11-01 实测取证，不是猜测）：
 *   · utils/auth.py            登录判据唯一事实源（顶部用户名容器最可靠，头像反而不存在）
 *   · scripts/setup_auth.py    登录流程实测：必须先勾协议；微信入口是 popup；
 *                              快捷登录面板需 15s 独占窗口；快捷登录必须系统 Chrome
 *   · pages/*.py               三频道已实测选择器（借用于本仓库 Python 框架）
 *   · pages/base_page.py       WAF / 风控特征（whaleguard、safe/sec/verify 域、滑块）
 *
 * 与 Python 框架（config/settings.py）的一条刻意差异：
 *   本工具用 launchPersistentContext 持久化 profile（用户明确要求，独立于 pytest
 *   框架的匿名实验条件），并在登录成功后另存 storageState 到 auth-state.json。
 */

import readline from 'node:readline/promises';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// ---------------------------------------------------------------- 路径与常量

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');                 // E:\WorkBoddy\train-e2e

const USER_DATA_DIR = path.join(ROOT, 'user-data');         // 持久化 profile（本次新增）
const STATE_FILE = path.join(ROOT, 'auth-state.json');      // storageState（需求指定）
const PY_STATE_FILE = path.join(ROOT, 'config', 'auth', 'ctrip_state.json'); // Python 框架共用（存在才写）
const QR_PNG = path.join(ROOT, 'qr-code.png');              // 二维码截图（需求指定）
const REPORT_DIR = path.join(ROOT, 'reports', 'node-smoke');

const URLS = {
  www: 'https://www.ctrip.com/',
  flightHome: 'https://flights.ctrip.com/online/channel/domestic',
  trainHome: 'https://trains.ctrip.com/',
};

const LOGIN_WALL_HOST = 'passport.ctrip.com';
const WECHAT_AUTH_HOST = 'open.weixin.qq.com';

// 登录等待参数（需求指定：2s 轮询 / 300s 上限 / 120s 刷码）
const POLL_INTERVAL_MS = 2000;
const QR_TOTAL_TIMEOUT_MS = 300_000;
const QR_REFRESH_AFTER_MS = 120_000;
const QUICK_PANEL_WINDOW_MS = 15_000;   // 快捷面板独占窗口（实测：等短了会误判"只有二维码"）
const QUICK_LOGIN_TIMEOUT_MS = 180_000;

// 浏览器启动参数：与 Python 框架 BROWSER_UI_ARGS 同源（关浏览器自带浮层，非反检测），
// 另按本次需求追加 --disable-blink-features=AutomationControlled。
const BROWSER_ARGS = [
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-save-password-bubble',
  '--disable-features=Translate,TranslateUI',
  '--lang=zh-CN',
  '--start-maximized',
  '--disable-blink-features=AutomationControlled',  // 本次需求明确要求，与主框架红线不同，见文件头注释
];

// ---------------------------------------------------------------- 登录判据（移植 utils/auth.py）

const LOGGED_OUT_TEXTS = ['注册', '登录'];
const LOGIN_ENTRY_WORDS = ['登录', '注册', '立即登录', '请登录'];
const USER_NAME_CANDIDATES = [
  "span[class*='header_login_title_name']",
  "[class*='header_login_title_name']",
];
const AVATAR_CANDIDATES = [
  "img[class*='avatar']",
  "[class*='avatar'] img",
  "[class*='user-info'] img",
  "[class*='head-img']",
  "[class*='userCenter']",
];
const PAGE_READY_TEXT = '我的订单';

/** 判定当前页登录态。返回 { logged, evidence } —— 证据原样返回，判错时能一眼看出是哪条。 */
async function isLoggedIn(page) {
  try {
    for (const text of LOGGED_OUT_TEXTS) {
      const loc = page.getByText(text, { exact: true }).first();
      if (await loc.isVisible().catch(() => false)) {
        return { logged: false, evidence: `顶部「${text}」仍可见 ⇒ 未登录` };
      }
    }
  } catch { /* 页面导航中，继续往下判 */ }

  // 用户名容器（实测最可靠的正证；登录态头部没有头像 img，只有纯文本用户名）
  for (const sel of USER_NAME_CANDIDATES) {
    try {
      const loc = page.locator(sel).first();
      if (!(await loc.isVisible().catch(() => false))) continue;
      const text = (await loc.innerText({ timeout: 3000 }).catch(() => '')) || '';
      const t = text.trim();
      if (!t || t.length < 2 || t.length > 20) continue;
      if (LOGIN_ENTRY_WORDS.some((w) => t.includes(w))) continue;
      return { logged: true, evidence: `顶部用户名容器可见：'${t}'` };
    } catch { continue; }
  }

  for (const sel of AVATAR_CANDIDATES) {
    try {
      if (await page.locator(sel).first().isVisible().catch(() => false)) {
        return { logged: true, evidence: `头像区可见：${sel}` };
      }
    } catch { continue; }
  }

  try {
    if ((await page.getByText('退出', { exact: false }).count()) > 0) {
      return { logged: true, evidence: '页面出现「退出」' };
    }
  } catch { /* ignore */ }

  // 兜底：登录入口全消失 + 页面确实加载出来了（排除空白页/风控页误判）
  try {
    if ((await page.getByText(PAGE_READY_TEXT, { exact: false }).count()) > 0) {
      return { logged: true, evidence: '「登录/注册」均不可见且「我的订单」在场 ⇒ 已登录' };
    }
  } catch { /* ignore */ }

  return { logged: false, evidence: '未命中任何登录判据' };
}

/** 等待 header 水合后再判（SPA 首页 domcontentloaded 后才把「登录/注册」换成用户名）。 */
async function waitLoggedInVerdict(page, timeoutMs = 10000) {
  const budget = Math.max(1, Math.floor(timeoutMs / USER_NAME_CANDIDATES.length));
  for (const sel of USER_NAME_CANDIDATES) {
    try {
      await page.locator(sel).first().waitFor({ state: 'visible', timeout: budget });
      break;
    } catch { continue; }
  }
  return isLoggedIn(page);
}

// ---------------------------------------------------------------- 风控拦截判定（移植 pages/base_page.py）

const BLOCK_TEXT_MARKERS = [
  '访问过于频繁', '请求过于频繁', '操作过于频繁', '请稍后再试',
  '安全验证', '人机验证', '拖动滑块', '滑动验证', '请完成验证', '异常访问',
];
const BLOCK_HOSTS = ['safe.ctrip.com', 'sec.ctrip.com', 'verify.ctrip.com'];

/** 被拦返回 { blocked, kind, detail }；正常返回 null。被拦是一等结果，如实报告，不重试绕过。 */
async function detectBlock(page) {
  try {
    const url = page.url() || '';
    for (const host of BLOCK_HOSTS) {
      if (url.includes(host)) return { blocked: true, kind: 'waf_redirect', detail: `URL 落在 ${host}` };
    }
    const title = (await page.title().catch(() => '')) || '';
    if (/^\s*40[13]\b/.test(title) || /访问被拒绝|Access Denied/i.test(title)) {
      return { blocked: true, kind: 'http_error_page', detail: `title=${title.slice(0, 80)}` };
    }
    const body = (await page.locator('body').innerText({ timeout: 3000 }).catch(() => '')) || '';
    if (/\bwhaleguard\b/i.test(body)) return { blocked: true, kind: 'waf_block', detail: '正文命中 whaleguard' };
    for (const marker of BLOCK_TEXT_MARKERS) {
      if (body.includes(marker)) return { blocked: true, kind: 'rate_limit_text', detail: `正文含「${marker}」` };
    }
    for (const sel of ["[class*='slide-verify']", "[class*='captcha']", "iframe[src*='captcha']"]) {
      if (await page.locator(sel).first().isVisible().catch(() => false)) {
        return { blocked: true, kind: 'captcha', detail: sel };
      }
    }
  } catch { /* ignore */ }
  return null;
}

// ---------------------------------------------------------------- 营销浮层（移植 utils/overlay.py）

const PROMO_ANCHORS = ['周周享低价', '领券更划算'];
const CLOSE_SELECTORS = ['[aria-label="关闭"]', '[title="关闭"]', '[class*="close"]', '[class*="Close"]'];

/** 尽力关掉底部领券浮层（它既挡点击，又会让"二维码可见"类断言假绿）。失败静默。 */
async function dismissPromoOverlays(page, tag = '') {
  const done = [];
  for (const anchor of PROMO_ANCHORS) {
    try {
      const loc = page.getByText(anchor, { exact: false });
      const count = await loc.count();
      let node = null;
      for (let i = 0; i < Math.min(count, 3); i++) {
        if (await loc.nth(i).isVisible().catch(() => false)) { node = loc.nth(i); break; }
      }
      if (!node) continue;
      let cursor = node;
      for (let hop = 0; hop < 6 && !done.length; hop++) {
        cursor = cursor.locator('xpath=..');
        if ((await cursor.count()) === 0) break;
        for (const sel of CLOSE_SELECTORS) {
          const btn = cursor.locator(sel).first();
          if ((await btn.count()) > 0 && (await btn.isVisible().catch(() => false))) {
            await btn.click({ timeout: 2000 }).catch(() => {});
            done.push(`${tag}${anchor}: 已点 ${sel}`);
            break;
          }
        }
      }
    } catch { continue; }
  }
  return done;
}

// ---------------------------------------------------------------- 小工具

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jitter = (min, max) => min + Math.floor(Math.random() * (max - min));
const ts = () => new Date().toLocaleTimeString('zh-CN', { hour12: false });

/** 有界等待：在候选选择器里等第一个可见的。全超时返回 null。 */
async function firstVisible(page, selectors, timeoutMs, scope = null) {
  const root = scope || page;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const sel of selectors) {
      const loc = root.locator(sel).first();
      if ((await loc.count().catch(() => 0)) > 0 && (await loc.isVisible().catch(() => false))) {
        const box = await loc.boundingBox().catch(() => null);
        if (box && box.width >= 40 && box.height >= 20) return { sel, loc };
      }
    }
    await sleep(500);
  }
  return null;
}

/** 当前 context 里是否已出现登录凭证 cookie（信号 B）。 */
async function hasTicketCookie(context) {
  try {
    const cookies = await context.cookies();
    return cookies.some((c) => c.name === 'cticket' || c.name === 'login_type');
  } catch { return false; }
}

// ---------------------------------------------------------------- 登录入口通用步骤

/** 打开 www 首页并点「登录」，落到 passport。已登录则返回 null（调用方直接跳过登录）。 */
async function openLoginPage(context, page, log) {
  await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await dismissPromoOverlays(page, '[首页浮层]');
  const verdict = await waitLoggedInVerdict(page);
  log(`  登录态判定：${verdict.evidence}`);
  if (verdict.logged) return { alreadyLoggedIn: true };

  const entry = page.getByText('登录', { exact: true }).first();
  if (!(await entry.isVisible().catch(() => false))) {
    throw new Error('首页找不到可点的「登录」入口');
  }
  await entry.click({ timeout: 10000 });
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});

  // 硬等待 passport 表单水合完（2026-11-02 实测根因）：点完「登录」时 domcontentloaded
  // 已返回，但表单是客户端渲染 —— 立刻去找协议框/微信入口必然落空，报"站点可能改版"。
  // 页面已实测有这些锚点：input#checkboxAgreementInput、a.icon-new-wechat、.login-code
  const formReady = await page.locator('a.icon-new-wechat').first()
    .waitFor({ state: 'visible', timeout: 20000 })
    .then(() => true).catch(() => false);
  if (!formReady) {
    await page.locator('input#checkboxAgreementInput').first()
      .waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
  }
  log(`  已点击「登录」，等表单就绪=${formReady ? '是' : '否（继续尝试）'}；URL：${page.url().slice(0, 100)}`);
  return { alreadyLoggedIn: false };
}

/**
 * 勾选协议（实测硬前置：不勾就点微信只弹「请先阅读并勾选协议」气泡）。
 * checkbox 被 label 覆盖，常规 check 会等可交互性到超时 —— 必须 force + 回读。
 */
async function checkAgreement(page, log) {
  const box = page.locator('input#checkboxAgreementInput');
  // 先等框出现（同 openLoginPage 的时序根因：表单是客户端渲染）
  await box.first().waitFor({ state: 'attached', timeout: 10000 }).catch(() => {});
  if ((await box.count()) === 0) {
    log('  ⚠️ 未找到协议勾选框（站点可能改版），继续但点微信可能无效');
    return false;
  }
  try { await box.check({ force: true, timeout: 4000 }); } catch {
    try { await box.click({ force: true, timeout: 4000 }); } catch { /* ignore */ }
  }
  const checked = await box.isChecked().catch(() => false);
  log(checked ? '  ✓ 协议已勾选（回读确认）' : '  ✗ 协议勾选失败（is_checked 仍为 False）');
  return checked;
}

/**
 * 点微信入口，返回授权页（popup 或原页）。
 * 实测：点 a.icon-new-wechat 必然开 popup 到 open.weixin.qq.com/connect/qrconnect。
 * 兼容两种形态：popup（实测主流）/ 同页 iframe（需求截图形态，用 frameLocator 处理）。
 */
async function openWechatAuthPage(context, page, log) {
  const entryCandidates = ['a.icon-new-wechat', "[class*='icon-new-wechat']", 'text=微信'];
  for (const sel of entryCandidates) {
    const entry = page.locator(sel).first();
    // 等入口渲染（最多 15s），不要因为 DOM 还没水合就判定"入口不存在"
    const appeared = await entry.waitFor({ state: 'visible', timeout: sel === entryCandidates[0] ? 15000 : 3000 })
      .then(() => true).catch(() => false);
    if (!appeared) continue;

    log(`  尝试微信入口：${sel}`);
    const popupPromise = context.waitForEvent('page', { timeout: 12000 }).catch(() => null);
    await entry.click({ timeout: 8000 }).catch(() => {});

    let authPage = null;
    try {
      authPage = await popupPromise;
    } catch { /* ignore */ }

    if (authPage && !authPage.isClosed()) {
      await authPage.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
      log(`  ✓ 捕获 popup：${authPage.url().slice(0, 110)}`);
      return { authPage, isPopup: true };
    }

    // 没开 popup：可能是同页 iframe 形态（二维码弹窗），确认页面仍在 passport 即可
    log('  未捕获 popup，按同页 iframe 形态继续（需求截图形态）');
    return { authPage: page, isPopup: false };
  }
  throw new Error('微信入口候选全部未命中');
}

/** 在授权页/iframe 里定位二维码元素。 */
function qrLocators(authPage) {
  return {
    pageLevel: ['img.js_qrcode_img', 'img.web_qrcode_img', "img[src*='/connect/qrcode']"],
    frameLevel: ["img.qr_code", 'canvas', "img[src*='qrcode']"],   // frameLocator 内（需求截图形态）
  };
}

async function findQrElement(authPage, log) {
  const { pageLevel, frameLevel } = qrLocators(authPage);
  // 先找主文档（popup 形态，实测主流）
  const hit = await firstVisible(authPage, pageLevel, 5000);
  if (hit) { log(`  ✓ 二维码在主文档：${hit.sel}`); return hit.loc; }
  // 再找 iframe（qrconnect 形态；frameLocator 处理跨域，禁用已废弃的 page.frame()）
  try {
    const wxFrame = authPage.frameLocator('iframe[src*="qrconnect"]').first();
    const fHit = await firstVisible(authPage, frameLevel, 5000, wxFrame);
    if (fHit) { log(`  ✓ 二维码在 qrconnect iframe：${fHit.sel}`); return fHit.loc; }
  } catch { /* ignore */ }
  return null;
}

/** 截二维码到本地（可见后再留 500ms 余量，规避 canvas 像素晚于 DOM 绘制）。 */
async function screenshotQr(qrLoc, log) {
  await qrLoc.waitFor({ state: 'visible', timeout: 10000 });
  await sleep(500); // 保底，不作主要等待手段
  await qrLoc.screenshot({ path: QR_PNG, timeout: 10000 });
  log(`  📷 二维码已保存：${QR_PNG}`);
  log('  → 请打开该图片，用手机微信扫码并确认登录');
}

/** 二维码是否已失效（微信页会显示失效文案）。 */
async function qrExpired(authPage) {
  try {
    // 只认失效文案；「刷新」链接常驻可见，不能当过期信号（否则每轮都白刷）
    const loc = authPage.getByText(/二维码已失效|已失效|已过期|重新扫码/).first();
    return await loc.isVisible().catch(() => false);
  } catch { return false; }
}

/** 轮询登录成功（组合信号：URL 离开 passport / cticket cookie / 首页用户名容器）。 */
async function waitForLoginSuccess(context, page, authPage, isPopup, timeoutMs, log, refreshCb = null) {
  const deadline = Date.now() + timeoutMs;
  let recheckAfterPopupClosed = false;
  let lastEvidence = '';
  let waitedSec = 0;

  while (Date.now() < deadline) {
    // 信号 0：popup 关闭（手机确认完成最硬的信号）—— 回 www 复核
    if (isPopup && authPage.isClosed() && !recheckAfterPopupClosed) {
      recheckAfterPopupClosed = true;
      log('  …授权页已关闭（手机侧确认完成），回 www 复核登录态');
      await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      await dismissPromoOverlays(page, '[复核浮层]');
    }

    // 信号 1：URL 离开 passport 登录页
    const url = page.url() || '';
    const urlLeft = !url.includes(`${LOGIN_WALL_HOST}/user/login`);

    // 信号 2：关键登录 cookie 出现
    const cookieOk = await hasTicketCookie(context);

    // 信号 3：页面登录态元素（在 www 上才判）
    let verdict = null;
    if (urlLeft && !url.includes(LOGIN_WALL_HOST)) {
      verdict = await isLoggedIn(page);
    }

    if ((verdict && verdict.logged) || (cookieOk && urlLeft) || (cookieOk && verdict && verdict.logged)) {
      const evidence = verdict?.logged ? verdict.evidence
        : `URL 已离开登录页 + 登录 cookie 在场（url=${url.slice(0, 80)}）`;
      return { success: true, evidence };
    }
    if (verdict) lastEvidence = verdict.evidence;
    else if (cookieOk) lastEvidence = '登录 cookie 已出现，URL 尚在 passport';
    else lastEvidence = urlLeft ? 'URL 已离开登录页，未见登录判据' : '等待中…';

    // 二维码过期 → 刷新重截
    if (refreshCb && (await qrExpired(authPage).catch(() => false))) {
      log('  ⚠️ 检测到二维码已失效，刷新中…');
      await refreshCb();
    } else if (refreshCb && waitedSec > 0 && waitedSec % (QR_REFRESH_AFTER_MS / 1000) === 0) {
      log(`  ⚠️ 已等 ${waitedSec}s 未成功，主动刷新二维码（防扫到过期码）`);
      await refreshCb();
    }

    if (waitedSec > 0 && waitedSec % 10 === 0) {
      log(`  …等待扫码中（${waitedSec}s / ${Math.round(timeoutMs / 1000)}s）：${lastEvidence}`);
    }
    await sleep(POLL_INTERVAL_MS);
    waitedSec += POLL_INTERVAL_MS / 1000;
  }
  return { success: false, evidence: `超时，最后一次判定：${lastEvidence}` };
}

// ---------------------------------------------------------------- 分支 1：微信快捷登录

async function quickLogin(context, page, browserChannel, log, askSwitchToQr) {
  log('\n【分支 1：微信快捷登录】');
  if (browserChannel !== 'chrome') {
    log(`  ⚠️ 当前浏览器是 ${browserChannel}，不是系统 Chrome：实测 Chromium 里微信回环探测`);
    log('     被切（localhost.weixin.qq.com:14013 → ERR_CONNECTION_CLOSED），快捷登录面板可能不出现；');
    log('     若 15s 内没等到面板，会按需求回退并提示切换扫码模式');
  }

  const entered = await openLoginPage(context, page, log);
  if (entered.alreadyLoggedIn) return { success: true, reused: true };
  await checkAgreement(page, log);

  const { authPage, isPopup } = await openWechatAuthPage(context, page, log);

  // 15s 独占窗口等快捷面板（实测：面板要等本机微信探测回调，二维码先渲染，先给面板机会）
  log(`  等待「微信快捷登录」面板（${QUICK_PANEL_WINDOW_MS / 1000}s 独占窗口）…`);
  const panel = await firstVisible(authPage, ['.js_quick_login'], QUICK_PANEL_WINDOW_MS);
  if (!panel) {
    log('  ✗ 快捷登录面板未出现（本机微信未被探测到或客户端未运行）');
    return askSwitchToQr('快捷登录面板未出现');
  }

  const btn = authPage.locator('.js_quick_login_btn').first();
  try {
    await btn.click({ timeout: 8000 });
  } catch (e) {
    log(`  ✗ 点击「微信快捷登录」失败：${e.message.split('\n')[0]}`);
    return askSwitchToQr('快捷按钮点击失败');
  }
  log('  ✓ 已点击「微信快捷登录」');
  log('  → 请在微信客户端弹出的原生授权框上点【允许】（该框在浏览器外，脚本点不到）');

  const result = await waitForLoginSuccess(context, page, authPage, isPopup, QUICK_LOGIN_TIMEOUT_MS, log);
  if (result.success) return { success: true, evidence: result.evidence };

  log(`  ✗ 快捷登录未成功：${result.evidence}`);
  return askSwitchToQr('快捷登录超时或调起失败');
}

/** 快捷登录失败后的回退：提示并询问是否切到扫码模式（需求要求）。 */
async function askSwitchToQr(reason, context, page, browserChannel, log) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const ans = await rl.question(`\n[${reason}] 是否切换到微信扫码登录？(y/n): `);
  rl.close();
  if (ans.trim().toLowerCase() !== 'y') return { success: false, evidence: reason };
  return qrLogin(context, page, browserChannel, log, false);
}

// ---------------------------------------------------------------- 分支 2：微信扫码登录

async function qrLogin(context, page, browserChannel, log, withBanner = true) {
  if (withBanner) log('\n【分支 2：微信扫码登录】');

  const entered = await openLoginPage(context, page, log);
  if (entered.alreadyLoggedIn) return { success: true, reused: true };
  await checkAgreement(page, log);

  const { authPage, isPopup } = await openWechatAuthPage(context, page, log);

  let qrLoc = await findQrElement(authPage, log);
  let enterCount = 0;

  // 刷新回调：优先点页面上的刷新，否则重开微信入口（重进流程）
  const refreshCb = async () => {
    try {
      const refreshBtn = authPage.getByText(/刷新|重新加载/).first();
      if ((await refreshBtn.count()) > 0 && (await refreshBtn.isVisible().catch(() => false))) {
        await refreshBtn.click({ timeout: 5000 });
        await sleep(1500);
      } else if (isPopup) {
        await authPage.reload({ waitUntil: 'domcontentloaded' });
      } else {
        enterCount += 1;
        if (enterCount > 3) throw new Error('刷新二维码次数超限');
        await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 });
        await checkAgreement(page, log);
        const again = await openWechatAuthPage(context, page, log);
        qrLoc = await findQrElement(again.authPage, log);
        if (!qrLoc) throw new Error('刷新后未重新找到二维码');
      }
      if (qrLoc) await screenshotQr(qrLoc, log);
    } catch (e) {
      log(`  ⚠️ 刷新二维码失败：${e.message.split('\n')[0]}`);
    }
  };

  if (!qrLoc) {
    log('  ✗ 未定位到二维码（主文档与 qrconnect iframe 都没等到）');
    log(`     现场排查：若页面有多个 iframe，脚本已按 src*="qrconnect" + .first() 消歧；`);
    log(`     若仍失败，把 ${REPORT_DIR} 下的截图发回来定位`);
    await authPage.screenshot({ path: path.join(REPORT_DIR, 'qr-not-found.png'), fullPage: true }).catch(() => {});
    return { success: false, evidence: '未定位到二维码元素' };
  }
  await screenshotQr(qrLoc, log);

  const result = await waitForLoginSuccess(context, page, authPage, isPopup, QR_TOTAL_TIMEOUT_MS, log, refreshCb);
  return result.success
    ? { success: true, evidence: result.evidence }
    : { success: false, evidence: result.evidence };
}

// ---------------------------------------------------------------- 登录态落盘

async function persistState(context, log) {
  await context.storageState({ path: STATE_FILE });
  log(`  ✓ storageState 已写入：${STATE_FILE}`);
  // 与 Python 框架共用同一份登录态（该目录存在才写；config/auth/ 已 gitignore）
  if (fs.existsSync(path.dirname(PY_STATE_FILE))) {
    await context.storageState({ path: PY_STATE_FILE });
    log(`  ✓ 同步写入 Python 框架登录态：${PY_STATE_FILE}`);
  } else {
    log('  （config/auth/ 不存在，跳过 Python 框架同步）');
  }
}

// ---------------------------------------------------------------- 三频道冒烟

async function smokeHotel(page, log) {
  const tag = '酒店';
  try {
    log(`  [${tag}] 打开 www 首页酒店模块`);
    await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await dismissPromoOverlays(page, `[${tag}]`);

    const dest = page.locator('#hotels-destination');
    await dest.waitFor({ state: 'visible', timeout: 20000 });
    await dest.click();
    await dest.fill('');
    await dest.pressSequentially('上海', { delay: 90 });  // 逐字符输入触发联想

    const suggest = await firstVisible(page, ["[class*='associative-item']"], 8000);
    if (suggest) {
      await suggest.loc.click();
      log(`  [${tag}] 已选联想项（${suggest.sel}）`);
    } else {
      log(`  [${tag}] ⚠️ 联想面板未出现，用当前输入值直接搜索`);
    }

    const searchBtn = await firstVisible(page, ["[class*='search-btn']"], 5000);
    if (!searchBtn) throw new Error('找不到搜索按钮 [class*=search-btn]');
    await searchBtn.loc.click();
    log(`  [${tag}] 已点击搜索，等待结果页…`);

    await page.waitForURL(/hotels\.ctrip\.com\/hotels\/(list|listPage)/, { timeout: 30000 })
      .catch(() => {});
    const resultSel = await firstVisible(
      page,
      ["[class*='hotel-item']", "[class*='list-card']", '.hotel-list', "[class*='sug-item']"],
      25000,
    );
    const blocked = await detectBlock(page);
    if (blocked) return { channel: tag, status: 'BLOCKED', reason: `${blocked.kind}: ${blocked.detail}`, url: page.url() };
    if (page.url().includes(LOGIN_WALL_HOST)) {
      return { channel: tag, status: 'FAIL', reason: '落点在 passport 登录墙', url: page.url() };
    }
    if (!resultSel && !/hotels\.ctrip\.com/.test(page.url())) {
      throw new Error(`未落到酒店列表页（当前 ${page.url().slice(0, 90)}）`);
    }
    return {
      channel: tag, status: 'PASS',
      reason: resultSel ? `结果元素 ${resultSel.sel} 可见` : '已落到酒店列表域（未等到具体卡片）',
      url: page.url().slice(0, 100),
    };
  } catch (e) {
    return failResult(page, '酒店', e);
  }
}

/**
 * 通用「确认按钮真的可点」：用 elementFromPoint 检查目标按钮中心是否被浮层挡住
 * （2026-11-02 实测：机票页日历开着盖住搜索按钮，且 div.calendar-modal 类名探测未命中，
 * 靠可见性判断会漏检）。被挡就点空白处关闭浮层并复核；空白坐标避开左侧导航列、
 * 单程/往返/日期等控件，选表单空白带。
 */
async function ensureClickable(page, selector, log, tag) {
  const blankSpots = [[800, 150], [500, 150], [450, 60]];
  for (let i = 0; i <= blankSpots.length; i++) {
    const st = await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      if (!el) return { missing: true };
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return { missing: true };
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      const top = document.elementFromPoint(cx, cy);
      const covered = !top || !(top === el || el.contains(top));
      return { covered, at: [Math.round(cx), Math.round(cy)] };
    }, selector);
    if (st.missing) return { ok: false, why: `${selector} 在页面上不存在` };
    if (!st.covered) return { ok: true };
    if (i === blankSpots.length) break;
    log(`  [${tag}] 搜索按钮被浮层遮挡（按钮中心 ${st.at}），点空白关闭（第 ${i + 1} 次）`);
    await page.mouse.click(...blankSpots[i]);
    await page.waitForTimeout(700);
  }
  return { ok: false, why: '多轮点空白后按钮仍被遮挡' };
}

async function smokeFlight(page, log) {
  const tag = '机票';
  try {
    log(`  [${tag}] 打开国内机票频道`);
    await page.goto(URLS.flightHome, { waitUntil: 'domcontentloaded', timeout: 45000 });

    const dep = page.locator("input[name='owDCity']").first();
    await dep.waitFor({ state: 'visible', timeout: 20000 });

    // 实测：必须点面板里的城市项，只 type 会出现「请选择出发城市」
    for (const [inputSel, city, panelWrap] of [
      ["input[name='owDCity']", '上海', "div[class*='cflt-poi']"],
      ["input[name='owACity']", '北京', "div[class*='cflt-poi']"],
    ]) {
      const box = page.locator(inputSel).first();
      await box.click();
      await box.fill('');
      await box.pressSequentially(city, { delay: 90 });
      // 城市行才有 span.code；机场行/邻近机场没有 —— 用它消歧（探针 33 取证）
      const item = page.locator(`${panelWrap} div[class*='address']:has(span[class*='code'])`).first();
      if (await item.isVisible().catch(() => false)) {
        await item.click({ timeout: 5000 });
      } else {
        const grid = page.locator(`li[title='${city}']`).first();  // 兜底：热门城市网格
        await grid.click({ timeout: 5000 }).catch(() => {});
      }
      log(`  [${tag}] 已选城市 ${city}（${inputSel}）`);
    }

    const btn = page.locator('button.search-btn').first();
    const clickable = await ensureClickable(page, 'button.search-btn', log, tag);
    if (!clickable.ok) throw new Error(`搜索按钮不可点：${clickable.why}`);
    await btn.click({ timeout: 8000 });
    log(`  [${tag}] 已点击搜索，等待航班列表…`);

    await page.waitForURL(/online\/list\//, { timeout: 30000 }).catch(() => {});
    // 结果验收走语义信号：优先航班卡片类名；新版往返页类名已变（2026-11-02 实测，
    // flight-item 不复存在），改等真实航班时间文本（如 18:15）——这是列表真正渲染的标志，
    // 骨架屏的 fake-* 元素不含独立时间文本（pages/flight_search_page.py 坑记录）。
    let evidence = null;
    const card = await firstVisible(page, ["[class*='flight-item']"], 12000);
    if (card) evidence = `结果卡片 ${card.sel} 可见`;
    else {
      const okTime = await page.getByText(/^\d{1,2}:\d{2}$/).first()
        .waitFor({ state: 'visible', timeout: 25000 }).then(() => true).catch(() => false);
      if (okTime) evidence = '航班时间文本可见（语义信号，新版列表结构）';
    }
    const blocked = await detectBlock(page);
    if (blocked) return { channel: tag, status: 'BLOCKED', reason: `${blocked.kind}: ${blocked.detail}`, url: page.url() };
    if (!evidence) throw new Error(`未等到航班结果（当前 ${page.url().slice(0, 90)}）`);
    return { channel: tag, status: 'PASS', reason: evidence, url: page.url().slice(0, 100) };
  } catch (e) {
    return failResult(page, '机票', e);
  }
}

async function smokeTrain(page, log) {
  const tag = '火车票';
  try {
    log(`  [${tag}] 打开火车票频道`);
    await page.goto(URLS.trainHome, { waitUntil: 'domcontentloaded', timeout: 45000 });

    // 城市面板结构已改版（2026-11-02 实测）：ul.widget-search-bd 不复存在，
    // 新面板是 ul.widget-city-bd（热门城市 li）+ ul.widget-city-hd（字母索引）。
    // 两个陷阱：① 底部 ul.index-show-city（热门路线）也有同名城市文本，绝不能点（K-11 同源）；
    // ② 选完城市必须确认面板/遮罩已关，否则盖住搜索按钮 → 点击超时（本次实测踩到）。
    const fields = [
      ['#label-departStation', '上海'],
      ['#label-arriveStation', '北京'],
    ];
    for (const [inputSel, city] of fields) {
      const box = page.locator(inputSel).first();
      await box.waitFor({ state: 'visible', timeout: 20000 });
      await box.click({ force: true });
      // 新面板优先，旧结构兜底（站点可能灰度回切）；按完整文本精确匹配防选错站
      const item = page.locator('ul.widget-city-bd li')
        .filter({ hasText: new RegExp(`^\\s*${city}\\s*$`) }).first();
      const itemLegacy = page.locator('div[class*="station-picker-wrap"] ul.widget-search-bd li div.cont')
        .filter({ hasText: new RegExp(`^\\s*${city}\\s*$`) }).first();
      let picked = false;
      for (let round = 0; round < 3 && !picked; round++) {
        for (const cand of [item, itemLegacy]) {
          try {
            await cand.waitFor({ state: 'visible', timeout: 4000 });
            await cand.click({ timeout: 4000 });
            picked = true;
            break;
          } catch { continue; }
        }
        // 失败重试禁止再 force 点击（会把面板重新点开、遮罩盖住搜索按钮——本次实测教训），
        // 用 focus() 恢复焦点唤起面板，不产生点击副作用
        if (!picked) await box.focus().catch(() => {});
      }
      if (!picked) log(`  [${tag}] ⚠️ ${city} 面板点选未确认，保留默认站点继续`);
      else log(`  [${tag}] 已选城市 ${city}（${inputSel}）`);
      // 面板/遮罩不关会盖住搜索按钮 → 超时（实测根因），点空白关闭
      const mask = page.locator('div.widget-city-mask, ul.widget-city-bd').first();
      if (await mask.isVisible().catch(() => false)) {
        await page.mouse.click(30, 700);
        await mask.waitFor({ state: 'hidden', timeout: 3000 }).catch(() => {});
      }
    }

    const btn = page.getByRole('button', { name: '搜索' }).first();
    await btn.click({ timeout: 8000 });
    log(`  [${tag}] 已点击搜索，等待车次列表…`);

    const row = await firstVisible(page, ['.list-item'], 35000);
    const blocked = await detectBlock(page);
    if (blocked) return { channel: tag, status: 'BLOCKED', reason: `${blocked.kind}: ${blocked.detail}`, url: page.url() };
    if (!row) throw new Error(`未等到车次列表（当前 ${page.url().slice(0, 90)}）`);
    return { channel: tag, status: 'PASS', reason: `车次行 .list-item 可见`, url: page.url().slice(0, 100) };
  } catch (e) {
    return failResult(page, '火车票', e);
  }
}

/** 冒烟失败证据：完整报错（含 locator 与最后的 waiting 状态行）+ 视口截图 + HTML 快照。 */
async function failResult(page, tag, e) {
  const lines = e.message.split('\n').filter((l) => l.trim());
  const reason = lines.slice(0, 2).join(' | ');
  const waiting = lines.filter((l) => /waiting|received|obscures|intercepts/i.test(l)).slice(-3);
  const shot = path.join(REPORT_DIR, `${tag}-fail.png`);
  const html = path.join(REPORT_DIR, `${tag}-fail.html`);
  await page.screenshot?.({ path: shot, fullPage: false }).catch(() => {});
  await page.content?.().then((c) => fs.writeFileSync(html, c)).catch(() => {});
  return {
    channel: tag, status: 'FAIL',
    reason: `${reason}${waiting.length ? ` ｜ 最后等待状态：${waiting.join(' ; ')}` : ''}（截图：${shot}；HTML：${html}）`,
    url: page.url?.() || '',
  };
}

// ---------------------------------------------------------------- 浏览器启动

/**
 * 启动持久化有头浏览器。优先系统 Chrome（快捷登录硬依赖，实测），
 * 失败退回 Playwright Chromium（只能扫码）。
 * 不主动 taskkill chrome.exe —— 那会连带杀掉用户自己开着的浏览器；
 * 只提示用户关闭本 profile 的残留实例。
 */
async function launchContext(log) {
  // 浏览器二进制兜底：本机 Python 端 playwright 已装好 chromium-1248（C 盘缓存），
  // 只读复用即可，避免往 C 盘重新下载。系统 Chrome/Edge 路径不受此影响。
  if (!process.env.PLAYWRIGHT_BROWSERS_PATH) {
    const bundled = chromium.executablePath();
    if (!fs.existsSync(bundled)) {
      const cache = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright');
      if (fs.existsSync(path.join(cache, 'chromium-1248'))) {
        process.env.PLAYWRIGHT_BROWSERS_PATH = cache;
        log(`  未找到 Playwright 自带浏览器，改用本机缓存（只读）：${cache}`);
      }
    }
  }
  const options = {
    headless: false,                       // 扫码登录必须无头=false（需求 + 实测）
    // 强制最大化（用户 2026-11-02 要求）：viewport 必须缺省，否则 Playwright 会用
    // 固定视口覆盖窗口内容区 —— 窗口最大、页面仍按固定宽度排版（settings.py 同结论）。
    viewport: null,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    args: BROWSER_ARGS,                    // 含 --start-maximized
  };

  for (const channel of ['chrome', 'msedge', undefined]) {
    try {
      const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
        ...options,
        ...(channel ? { channel } : {}),
      });
      // 实测硬前置：微信页面靠 local-network-access 权限探测本机客户端，缺了永远只给二维码
      try { await context.grantPermissions(['local-network-access']); } catch { /* ignore */ }
      const label = channel === 'chrome' ? '系统 Chrome'
        : channel === 'msedge' ? '系统 Edge（⚠️ 快捷登录未实测，面板若不出现会自动走扫码）'
        : 'Playwright Chromium（实测拿不到快捷登录，只能扫码）';
      log(`  浏览器：${label}`);
      log(`  持久化 profile：${USER_DATA_DIR}`);
      return { context, channel: channel || 'chromium' };
    } catch (e) {
      const msg = e.message || '';
      if (channel) {
        log(`  ⚠️ ${channel} 启动失败（${msg.split('\n')[0]}），尝试下一个浏览器`);
        continue;
      }
      if (/ProcessSingleton|in use|already running|Failed to create/i.test(msg)) {
        throw new Error(
          `持久化 profile 被占用（上次脚本异常退出后 Chrome 进程未退净）。\n` +
          `  请先关闭残留的 Chrome 窗口，或只结束使用 ${USER_DATA_DIR} 的进程后重试。`,
        );
      }
      throw e;
    }
  }
}

// ---------------------------------------------------------------- 主流程

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const choiceArgIdx = args.indexOf('--choice');
  const choiceArg = choiceArgIdx >= 0 ? args[choiceArgIdx + 1] : null;

  const log = (...m) => console.log(`[${ts()}]`, ...m);
  fs.mkdirSync(REPORT_DIR, { recursive: true });

  log('==============================================');
  log('携程 E2E：登录（快捷/扫码）+ 三频道搜索冒烟');
  log('==============================================');

  const { context, channel } = await launchContext(log);
  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(20000);

  let exitCode = 0;
  let login = null;

  try {
    if (dryRun) {
      log('\n[dry-run] 自检：打开首页 + 登录态判定（不登录、不冒烟）');
      await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await dismissPromoOverlays(page, '[dry-run]');
      const v = await waitLoggedInVerdict(page);
      log(`  登录态判定：${v.evidence}`);
      const blocked = await detectBlock(page);
      if (blocked) log(`  ⚠️ 风控特征：${blocked.kind} ${blocked.detail}`);
      log('[dry-run] 自检通过');
      return;
    }

    // ---- 登录方式选择 ----
    let choice = choiceArg;
    if (!login) {
      // 先探一次登录态：持久化 profile 还有会话就跳过菜单，不重复扫码
      await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      await dismissPromoOverlays(page, '[首页浮层]');
      const v = await waitLoggedInVerdict(page);
      if (v.logged) {
        log(`  持久化登录态仍有效：${v.evidence} —— 跳过登录，直接冒烟`);
        login = { success: true, reused: true };
      } else if (choice) {
        log(`  当前未登录（${v.evidence}），按 --choice ${choice} 进入对应分支`);
      } else {
        const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
        console.log('\n请选择登录方式：');
        console.log('  1 = 微信快捷登录（需本机安装微信客户端，当前机器不可用，仅保留兼容）');
        console.log('  2 = 微信扫码登录（推荐：手机微信扫码，无需本机微信）');
        choice = await rl.question('输入 1 或 2: ');
        rl.close();
      }
    }

    if (!login) {
      if (String(choice).trim() === '2') {
        login = await qrLogin(context, page, channel, log);
      } else {
        // 本机无微信客户端（用户约束 2026-11-02）：快捷登录入口加二次确认，快速失败不空等
        const rl2 = readline.createInterface({ input: process.stdin, output: process.stdout });
        const go = await rl2.question(
          '本机未安装微信，快捷登录大概率不可用，是否仍尝试？(y = 仍尝试 / 其他输入 = 改用 2 扫码): ',
        );
        rl2.close();
        if (go.trim().toLowerCase() !== 'y') {
          log('  已按提示切换到扫码登录');
          login = await qrLogin(context, page, channel, log, false);
        } else {
          // quickLogin 内部：15s 拿不到快捷面板 → 立即回退询问是否切扫码（不空等）
          login = await quickLogin(context, page, channel, log, (reason) =>
            askSwitchToQr(reason, context, page, channel, log));
        }
      }
      if (!login.success) {
        log(`\n✗ 登录失败：${login.evidence}`);
        log('  未写入任何登录态文件（不落半成品凭据）。');
        exitCode = 1;
        return;
      }
      log(`\n✓ 登录成功：${login.evidence || '复用已有会话'}`);
      await persistState(context, log);
    }

    // ---- 冒烟前置：登录态复核 ----
    log('\n[冒烟前置] 复核登录态');
    await page.goto(URLS.www, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await dismissPromoOverlays(page, '[前置浮层]');
    const pre = await waitLoggedInVerdict(page);
    log(`  ${pre.evidence}`);
    if (!pre.logged) {
      log('\n✗ 登录态已过期，请重新运行本脚本扫码（auth-state.json 可能只是存了，不代表仍有效）');
      exitCode = 3;
      return;
    }

    // ---- 三频道冒烟（频道间加随机人类化延迟，非固定 sleep）----
    const results = [];
    log('\n[冒烟] 酒店 → 机票 → 火车票');
    results.push(await smokeHotel(page, log));
    await sleep(jitter(800, 2000));
    results.push(await smokeFlight(page, log));
    await sleep(jitter(800, 2000));
    results.push(await smokeTrain(page, log));

    // ---- 报告 ----
    log('\n================ 测试报告 ================');
    for (const r of results) {
      const mark = r.status === 'PASS' ? '✓ PASS' : r.status === 'BLOCKED' ? '⚠ BLOCKED' : '✗ FAIL';
      log(`  ${mark}  ${r.channel}  ${r.reason}`);
      log(`         url: ${r.url}`);
    }
    const failed = results.filter((r) => r.status !== 'PASS');
    if (failed.length) {
      log(`\n未通过 ${failed.length} 个频道。BLOCKED=风控拦截（一等结果，不重试绕过）；FAIL=功能未通。`);
      exitCode = 2;
    } else {
      log('\n✓ 三个频道全部通过');
    }
  } finally {
    await context.close().catch(() => {});
  }
  process.exit(exitCode);
}

main().catch((e) => {
  console.error(`[fatal] ${e.message}`);
  process.exit(1);
});
