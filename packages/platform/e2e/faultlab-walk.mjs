import { chromium } from "playwright-core";
const BASE = "http://127.0.0.1:8000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const b = await chromium.launch({ executablePath: EDGE, headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 1200 } });
const out = [];
const assert = (n, c) => out.push([n, c]);

// FaultLab: demo overspeed with auto-play; check event list visible after load
await p.goto(BASE + "/faultlab", { waitUntil: "networkidle" });
await p.waitForTimeout(500);
await p.selectOption("select", "overspeed_derate.yaml");
await p.click("button:has-text('演示此场景')");
await p.waitForTimeout(900);
let body = await p.locator("body").innerText();
assert("FaultLab 事件时间线含 注入", body.includes("注入故障"));
assert("FaultLab 事件时间线含 处置", body.includes("处置"));
assert("FaultLab 超速场景含 降级运行", body.includes("降级运行"));
assert("FaultLab 引擎观察窗(数据管线)", body.includes("引擎观察窗") && body.includes("数据管线"));
assert("FaultLab 事件刻度图例", body.includes("恢复"));
await p.screenshot({ path: "e2e/shots-faultlab-os2.png", fullPage: false });
// wait for auto play to reach ~ 12s (inject 10s)
await p.waitForTimeout(4000);
body = await p.locator("body").innerText();
assert("FaultLab 播放到事件后画面推进", body.includes("s / 24") || body.includes("s / 24.0") || /\.\ds \/ 24/.test(body));
await p.screenshot({ path: "e2e/shots-faultlab-playing2.png" });

// FaultLab eb_failure teaching point
await p.selectOption("select", "eb_failure_eb.yaml");
await p.click("button:has-text('演示此场景')");
await p.waitForTimeout(700);
body = await p.locator("body").innerText();
assert("eb_failure 场景事件含 紧急制动执行失败", body.includes("紧急制动执行失败"));
await p.screenshot({ path: "e2e/shots-faultlab-eb2.png" });

// t4 ①: 内置场景 URL 直连 + 来源 Tag（?scenario=file&from=scenario-exec）
await p.goto(BASE + "/faultlab?scenario=overspeed_derate.yaml&from=scenario-exec", { waitUntil: "networkidle" });
await p.waitForTimeout(900);
body = await p.locator("body").innerText();
assert("t4 URL 直连自动演示（超速场景事件）", body.includes("注入故障"));
assert("t4 来源 Tag「来自场景执行」", body.includes("来自场景执行"));
await p.screenshot({ path: "e2e/shots-faultlab-t4-url.png" });

// t4 ②: sessionStorage tcms.faultlab.draft → demo-steps（端点未就绪则跳过，防后端未合入时误报）
let dsOk = false;
try {
  const r = await p.evaluate(async () => {
    const resp = await fetch("/api/faultlab/demo-steps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "e2e-t4-draft",
        steps: [
          { at: 5, action: "inject", fault: "door_fault", node: "vcu", level: "major", expect: "derate" },
          { at: 12, action: "recover", fault: "door_fault" },
          { at: 18, action: "inject", fault: "overspeed", node: "vcu", level: "major", expect: "derate" },
          { at: 26, action: "recover", fault: "overspeed" },
        ],
      }),
    });
    return resp.ok;
  });
  dsOk = r === true;
} catch {
  dsOk = false;
}
if (!dsOk) console.log("– demo-steps 端点未就绪（be-core t2 未合入），t4 draft 断言跳过");
if (dsOk) {
  await p.evaluate(() => {
    sessionStorage.setItem(
      "tcms.faultlab.draft",
      JSON.stringify({
        name: "e2e-t4-draft",
        from: "agent-exec",
        steps: [
          { at: 5, action: "inject", fault: "door_fault", node: "vcu", level: "major", expect: "derate" },
          { at: 12, action: "recover", fault: "door_fault" },
          { at: 18, action: "inject", fault: "overspeed", node: "vcu", level: "major", expect: "derate" },
          { at: 26, action: "recover", fault: "overspeed" },
        ],
      })
    );
  });
  await p.goto(BASE + "/faultlab", { waitUntil: "networkidle" });
  await p.waitForTimeout(1000);
  body = await p.locator("body").innerText();
  assert("t4 draft 通道自动演示（自定义序列）", body.includes("自定义故障序列") || body.includes("e2e-t4-draft"));
  assert("t4 来源 Tag「来自 Agent 执行」", body.includes("来自 Agent 执行"));
  await p.screenshot({ path: "e2e/shots-faultlab-t4-draft.png" });
}

// agent evidence panel check
await p.goto(BASE + "/agent", { waitUntil: "networkidle" });
await p.waitForTimeout(500);
await p.click("button:has-text('执行此任务')");
await p.waitForSelector("text=评分", { timeout: 20000 });
await p.waitForTimeout(1200);
body = await p.locator("body").innerText();
const lowBody = body.toLowerCase();
assert("Agent 证据链面板(检索证据 GraphRAG)", lowBody.includes("检索证据") && lowBody.includes("graphrag"));
const evidLines = (body.match(/mechanism:|interlock:|threshold:|state:/g) || []).length;
assert("Agent 证据含真实知识节点", evidLines >= 1);
await p.screenshot({ path: "e2e/shots-agent-evidence2.png" });

// narrow screens
const narrow = await b.newPage({ viewport: { width: 860, height: 1000 } });
for (const path of ["/faultlab", "/graph"]) {
  await narrow.goto(BASE + path, { waitUntil: "networkidle" });
  await narrow.waitForTimeout(500);
  const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert(`窄屏无横向溢出 ${path}`, overflow <= 2);
}
await narrow.close();

console.log("\n===== FaultLab / 证据链 UI 验证 2 =====");
out.forEach(([n, ok]) => console.log((ok ? "✓ " : "✗ ") + n));
console.log("fails:", out.filter(([, c]) => !c).length);
await b.close();
process.exit(out.filter(([, c]) => !c).length ? 1 : 0);
