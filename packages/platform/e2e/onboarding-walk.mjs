// 引导弹窗 e2e 走查（playwright-core + Edge）：首启自动弹 → 跳过 → 重开 → 步骤推进
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const OUT = process.env.SHOT_DIR || "e2e";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });

const results = [];
const check = (name, cond, extra = "") => {
  results.push(`${cond ? "PASS" : "FAIL"}  ${name}${extra ? ` — ${extra}` : ""}`);
};

try {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await sleep(900);

  const modalVisible = await page.locator('[role="dialog"]').isVisible().catch(() => false);
  check("首启自动弹出引导弹窗", modalVisible);

  const title = await page.getByText("首次使用引导").isVisible().catch(() => false);
  check("顶栏标题「首次使用引导」", title);

  const mockTxt = (await page.locator("body").innerText()).includes("离线 Mock");
  check("欢迎步提及离线 Mock 可跑", mockTxt);

  // 跳过 → 弹窗关闭
  await page.locator('[role="dialog"] button:has-text("跳过")').first().click();
  await sleep(400);
  const closed = !(await page.locator('[role="dialog"]').isVisible().catch(() => false));
  check("点「跳过」后弹窗关闭", closed);

  // 顶部小条仍在 → 重开
  const hint = (await page.locator("body").innerText()).includes("第一次用");
  check("未完成时顶部保留引导入口条", hint);
  await page.getByRole("button", { name: "打开引导 →" }).click();
  await sleep(400);
  const reopened = await page.locator('[role="dialog"]').isVisible().catch(() => false);
  check("点「打开引导」重开弹窗", reopened);

  // 开始配置 → 步骤②
  await page.locator('[role="dialog"] button:has-text("开始配置")').click();
  await sleep(300);
  const step2 = await page.getByText("选服务商 + 填 API Key").isVisible().catch(() => false);
  check("进入步骤②(选服务商+填key)", step2);

  // 选 DeepSeek → base_url 自动填
  const sel = page.locator('[role="dialog"] select').first();
  await sel.selectOption({ label: "DeepSeek 官方" });
  await sleep(200);
  const baseUrlVal = await page
    .locator('[role="dialog"] input[placeholder*="api.deepseek.com"]')
    .inputValue()
    .catch(() => "");
  check("选 DeepSeek 自动填 base_url", baseUrlVal.includes("deepseek.com"), `base=${baseUrlVal}`);

  // 保存并下一步 → 步骤③
  await page.locator('[role="dialog"] button:has-text("保存并下一步")').click();
  await sleep(400);
  const step3 = await page.getByText("测试连接 & 获取你的模型列表").isVisible().catch(() => false);
  check("进入步骤③(测试连接/获取模型)", step3);

  const probeBtn = await page.locator('[role="dialog"] button:has-text("测试连接并获取模型")').isVisible().catch(() => false);
  check("步骤③有「测试连接并获取模型」按钮", probeBtn);

  await page.screenshot({ path: `${OUT}/shots-onboarding-step3.png` });
  check("截图 shots-onboarding-step3.png 已存", true);
} catch (e) {
  results.push(`FAIL  ${String(e).slice(0, 300)}`);
} finally {
  await browser.close();
}

console.log(results.join("\n"));
process.exit(results.some((r) => r.startsWith("FAIL")) ? 1 : 0);
