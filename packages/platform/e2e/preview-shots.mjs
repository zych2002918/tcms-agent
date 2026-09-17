import { chromium } from "playwright-core";
const b = await chromium.launch({ executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
// 关键页截图（宽屏全景 + 交互态）
const mk = async (path, name, setup) => {
  const p = await b.newPage({ viewport: { width: 1560, height: 980 } });
  await p.goto("http://127.0.0.1:8000" + path, { waitUntil: "networkidle" });
  await p.waitForTimeout(600);
  if (setup) await setup(p);
  await p.waitForTimeout(1200);
  await p.screenshot({ path: "docs/preview/" + name + ".png", fullPage: true });
  await p.close();
};
await mk("/", "1-dashboard", null);
await mk("/graph", "2-graph-empty", null);
await mk("/graph", "3-graph-search", async (p) => { await p.fill("input[placeholder*='大白话']", "车门故障不能发车"); await p.click("button:has-text('检索')"); });
await mk("/scenarios", "4-scenario-running", async (p) => { await p.selectOption("select", "door_cascade.yaml"); await p.click("button:has-text('运行此场景')"); });
await mk("/scenarios", "5-scenario-done", async (p) => { await p.selectOption("select", "eb_failure_eb.yaml"); await p.click("button:has-text('运行此场景')"); await p.waitForSelector("text=运行完成", { timeout: 10000 }); });
await mk("/assets", "6-assets-faults", async (p) => { await p.click("button:has-text('故障')"); await p.waitForTimeout(400); await p.click("tr:has-text('紧急制动执行失败')"); });
await mk("/faultlab", "8-faultlab", async (p) => { await p.selectOption("select", "overspeed_derate.yaml"); await p.click("button:has-text('演示此场景')"); await p.waitForTimeout(1100); });
await mk("/agent", "7-agent-done", async (p) => { await p.click("button:has-text('执行此任务')"); await p.waitForSelector("text=评分", { timeout: 15000 }); });
console.log("preview shots done");
await b.close();
