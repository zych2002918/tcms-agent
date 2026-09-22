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

/** 备选扫描位置（仅当 playwright 自己解析不出可用路径时才用）。 */
const CHROMIUM_ROOTS = [
  path.join(os.homedir(), "AppData", "Local", "ms-playwright"),
  path.join(os.homedir(), ".cache", "ms-playwright"),
  "/root/.cache/ms-playwright",
];

function findChromium() {
  const roots = CHROMIUM_ROOTS;
  // 1) 显式指定优先（CI / 特殊环境可用 TCMS_CHROMIUM 指任意浏览器）
  const explicit = process.env.TCMS_CHROMIUM;
  if (explicit) return explicit;
  // 2) 首选 playwright **自己**解析出的路径：那正是 `npx playwright install` 装的位置，
  //    也因此一定与 playwright-core 的版本期望一致。此前只靠扫描目录猜结构，结果在 CI 上
  //    出现"服务起来了、dist 也构建了，却报未找到 chromium"这种本地永远复现不了的失败。
  try {
    const p = chromium.executablePath();
    if (p && fs.existsSync(p)) return p;
  } catch {
    /* 解析不出来就退回目录扫描 */
  }
  const candidates = [];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const dir of fs.readdirSync(root).sort()) {
      const full = path.join(root, dir);
      for (const rel of ["chrome-win64/chrome.exe", "chrome-headless-shell-win64/chrome-headless-shell.exe", "chrome-linux/chrome", "chrome-linux64/chrome", "chrome-mac/Chromium"]) {
        const p = path.join(full, rel);
        if (fs.existsSync(p)) candidates.push(p);
      }
    }
  }
  return candidates[0];
}

async function waitText(page, text, ms = 20000) {
  // 全新实例（CI 就是全新实例）会自动弹出「首次使用引导」弹窗，它是 aria-modal 全屏浮层，
  // **会拦截点击** —— 真实用户也是先把它关掉。本地因为引导已完成，永远看不到这一幕，
  // 所以这个失败只在 CI 上出现（这正是把 e2e 放进 CI 的价值）。
  const guide = page.getByRole("dialog", { name: "首次使用引导" });
  if (await guide.count()) {
    await page.keyboard.press("Escape"); // Esc 关闭（弹窗三件套之一）
    if (await guide.count()) {
      const skip = guide.getByRole("button", { name: /跳过|关闭|稍后/ }).first();
      if (await skip.count()) await skip.click().catch(() => undefined);
    }
    await guide.waitFor({ state: "hidden", timeout: 5000 }).catch(() => undefined);
  }
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), text, { timeout: ms });
}

async function main() {
  const exe = findChromium();
  if (!exe) {
    let resolved = "(解析失败)";
    try {
      resolved = chromium.executablePath();
    } catch (e) {
      resolved = `(解析抛错: ${String(e && e.message)} )`;
    }
    throw new Error(
      [
        "未找到 chromium 可执行文件。诊断信息：",
        `  TCMS_CHROMIUM          = ${process.env.TCMS_CHROMIUM ?? "(未设)"}`,
        `  playwright 期望的路径  = ${resolved}`,
        `  该路径存在吗           = ${resolved.startsWith("/") || resolved.includes(":") ? fs.existsSync(resolved) : false}`,
        `  扫描过的位置           = ${CHROMIUM_ROOTS.join(" / ")}`,
        "  修法：npx playwright install chromium（或显式设 TCMS_CHROMIUM 指向浏览器可执行文件）",
      ].join("\n"),
    );
  }
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
    assert(nodeText.includes("59 节点"), "overview 计数 Tag 应含 59 节点（13 系统 + 11 功能 + 代表故障 + 孤立域补的真实成员）");
    record("graph: overview 计数 59 节点可见");

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
    await page.getByRole("button", { name: "症状诊断" }).click();
    await waitText(page, "症状资产");
    await page.waitForTimeout(600);
    const diagText = await page.evaluate(() => document.body.innerText);
    assert(diagText.includes("aux_24v_undervoltage") || diagText.includes("aux_capacitor_aging"), "诊断候选应含供电域故障");
    record("agent: 症状诊断卡输出候选（含供电域）");

    // D. 图谱 3D：切到 3D 视图仍渲染画布，且“适配/停转”控件可见
    await page.goto(`${BASE}/graph`, { waitUntil: "domcontentloaded" });
    await waitText(page, "基础关联图谱（13 系统域骨架）");
    await page.getByRole("tab", { name: "3D" }).click();
    await page.waitForTimeout(400);
    assert((await page.locator("svg.chart-bg").count()) >= 1, "3D 视图应有 svg 画布");
    const threeText = await page.evaluate(() => document.body.innerText);
    assert((await page.getByRole("button", { name: "适配" }).count()) >= 1, "3D 控制条应有适配");
    assert((await page.getByRole("button", { name: "停转" }).count()) >= 1, "3D 控制条应有停转");
    assert(threeText.includes("拖拽旋转"), "3D 模式下应显示 3D 操作提示（2D 提示为「空白拖拽平移」）");
    record("graph-3d: 3D 视图渲染正常，控件可用");

    // E. 空子图不许静默（曾出现：种子解析不到实体 → 纯白画布，用户只会以为"图谱不全"）
    const bogus = encodeURIComponent("不存在的对象XYZ");
    await page.goto(`${BASE}/graph?focus=${bogus}`, { waitUntil: "domcontentloaded" });
    await waitText(page, "这个对象在图谱里没有对应节点");
    record("graph: 解析不到的种子给出空态解释（不静默白屏）");
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
