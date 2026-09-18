/**
 * 用 CDP 驱动 Edge 截图（含交互），做"改完自己看"的视觉验证。
 *
 * 为什么需要它：headless 的 `--screenshot` 只能拍静态首帧，
 * 而本项目最需要看的是**运行态**——白盒流跑起来之后长什么样。
 * 这里通过 DevTools 协议注入输入、点按钮，再抓图。
 *
 * 用法（先起一个带调试端口的 Edge）：
 *   msedge --headless=new --remote-debugging-port=9333 --user-data-dir=<dir> about:blank
 *   node shoot.mjs <url> <out.png> [--goal "文本"] [--click "按钮文字"] [--wait 8000] [--w 1680] [--h 1000]
 *
 * 本文件是开发期工具，不参与构建。产物自己指定路径（建议落在仓库外的临时目录）。
 *
 * 为什么值得进仓库：headless 的 --screenshot 只能拍静态首帧，而本项目最需要看的是
 * **运行态**（白盒流跑起来之后长什么样）。把它固化下来，任何人改完前端都能复现同一套验证。
 */

import { writeFileSync } from "node:fs";

const CDP = process.env.CDP || "http://127.0.0.1:9333";
const argv = process.argv.slice(2);
const url = argv[0];
const out = argv[1];
const opt = (name, dflt) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : dflt;
};
const goal = opt("goal", "");
const click = opt("click", "");
const waitMs = Number(opt("wait", 6000));
const width = Number(opt("w", 1680));
const height = Number(opt("h", 1000));

const list = await (await fetch(`${CDP}/json/list`)).json();
const page = list.find((t) => t.type === "page");
if (!page) throw new Error("没有可用的页面 target");

const ws = new WebSocket(page.webSocketDebuggerUrl);
let seq = 0;
const pending = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) {
    pending.get(m.id)(m);
    pending.delete(m.id);
  }
});
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
const send = (method, params = {}) =>
  new Promise((res) => {
    const id = ++seq;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", {
  width,
  height,
  deviceScaleFactor: 1,
  mobile: false,
});
await send("Page.navigate", { url });
await new Promise((r) => setTimeout(r, 4500)); // 等 React 挂载 + 首屏接口

if (goal) {
  // React 受控组件：必须走原生 setter + input 事件，直接改 value 不会被识别
  const expr = `(() => {
    const ta = document.querySelector('textarea');
    if (!ta) return 'no-textarea';
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, ${JSON.stringify(goal)});
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return 'goal-set';
  })()`;
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log("set goal:", r.result?.result?.value);
}

if (click) {
  const expr = `(() => {
    const b = [...document.querySelectorAll('button')].find(x => x.textContent.includes(${JSON.stringify(click)}));
    if (!b) return 'no-button';
    b.click();
    return 'clicked';
  })()`;
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log("click:", r.result?.result?.value);
}

await new Promise((r) => setTimeout(r, waitMs));
const shot = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(out, Buffer.from(shot.result.data, "base64"));
console.log("saved:", out);
ws.close();
