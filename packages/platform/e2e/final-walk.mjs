import { chromium } from "playwright-core";
const BASE = "http://127.0.0.1:8000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const b = await chromium.launch({ executablePath: EDGE, headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const results = [];
const assert = (name, cond) => results.push((cond ? "✓ " : "✗ ") + name);

// 1. 总览 —— 新仪表盘
await p.goto(BASE + "/", { waitUntil: "networkidle" });
await p.waitForTimeout(600);
let body = await p.locator("body").innerText();
assert("总览:状态条+从这里开始", body.includes("平台运行中") && body.includes("从这里开始") && body.includes("被测功能"));

// 2. 资产 —— 报文 tab + 筛选
await p.goto(BASE + "/assets", { waitUntil: "networkidle" });
await p.waitForTimeout(600);
body = await p.locator("body").innerText();
assert("资产:报文表+DoorControl", body.includes("DoorControl") && body.includes("TCMS_Heartbeat"));
// 切故障 tab（按钮是 tab 样式，用 role=tab 语义或文字）
await p.click("button:has-text('故障')");
await p.waitForTimeout(500);
body = await p.locator("body").innerText();
assert("资产:故障含 EB 条目", body.includes("紧急制动执行失败"));
await p.click("tr:has-text('紧急制动执行失败')");
await p.waitForTimeout(500);
body = await p.locator("body").innerText();
assert("资产:故障详情侧栏(检测/恢复/处置)", body.includes("恢复") && body.includes("检测") && body.includes("处置"));

// 3. 场景执行 —— 运行流程感
await p.goto(BASE + "/scenarios", { waitUntil: "networkidle" });
await p.selectOption("select", "eb_failure_eb.yaml");
await p.click("button:has-text('运行此场景')");
// 引擎执行 <20ms，瞬态「执行中」状态难稳定采样 —— 改为断言完成流程（更稳）
await p.waitForSelector("text=运行完成", { timeout: 12000 });
body = await p.locator("body").innerText();
assert("场景:运行完成 + PASS+断言", body.includes("PASS") && body.includes("emergency_brake") && body.includes("TCMS 引擎"));
assert("场景:数量来源说明(机器自证)", body.includes("个场景来自当前资产源") && body.includes("scenarios/*.yaml"));
// 手动编排模式：切换 tab + 默认行编辑器存在（runCustom 由契约 t1 接线，此处只验 UI）
await p.click("button:has-text('手动编排')");
await p.waitForTimeout(300);
body = await p.locator("body").innerText();
assert("场景:手动编排编辑器(添加步骤/执行按钮)", body.includes("添加一步") && body.includes("执行这个自定义场景") && body.includes("inject（注入）"));
assert("场景:手动编排默认示例行", body.includes("overspeed") && body.includes("期望处置"));
// 回到内置 tab，保持后续窄屏走查干净
await p.click("button:has-text('内置场景')");
await p.waitForTimeout(200);

// 3b. 故障演示 —— 动画回放流程感
await p.goto(BASE + "/faultlab", { waitUntil: "networkidle" });
await p.selectOption("select", "overspeed_derate.yaml");
await p.click("button:has-text('演示此场景')");
await p.waitForTimeout(900);
body = await p.locator("body").innerText();
assert("FaultLab:事件时间线(注入/检测/处置/恢复)", body.includes("注入故障") && body.includes("处置") && body.includes("恢复") && body.includes("检测到异常"));
assert("FaultLab:引擎观察窗(数据管线)", body.includes("引擎观察窗") && body.includes("数据管线"));
// 播放中画面推进（超速在 10s 注入，等播放推进）
await p.waitForTimeout(6000);
body = await p.locator("body").innerText();
assert("FaultLab:自动播放推进到事件后", body.includes("降级运行") || body.includes("超速"));
await p.screenshot({ path: "docs/preview/faultlab-playing.png" });

// 4. 图谱 —— 分类检索
await p.goto(BASE + "/graph", { waitUntil: "networkidle" });
await p.waitForTimeout(600);
body = await p.locator("body").innerText();
assert("图谱:KB 索引概览(节点/向量)", body.includes("知识底座") && body.includes("向量索引"));
await p.fill("input[placeholder*='大白话']", "车门故障");
await p.click("button:has-text('检索')");
await p.waitForTimeout(1800);
body = await p.locator("body").innerText();
assert("图谱:检索分类出现(故障/全部)", body.includes("全部") && body.includes("door_fault"));
assert("图谱:小白解释行出现", body.includes("这是一条"));
// 点一个命中聚焦图谱
await p.locator("code:has-text('door_fault')").first().click();
await p.waitForTimeout(1600);
const circles = await p.locator("svg circle").count();
assert("图谱:力导向画布渲染", circles >= 2);

// 5. Agent
await p.goto(BASE + "/agent", { waitUntil: "networkidle" });
await p.click("button:has-text('执行此任务')");
await p.waitForSelector("text=评分", { timeout: 15000 });
body = await p.locator("body").innerText();
assert("Agent:任务达成+轨迹(证据/评分)", body.includes("达成") && body.includes("评分") && body.includes("证据"));
assert("Agent:检索证据链(GraphRAG)", body.toLowerCase().includes("检索证据") && body.toLowerCase().includes("graphrag"));

// 5b. 设置 / 新手引导
await p.goto(BASE + "/settings", { waitUntil: "networkidle" });
await p.waitForTimeout(600);
body = await p.locator("body").innerText();
assert("设置:新手引导向导(资产源)", body.includes("新手引导") && (body.includes("资产源") || body.includes("tcms-can-test")));
assert("设置:扩展与集成面板(可扩展点地图)", body.includes("扩展与集成") && body.includes("外部可配置接口") && body.includes("数据扩展") && body.includes("AI / API 扩展") && body.includes("环境变量扩展"));
assert("设置:能力矩阵(机器自证)", body.includes("能力矩阵") && body.includes("browse_assets") && body.includes("llm_generation"));
assert("设置:环境变量键值表", body.includes("TCMS_UPSTREAM_DIR") && body.includes("TCMS_AI_HOME") && body.includes("DASH_API_KEY"));
// 走到 LLM 步骤
await p.click("button:has-text('下一步：接入 AI')");
await p.waitForTimeout(300);
body = await p.locator("body").innerText();
assert("设置:服务商预设(阿里云/DeepSeek)", body.includes("阿里云百炼") && body.includes("DeepSeek 官方"));
assert("设置:key 只存本机提示", body.includes("绝不上传") || body.includes("只写入本机"));
await p.click("button:has-text('下一步：检查引擎')");
await p.waitForTimeout(300);
body = await p.locator("body").innerText();
assert("设置:引擎状态检查步", body.includes("TCMS 引擎") && (body.includes("可用") || body.includes("不可用")));
// 完成步：下一步建议卡片（自由目标 / 自定义场景 可直达）
await p.click("button:has-text('下一步：完成')");
await p.waitForTimeout(300);
body = await p.locator("body").innerText();
assert("设置:完成步下一步建议(AI Agent 自由目标/自定义场景)", body.includes("自由目标") && body.includes("自定义场景"));

// 6. 窄屏无横向溢出
const narrow = await b.newPage({ viewport: { width: 860, height: 900 } });
for (const path of ["/", "/assets", "/scenarios", "/faultlab", "/graph", "/agent", "/settings"]) {
  await narrow.goto(BASE + path, { waitUntil: "networkidle" });
  await narrow.waitForTimeout(500);
  const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert(`窄屏(860px)无横向溢出: ${path}`, overflow <= 2);
}
await narrow.close();

console.log("\n===== 新 UI 端到端走查 =====");
console.log(results.join("\n"));
const fails = results.filter((r) => r.startsWith("✗")).length;
console.log(fails ? `\n${fails} 项未通过` : "\n✅ 全部通过");
await b.close();
process.exit(fails ? 1 : 0);
