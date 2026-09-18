/**
 * 「本次运行用哪个模型」的状态与纯函数。
 *
 * 产品判断（为什么不直接改设置里的默认模型）：
 * 用户想试一个轻量模型 / 换一家厂商时，**不该顺手改掉自己的默认配置**——
 * 那会让"我平时用什么"和"我这次试什么"混在一起，事后也说不清哪次跑的。
 * 所以选择只作用于**本次运行**（随请求参数下发），并持久化在浏览器本地
 * （刷新页面还在，但不影响服务端设置）。
 *
 * 另一条纪律：**如实标注**。选择器只负责"请求用哪个"，
 * "这次实际用了哪个"必须来自后端回填的 llm 身份（见 api.AgentLlmIdentity），
 * 前端不猜、不美化。
 */

export type ModelChoice =
  | { mode: "follow" }
  | { mode: "pick"; id: string; base_url?: string | null };

const STORAGE_KEY = "tcms.modelChoice.v1";

/** 本地存储可用性（node 测试环境下没有 localStorage） */
function _ls(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // 隐私模式等场景下访问会抛
  }
}

/** 解析持久化的选择（坏数据一律回落"跟随设置"，不让它把页面搞崩） */
export function parseModelChoice(raw: string | null): ModelChoice {
  if (!raw) return { mode: "follow" };
  try {
    const o = JSON.parse(raw) as Partial<{ mode: string; id: string; base_url: string }>;
    if (o?.mode === "pick" && typeof o.id === "string" && o.id.trim()) {
      return { mode: "pick", id: o.id.trim(), base_url: o.base_url ?? null };
    }
  } catch {
    /* 坏数据 → 默认 */
  }
  return { mode: "follow" };
}

export function loadModelChoice(): ModelChoice {
  return parseModelChoice(_ls()?.getItem(STORAGE_KEY) ?? null);
}

export function saveModelChoice(c: ModelChoice): void {
  try {
    _ls()?.setItem(STORAGE_KEY, JSON.stringify(c));
  } catch {
    /* 存不进去不影响本次运行 */
  }
}

/** 转成请求参数：跟随设置 → 空对象（后端走统一解析链） */
export function choiceToParams(c: ModelChoice): { model?: string; base_url?: string } {
  if (c.mode !== "pick") return {};
  const out: { model?: string; base_url?: string } = { model: c.id };
  if (c.base_url) out.base_url = c.base_url;
  return out;
}

/** 选择器上显示的文字 */
export function choiceLabel(c: ModelChoice, followLabel: string): string {
  return c.mode === "pick" ? c.id : followLabel;
}

/** 是否与"跟随设置"不同（界面上用一个小圆点提示"本次已覆盖"） */
export function isOverridden(c: ModelChoice): boolean {
  return c.mode === "pick";
}
