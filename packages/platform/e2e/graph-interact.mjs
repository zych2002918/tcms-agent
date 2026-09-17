#!/usr/bin/env node
// 图谱交互回归（P：单击详情+高亮环 / 双击以其为中心跳转 / ⬅ 返回 / 3D 同款）
// 前置：后端 http://127.0.0.1:8000 已运行 且 web/dist 已构建。
// 运行：node e2e/graph-interact.mjs
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const BASE = process.env.TCMS_E2E_BASE ?? "http://127.0.0.1:8000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
const results = [];
const ok = (name, cond) => results.push(`${cond ? "✓ " : "✗ "}${name}`) || cond;

const hasRing = async (nid) =>
  page.evaluate(
    (id) => {
      const g = document.querySelector(`svg g[data-nid="${id.replaceAll('"', '\\"')}"]`);
      return !!g && Array.from(g.querySelectorAll("circle")).some((c) => c.getAttribute("stroke") === "var(--info)");
    },
    nid
  );
const isSeed = async (nid) =>
  page.evaluate(
    (id) => {
      const g = document.querySelector(`svg g[data-nid="${id.replaceAll('"', '\\"')}"]`);
      return !!g && !!g.querySelector("circle.pulse-glow");
    },
    nid
  );
// Node 端无 CSS.escape：简单转义属性值里的引号/反斜杠即可
const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

try {
  await page.goto(`${BASE}/graph?focus=F-EBM`, { waitUntil: "networkidle" });
  await page.waitForSelector("svg g[data-nid]", { timeout: 25000 });

  // 选一个非种子节点（种子=当前中心 pulse-glow）
  const seedId = await page.evaluate(() => document.querySelector("svg g[data-nid] circle.pulse-glow")?.parentElement?.getAttribute("data-nid") ?? null);
  const nodeIds = await page.$$eval("svg g[data-nid]", (gs) => gs.map((g) => g.getAttribute("data-nid")).filter(Boolean));
  const target = nodeIds.find((id) => id !== seedId);
  assert(target, "需存在非种子节点用于点击测试");

  // 1. 单击 → 详情面板 + 高亮环
  await page.click(`svg g[data-nid="${esc(target)}"]`);
  await page.waitForSelector("button:has-text('以它为中心扩展')", { timeout: 8000 });
  ok("2D 单击:详情面板出现（含一键扩展按钮）", true);
  await page.waitForFunction((id) => !!document.querySelector(`svg g[data-nid="${String(id).replaceAll('"', '\\"')}"] circle[stroke="var(--info)"]`), target, { timeout: 8000 });
  ok("2D 单击:节点出现选中高亮环", true);

  // 2. 双击 → 以它为中心跳转（该节点出现种子脉冲环）
  await page.dblclick(`svg g[data-nid="${esc(target)}"]`);
  await page.waitForFunction((id) => {
    const g = document.querySelector(`svg g[data-nid="${String(id).replaceAll('"', '\\"')}"]`);
    return !!g && !!g.querySelector("circle.pulse-glow");
  }, target, { timeout: 15000 });
  ok("2D 双击:节点变种子（以其为中心跳转生效）", true);

  // 3. ⬅ 返回 → 回到上一种子
  const backBtn = page.locator("button:has-text('⬅ 返回')");
  if ((await backBtn.count()) > 0) {
    await backBtn.click();
    await page.waitForFunction((id) => {
      const g = document.querySelector(`svg g[data-nid="${String(id).replaceAll('"', '\\"')}"]`);
      return !!g && !!g.querySelector("circle.pulse-glow");
    }, seedId, { timeout: 15000 });
    ok("2D ⬅ 返回:回到上一视图种子", true);
  } else {
    ok("2D ⬅ 返回:存在返回按钮", false);
  }

  // 4. 3D：先停自转，再单击节点看详情 + 高亮环
  await page.click("button:has-text('3D')");
  await page.waitForSelector("svg g[data-nid]", { timeout: 15000 });
  const stopBtn = page.locator("button:has-text('停转')");
  if ((await stopBtn.count()) > 0) {
    await stopBtn.click();
    await page.waitForTimeout(500);
  }
  const t3 = await page.$$eval("svg g[data-nid]", (gs, seed) => gs.map((g) => g.getAttribute("data-nid")).filter(Boolean).find((id) => id !== seed), seedId);
  if (t3) {
    await page.click(`svg g[data-nid="${esc(t3)}"]`);
    await page.waitForFunction((id) => !!document.querySelector(`svg g[data-nid="${String(id).replaceAll('"', '\\"')}"] circle[stroke="var(--info)"]`), t3, { timeout: 8000 }).catch(() => undefined);
    const ring3 = await hasRing(t3);
    ok("3D 单击:详情/高亮环生效", ring3);
  } else {
    ok("3D 单击:有待点节点", false);
  }
} catch (e) {
  ok(`执行异常: ${e instanceof Error ? e.message : String(e)}`, false);
} finally {
  console.log(results.join("\n"));
  const failed = results.filter((r) => r.startsWith("✗")).length;
  await browser.close().catch(() => undefined);
  process.exitCode = failed === 0 ? 0 : 1; // 不用 process.exit，避免截断 stdout
}
