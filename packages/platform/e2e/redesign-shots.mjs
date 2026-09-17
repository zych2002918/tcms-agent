/* 多分辨率视觉审查：对每个页面在 宽/中/窄 三档截图，供人工检查布局不错位。 */
import { chromium } from "playwright-core";

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const APP = "http://127.0.0.1:8000";
const VIEWPORTS = [
  { name: "wide", w: 1600, h: 950 },
  { name: "mid", w: 1200, h: 900 },
  { name: "narrow", w: 860, h: 900 },
];
const PAGES = [
  { path: "/", name: "dashboard" },
  { path: "/assets", name: "assets" },
  { path: "/scenarios", name: "scenarios" },
  { path: "/graph", name: "graph" },
  { path: "/agent", name: "agent" },
];

const b = await chromium.launch({ executablePath: EDGE, headless: true });
const errors = [];
for (const vp of VIEWPORTS) {
  const ctx = await b.newContext({ viewport: { width: vp.w, height: vp.h } });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`[${vp.name}] ${e}`));
  p.on("console", (m) => m.type() === "error" && errors.push(`[${vp.name}] console: ${m.text().slice(0, 120)}`));
  for (const pg of PAGES) {
    await p.goto(APP + pg.path, { waitUntil: "networkidle" });
    await p.waitForTimeout(700);
    await p.screenshot({ path: `e2e/redesign-${vp.name}-${pg.name}.png`, fullPage: false });
  }
  await ctx.close();
}
console.log("done. errors:");
console.log([...new Set(errors)].join("\n") || "(none)");
await b.close();
