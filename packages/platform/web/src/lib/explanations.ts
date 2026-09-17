/** 小白可懂的术语解释与搜索结果说明。
 * 原则（frontend-design skill）：用用户语言命名，不堆术语。
 */

export interface KindMeta {
  label: string; // 中文类型名
  what: string; // 是什么（一句话）
  color: "info" | "vio" | "ok" | "warn" | "bad" | "dim";
}

export const KIND_META: Record<string, KindMeta> = {
  message: { label: "报文", what: "车上设备之间互发的一条 CAN 消息（如“心跳”“车门控制”），按固定周期在总线上传输。", color: "info" },
  signal: { label: "信号", what: "报文里携带的一个具体数值/状态，例如车门是“关/开/故障”。", color: "vio" },
  device: { label: "设备", what: "列车上的一个控制单元（如 VCU 主控、BCU 制动控制），负责收发报文。", color: "info" },
  function: { label: "被测功能", what: "把相关报文、信号、故障、需求聚合起来的一个“测试对象”，例如紧急制动管理。", color: "ok" },
  requirement: { label: "安全需求", what: "一条必须被满足的安全要求（如“任一原因触发即制动”），由测试用例验证。", color: "ok" },
  fault: { label: "故障", what: "一种可注入的异常（如“超速”“车门故障”），测试它时看系统如何处置。", color: "warn" },
  scenario: { label: "场景", what: "一段编排好的测试：何时注入什么故障、期望系统怎么反应、何时恢复。", color: "bad" },
  run: { label: "运行记录", what: "一次真实执行的留痕：跑了哪个场景、断言是否通过。", color: "dim" },
  // P6 领域知识节点
  mode: { label: "驾驶模式", what: "列车驾驶模式（全自动 FAM / 受控人工 CM / 限制人工 RM）。", color: "info" },
  state: { label: "状态", what: "安全功能状态机里的一个状态（如 EBM 的制动/缓解）。", color: "warn" },
  interlock: { label: "联锁规则", what: "一条“必须满足否则违规”的安全约束（如移动中不能开门）。", color: "bad" },
  threshold: { label: "关键阈值", what: "一个真实安全阈值（如超速 160km/h、制动缸 300kPa）。", color: "warn" },
  mechanism: { label: "安全机制", what: "一种安全实现机制（如 2oo3 表决、三重证据）。", color: "vio" },
  standard: { label: "标准", what: "引用的行业标准（EN 50128 / IEC 61508 等）。", color: "info" },
  hazard: { label: "安全危害", what: "一种需要被防止的危险场景（H-01 起）。", color: "bad" },
  concept: { label: "领域概念", what: "面向新人的概念解释（EBM / ATP / SIL 等）。", color: "ok" },
  system: { label: "列车系统", what: "列车系统分类框架（S1000D 思想对齐）：一个系统族（如制动/车门/网络），聚合它的故障与设备，让检索先定位到系统视角。", color: "dim" },
};

/** 给一条命中文档生成小白解释 */
export function plainExplain(docId: string, text: string): string {
  const [kind, key] = docId.split(":", 2);
  const meta = KIND_META[kind];
  if (!meta) return text.slice(0, 80);
  const k = key ?? "";
  switch (kind) {
    case "fault":
      return `这是一条「${meta.label}」：${text.split(" ")[0] ?? k}。匹配到它，通常表示系统里出现了与它描述的异常相关的测试证据。`;
    case "requirement":
      return `这是一条「${meta.label}」（${k}）：规定了列车某项安全行为必须成立。下面的覆盖描述说明由哪个模块和用例来保证它。`;
    case "scenario":
      return `这是一个「${meta.label}」：${k} —— 一段可真实执行的测试编排。你可以到「场景执行」页直接运行它。`;
    case "function":
      return `这是一个「${meta.label}」（${k}）：${text.split(" ").slice(1).join(" ") || "列车视角下的测试对象"}。`;
    case "message":
      return `这是一条「${meta.label}」（${k}）：${text}`;
    case "signal":
      return `这是一个「${meta.label}」（${k}）：${text}`;
    default:
      return `这是「${meta.label}」${k}：${text.slice(0, 60)}`;
  }
}

/** 术语词典（悬停/详情用） */
export const GLOSSARY: Record<string, string> = {
  DBC: "CAN 报文的“字典”，定义每条报文有哪些信号、范围、枚举含义。",
  FMEA: "故障模式与影响分析——一张表，登记每种故障怎么注入、怎么检测、怎么处置。",
  RTM: "需求追溯矩阵——把安全需求映射到实现模块和验证用例的表。",
  SIL: "安全完整性等级（0–4），越高代表越需要被严格保证的安全功能。",
  derate: "降级运行——检测到故障后限制列车性能（如限速）继续安全运行。",
  "emergency_brake": "紧急制动——最高级别的安全处置，立即停车。",
  EBM: "紧急制动管理——负责“何时触发、如何缓解”紧急制动的功能。",
  ATP: "超速防护——持续监督车速，超限时先警告再制动。",
};
