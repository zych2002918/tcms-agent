#!/usr/bin/env node
// P1-4 e2e 主流程 smoke：真实 chromium（playwright-core）走查三大页面。
// 前置：后端已在 http://127.0.0.1:8000 运行且 web/dist 已构建。
// 运行：node e2e/main-flow.mjs   （或 cd e2e && npm run smoke）
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const BASE = process.env.TCMS_E2E_BASE ?? "http://127.0.0.1:8000";

function findChromium() {
  const roots = [path.join(os.homedir(), "AppData", "Local", "ms-playwright"), path.join(os.homedir(), ".cache", "ms-playwright")];
  const candidates = [];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const dir of fs.readdirSync(root)) {
      const full = path.join(root, dir);
      for (const rel of ["chrome-win64/chrome.exe", "chrome-headless-shell-win64/chrome-headless-shell.exe", "chrome-linux/chrome", "chrome-mac/Chromium"]) {
        const p = path.join(full, rel);
        if (fs.existsSync(p)) candidates.push(p);
      }
    }
  }
  return candidates[0];
}

async function waitText(page, text, ms = 20000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), text, { timeout: ms });
}

async function main() {
  const exe = findChromium();
  if (!exe) throw new Error("未找到 chromium 可执行文件，请先 npx playwright install chromium");
  console.log(`[e2e] chromium: ${exe}`);
  const browser = await chromium.launch({ executablePath: exe, headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const results = [];
  const record = (name) => results.push(`PASS ${name}`);

  try {
    // A. 图谱默认骨架（未搜索即加载 overview）
    await page.goto(`${BASE}/graph`, { waitUntil: "domcontentloaded" });
    await waitText(page, "基础关联图谱（13 系统域骨架）");
    assert(page.url().includes("/graph"));
    record("graph: 默认基础关联图谱已加载（无需搜索）");
    const nodeText = await page.evaluate(() => document.body.innerText);
    assert(nodeText.includes("56 节点"), "overview 计数 Tag 应含 56 节点");
    record("graph: overview 计数 56 节点可见");

    // B. FaultLab：URL 直达场景自动演示 + 具体异常文案
    await page.goto(`${BASE}/faultlab?scenario=door_cascade.yaml&from=scenario-exec`, { waitUntil: "domcontentloaded" });
    await waitText(page, "车门故障级联");
    await page.waitForTimeout(1800); // 给 rAF 播放器推进几帧
    const faultText = await page.evaluate(() => document.body.innerText);
    assert(/注入故障/.test(faultText) || /高亮：/.test(faultText), "应出现故障注入/高亮内容");
    record("faultlab: 场景自动播放并出现故障高亮");

    // C. Agent 症状诊断卡（无码症状 → 候选卡）
    await page.goto(`${BASE}/agent`, { waitUntil: "domcontentloaded" });
    await waitText(page, "没有故障码？描述异常现象 → 图谱多跳诊断");
    const input = page.locator('input[aria-label="症状描述输入"]');
    await input.fill("仪表盘闪烁但无故障码");
    await page.getByRole("button", { name: "🔎 症状诊断" }).click();
    await waitText(page, "症状资产");
    await page.waitForTimeout(600);
    const diagText = await page.evaluate(() => document.body.innerText);
    assert(diagText.includes("aux_24v_undervoltage") || diagText.includes("aux_capacitor_aging"), "诊断候选应含供电域故障");
    record("agent: 症状诊断卡输出候选（含供电域）");

    // D. 图谱 3D：切到 3D 视图仍渲染画布，且“适配/停转”控件可见
    await page.goto(`${BASE}/graph`, { waitUntil: "domcontentloaded" });
    await waitText(page, "基础关联图谱（13 系统域骨架）");
    await page.getByRole("button", { name: "◍ 3D" }).click();
    await page.waitForTimeout(400);
    assert((await page.locator("svg.chart-bg").count()) >= 1, "3D 视图应有 svg 画布");
    const threeText = await page.evaluate(() => document.body.innerText);
    assert(threeText.includes("⤢ 适配") && threeText.includes("⏸ 停转"), "3D 控制条应有适配与停转");
    record("graph-3d: 3D 视图渲染正常，控件可用");
  } finally {
    await browser.close();
  }

  console.log(results.join("\n"));
  console.log(`[e2e] ALL PASS (${results.length})`);
}

main().catch((e) => {
  console.error("[e2e] FAIL:", e?.message ?? e);
  process.exit(1);
});
