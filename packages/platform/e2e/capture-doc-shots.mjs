// 文档截图采集：为 README 生成"当前构建"的界面截图。
// 带新鲜度守卫——若页面上的引擎版本/图谱规模与当前构建不符，直接失败，
// 避免把过期截图（如引擎 v1.9.1、图谱 221 节点）再次发到 README。
//
// 前置：
//   1) 后端已启动： .venv\Scripts\python.exe -m uvicorn tcms_ai_platform.server.app:app --port 8000
//   2) web/dist 已构建（README 引用的即线上界面）
// 用法： cd e2e && node capture-doc-shots.mjs
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const BASE = process.env.TCMS_SHOT_BASE ?? "http://127.0.0.1:8000";
const EDGE =
  process.env.TCMS_EDGE ??
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const OUT = "../docs";

// 期望的新鲜度：与当前构建一致（改版时同步更新这里）
const EXPECT = {
  engine: process.env.TCMS_EXPECT_ENGINE ?? "1.12.0",
  minNodes: Number(process.env.TCMS_EXPECT_MIN_NODES ?? 600),
};

mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (n, c, extra = "") =>
  results.push(`${c ? "✓" : "✗"} ${n}${extra ? " — " + extra : ""}`);
let failed = false;

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });

try {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);

  // ---- 新鲜度守卫 ----
  const st = await page.evaluate(async () => (await fetch("/api/system/status")).json());
  const kb = await page.evaluate(async () => (await fetch("/api/kb/stats")).json());
  const engineOk = st?.engine?.version === EXPECT.engine;
  ok(`新鲜度:引擎版本=${EXPECT.engine}`, engineOk, `实际 ${st?.engine?.version}`);
  const nodesOk = (kb?.graph?.nodes ?? 0) >= EXPECT.minNodes;
  ok(`新鲜度:图谱节点≥${EXPECT.minNodes}`, nodesOk, `实际 ${kb?.graph?.nodes}`);
  if (!engineOk || !nodesOk) failed = true;

  const theme = async (t) => {
    const cur = await page.evaluate(() => document.documentElement.dataset.theme);
    if (cur !== t) {
      await page.click("button[aria-label='切换主题']");
      await page.waitForTimeout(600);
    }
  };

  // ---- 1. 知识图谱（2D, dark）----
  await theme("dark");
  await page.goto(BASE + "/graph?focus=F-EBM", { waitUntil: "networkidle" });
  await page.waitForTimeout(2600);
  const gBody = await page.locator("body").innerText();
  ok("图谱:2D 控制条就位", gBody.includes("2D") && gBody.includes("适配"));
  ok("图谱:EBM 节点已渲染", /F-EBM|紧急制动/.test(gBody));
  await page.screenshot({ path: `${OUT}/graph-2d-preview.png` });
  results.push("  → docs/graph-2d-preview.png");

  // ---- 2. FaultLab（light）----
  await theme("light");
  await page.goto(BASE + "/faultlab", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page.selectOption("select", "overspeed_derate.yaml");
  await page.click("button:has-text('演示此场景')");
  await page.waitForTimeout(1800);
  const fBody = await page.locator("body").innerText();
  ok("FaultLab:场景已加载", /overspeed_derate/.test(fBody));
  await page.screenshot({ path: `${OUT}/faultlab-preview.png` });
  results.push("  → docs/faultlab-preview.png");
} catch (e) {
  results.push("✗ 异常: " + (e?.message ?? String(e)));
  failed = true;
}

await browser.close();
console.log(results.join("\n"));
process.exit(failed ? 1 : 0);
