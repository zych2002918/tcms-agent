import { chromium } from "playwright-core";
const BASE = "http://127.0.0.1:8000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const b = await chromium.launch({ executablePath: EDGE, headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 1100 } });
const out = [];
const assert = (n, c) => out.push([n, c]);

// 0. 首次引导弹窗（未完成 onboarding 时自动弹）→ 跳过，保证本走查聚焦设置页
await p.goto(BASE + "/settings", { waitUntil: "networkidle" });
await p.waitForTimeout(800);
const dialog = p.locator('[role="dialog"]');
if (await dialog.isVisible().catch(() => false)) {
  await dialog.locator("button:has-text('跳过')").first().click();
  await p.waitForTimeout(300);
}

// 1. Settings page loads & wizard shows
let body = await p.locator("body").innerText();
assert("设置页:新手引导面板", body.includes("新手引导"));
assert("设置页:资产源步骤", body.includes("资产源") || body.includes("tcms-can-test"));
assert("设置页:高级说明/外接接口", body.includes("外部可配置接口"));
await p.screenshot({ path: "e2e/shots-settings-0.png" });

// 2. Step to LLM config
await p.click("button:has-text('下一步：接入 AI')");
await p.waitForTimeout(400);
body = await p.locator("body").innerText();
assert("LLM 步:服务商预设(阿里云/DeepSeek)", body.includes("阿里云百炼") && body.includes("DeepSeek 官方"));
assert("LLM 步:key 只存本机提示", body.includes("绝不上传") || body.includes("只写入本机"));
assert("LLM 步:测试连接并获取模型按钮", body.includes("测试连接并获取模型"));
await p.screenshot({ path: "e2e/shots-settings-llm.png" });

// pick provider deepseek fills fields
await p.selectOption("select", "deepseek");
await p.waitForTimeout(300);
const baseVal = await p.locator("input[placeholder='https://…/v1']").inputValue();
assert("选 DeepSeek 自动填 base_url", baseVal.includes("deepseek.com"));
const hasProbeBtn = await p.locator("button:has-text('测试连接并获取模型')").isVisible();
assert("模型获取按钮可见", hasProbeBtn);

// 3. step engine check
await p.click("button:has-text('下一步：检查引擎')");
await p.waitForTimeout(300);
body = await p.locator("body").innerText();
assert("引擎检查步:引擎状态", body.includes("TCMS 引擎") && (body.includes("可用") || body.includes("不可用")));

// 4. complete step
await p.click("button:has-text('下一步：完成')");
await p.waitForTimeout(300);
body = await p.locator("body").innerText();
assert("完成步:开始使用按钮", body.includes("开始使用"));
await p.screenshot({ path: "e2e/shots-settings-done.png" });

// narrow
const narrow = await b.newPage({ viewport: { width: 860, height: 1000 } });
await narrow.goto(BASE + "/settings", { waitUntil: "networkidle" });
await narrow.waitForTimeout(600);
const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert("窄屏(860)无横向溢出 /settings", overflow <= 2);

console.log("\n===== 设置/引导页验证 =====");
out.forEach(([n, ok]) => console.log((ok ? "✓ " : "✗ ") + n));
console.log("fails:", out.filter(([, c]) => !c).length);
await b.close();
process.exit(out.filter(([, c]) => !c).length ? 1 : 0);
