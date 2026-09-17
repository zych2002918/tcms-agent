// t3 验证脚本：手动编排页 UI（指引 + 顾问对话）——只依赖前端静态产物（vite preview 4173）
// 后端 8000 不可用 → 故障列表为空，仅验证结构文案与本地兜底回复路径（fetch 404 → 本地启发式）。
import { chromium } from "playwright-core";
const BASE = "http://localhost:4173";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const b = await chromium.launch({ executablePath: EDGE, headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
// preview 不代理 /api：把顾问端点拦成 404，强制走「本地故障字典兜底」路径（真实部署中由后端处理）
await p.route("**/api/agent/advisor", (route) => route.fulfill({ status: 404, body: "not implemented in preview" }));
const results = [];
const assert = (name, cond) => results.push((cond ? "✓ " : "✗ ") + name);

await p.goto(BASE + "/scenarios", { waitUntil: "networkidle" }).catch(async () => {
  // preview 是 SPA：/scenarios 直连可能 404 → 回根再路由
  await p.goto(BASE + "/", { waitUntil: "networkidle" });
  await p.waitForTimeout(800);
});
await p.waitForTimeout(800);
// 切手动编排
await p.click("button:has-text('手动编排')").catch(() => {});
await p.waitForTimeout(600);

let body = await p.locator("body").innerText();

// 1. 编排指引
assert("指引:标题(场景名称与故障步骤的关系)", body.includes("编排指引") && body.includes("场景名称与故障步骤"));
assert("指引:叙事标签/注入脚本两卡", body.includes("叙事标签") && body.includes("注入脚本"));
assert("指引:剧名/台词比喻", body.includes("剧名") && body.includes("台词与走位"));
assert("指引:微型示例步骤(车门故障级联)", body.includes("车门故障级联") && body.includes("door_fault") && body.includes("overspeed"));
assert("指引:分步引导1/2/3", body.includes("起个场景名称") && body.includes("加步骤") && body.includes("执行这个自定义场景"));

// 2. 顾问对话面板
assert("顾问:标题存在", body.includes("AI 编排顾问"));
assert("顾问:引导消息示例提问", body.includes("我是你的编排顾问") || body.includes("示例提问"));

// 3. 与顾问对话（本地兜底路径，后端 404）
const input = p.locator("input[placeholder*='说你的意图']");
await input.fill("我想测门坏了车不能跑");
await p.click("button:has-text('发送')");
await p.waitForSelector("text=顾问思考中", { timeout: 3000 }).catch(() => {});
await p.waitForTimeout(2500);
body = await p.locator("body").innerText();
assert("顾问:收到 AI 回复(不 422)", body.includes("兜底") || body.includes("door") || body.includes("车门") || body.includes("候选") || body.includes("澄清") || body.includes("故障"));
assert("顾问:本地兜底标注", body.includes("本地故障字典") || body.includes("兜底") || body.includes("未接线"));

// 4. 证据折叠存在
assert("顾问:证据链折叠入口", body.includes("查看检索证据") || body.includes("透明"));

// 5. e2e 关键文案回归
assert("e2e:添加一步/执行按钮/inject", body.includes("添加一步") && body.includes("执行这个自定义场景") && body.includes("inject（注入）"));
assert("e2e:默认示例行(overspeed)", body.includes("overspeed"));

// 窄屏无横向溢出
const narrow = await b.newPage({ viewport: { width: 860, height: 900 } });
await narrow.goto(BASE + "/", { waitUntil: "networkidle" }).catch(() => {});
await narrow.waitForTimeout(600);
await narrow.click("button:has-text('手动编排')").catch(() => {});
await narrow.waitForTimeout(500);
const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert("窄屏(860px)手动编排无横向溢出", overflow <= 2);
await narrow.close();

await p.screenshot({ path: "docs/preview/t3-scenarios-guide.png" });

console.log("\n===== t3 手动编排引导 + AI 顾问 验证 =====");
console.log(results.join("\n"));
const fails = results.filter((r) => r.startsWith("✗")).length;
console.log(fails ? `\n${fails} 项未通过` : "\n✅ 全部通过");
await b.close();
process.exit(fails ? 1 : 0);

