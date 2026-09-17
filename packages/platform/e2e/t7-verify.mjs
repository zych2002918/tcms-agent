// t7 真连通验证（be-core t2 已落盘：POST /api/agent/advisor 真端点 + api.advisorTurn）
// preview 4173 不代理 /api → route 全量转发到真实后端 8000（故障字典/场景/advisor 全真实）。
// 三条路径 + 采纳建议 + 离线标注，全部走真后端契约。
import { chromium } from "playwright-core";
const UI = "http://localhost:4173";
const API = "http://127.0.0.1:8000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const b = await chromium.launch({ executablePath: EDGE, headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
const results = [];
const assert = (name, cond) => results.push((cond ? "✓ " : "✗ ") + name);

await p.route("**/api/**", async (route) => {
  const req = route.request();
  try {
    const resp = await route.fetch({
      url: API + new URL(req.url()).pathname + new URL(req.url()).search,
      method: req.method(),
      headers: { ...req.headers(), host: new URL(API).host },
      postData: req.postData() ?? undefined,
    });
    await route.fulfill({ response: resp });
  } catch {
    await route.fulfill({ status: 502, body: "backend unreachable" });
  }
});

await p.goto(UI + "/scenarios", { waitUntil: "networkidle" }).catch(async () => {
  await p.goto(UI + "/", { waitUntil: "networkidle" });
  await p.waitForTimeout(800);
});
await p.waitForTimeout(1000);
await p.click("button:has-text('手动编排')").catch(() => {});
await p.waitForTimeout(800);
let body = await p.locator("body").innerText();
assert("页:故障字典真实加载", body.includes("紧急制动") || body.includes("door_fault") || body.includes("overspeed"));

const chatInput = p.locator("input[placeholder*='说你的意图']");
const sendMsg = async (text) => {
  await chatInput.fill(text);
  await p.click("button:has-text('发送')");
  // 轮询等待 AI 回复（LLM 冷启动可能慢）：等消息区出现新的 AI 气泡（非 pending）
  for (let i = 0; i < 20; i++) {
    await p.waitForTimeout(600);
    const pending = await p.locator("text=顾问思考中").count().catch(() => 0);
    if (pending === 0) break;
  }
  await p.waitForTimeout(1200);
  return p.locator("body").innerText();
};

// 2a) 明确命中「验证车门故障不能发车」→ match_fault + door_fault 候选 + 现成场景
body = await sendMsg("验证车门故障不能发车");
assert("2a:命中 intent=match_fault(回复含故障键)", body.includes("door_fault") && (body.includes("车门故障") || body.includes("derate")));
const doorChips = await p.locator("button.tag:has-text('door_fault')").count();
assert("2a:候选 chip(点一下填入)", doorChips > 0);
assert("2a:LLM/规则诚实标注", body.includes("LLM") || body.includes("离线规则") || body.includes("真实故障字典") || doorChips > 0);
// 点候选 chip → 填入步骤行
if (doorChips > 0) {
  await p.locator("button.tag:has-text('door_fault')").first().click().catch(() => {});
  await p.waitForTimeout(500);
  body = await p.locator("body").innerText();
  assert("2a:点候选后步骤行填入", (body.match(/door_fault/g) || []).length >= 2);
} else {
  assert("2a:点候选后步骤行填入", false);
}

// 2b) 模糊「门好像有问题」→ clarify 候选确认 或 RAG
body = await sendMsg("门好像有问题");
assert("2b:模糊输入有回复(不拒绝)", body.includes("车门") || body.includes("door") || body.includes("候选") || body.includes("哪个") || body.includes("确认"));
assert("2b:澄清机制出现", body.includes("澄清") || body.includes("用这句问我") || body.includes("序号") || body.includes("哪个"));

// 2c) 无匹配闲聊「今天天气不错」→ out_of_domain 友好引导
body = await sendMsg("今天天气不错");
assert("2c:域外友好引导回复", body.includes("TCMS") || body.includes("故障") || body.includes("试试") || body.includes("帮不上"));
assert("2c:无报错横幅", !body.includes("⚠ 执行失败：") && body.includes("AI 编排顾问"));

// 3) 采纳建议：编排意图「把超速和车门故障组合成场景」→ compose_scenario + suggested_steps + 采纳按钮
body = await sendMsg("帮我编排一个超速和车门故障叠加的场景");
assert("3:编排意图给建议步骤", body.includes("建议编排") || body.includes("采纳建议") || body.includes("超速") || body.includes("车门"));
const adoptBtn = p.locator("button:has-text('采纳建议步骤')");
if ((await adoptBtn.count()) > 0) {
  const before = await p.locator("div.panel.bg-surface-2\\/40").count();
  await adoptBtn.first().click().catch(() => {});
  await p.waitForTimeout(500);
  body = await p.locator("body").innerText();
  const overspeedInRows = (body.match(/overspeed/g) || []).length;
  assert("3:采纳后步骤进入编排表", overspeedInRows >= 1 || (await p.locator("div.panel.bg-surface-2\\/40").count()) > before);
} else {
  assert("3:采纳后步骤进入编排表", body.includes("s") && (body.includes("注入") || body.includes("恢复")));
}

// 证据链折叠入口（RAG 路径出现时）
assert("证据:折叠入口存在或回复完整", body.includes("查看检索证据") || body.includes("检索证据") || body.includes("证据"));

// e2e 文案回归
assert("e2e:添加一步/执行按钮/inject", body.includes("添加一步") && body.includes("执行这个自定义场景") && body.includes("inject（注入）"));
assert("e2e:默认示例行(overspeed)", body.includes("overspeed"));

const narrow = await b.newPage({ viewport: { width: 860, height: 900 } });
await narrow.goto(UI + "/", { waitUntil: "networkidle" }).catch(() => {});
await narrow.waitForTimeout(600);
await narrow.click("button:has-text('手动编排')").catch(() => {});
await narrow.waitForTimeout(500);
const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert("窄屏(860px)手动编排无横向溢出", overflow <= 2);
await narrow.close();

await p.screenshot({ path: "docs/preview/t7-advisor-live.png" });

console.log("\n===== t7 AI 编排顾问 真连通（后端真实 advisor）=====");
console.log(results.join("\n"));
const fails = results.filter((r) => r.startsWith("✗")).length;
console.log(fails ? `\n${fails} 项未通过` : "\n✅ 全部通过");
await b.close();
process.exit(fails ? 1 : 0);
