/** 内联 SVG 图标（零依赖）。
 *
 * ## 为什么不用字符图标
 *
 * 此前全站用 Unicode 字符当图标：`▶`（跑场景）`⚙`（演示）`◈`（图谱）`✦`（Agent）
 * `◎`（说明条）`◌`（空态）`✓`（完成）。问题不在"字符不好看"，而在**它们不受控**：
 *
 * 1. **字形由系统字体决定** —— 同一份代码在 Windows / macOS / Linux 上渲出三种粗细与
 *    三种基线，甚至可能落到 emoji 字体（彩色、尺寸不一）；
 * 2. **来自不同 Unicode 区块**（几何图形 / 杂项符号 / 装饰符号），**stroke 粗细天然不一致**，
 *    放同一行里会显得"拼凑"；
 * 3. 无法统一控制**视觉尺寸与线条宽度**——而这两件事恰恰是"看起来专业"的主要来源。
 *
 * 内联 SVG 把尺寸、线宽、基线一次锁死，且**不引入任何依赖**（本仓前端零 icon 库）。
 * 全部图标共用一套规格：16 视框 / 1.5 线宽 / currentColor / 圆头圆角连接。
 *
 * ## 判定规则：什么时候该换成 SVG（新代码照此执行）
 *
 * **图标槽位 → 必须用本模块的 SVG，不得再写 Unicode 字符**：
 * 按钮/导航项的前导字形、`Callout` / `EmptyState` / `Panel` 的 `icon=` 属性、
 * 步骤与图例徽标、单选/复选指示、`✕` 关闭件。
 *
 * **正文标点 → 保留字符**：`→` `←` `✓` `✗` `△` 出现在句子、提示语或状态 Tag 的
 * 文字内容里时，它们是排版符号而非图标；硬换成 SVG 会让行内基线错位。
 * 口径只有一条：**它旁边有词吗？** 有词 → 标点；自己独占一个"图形位" → 图标。
 */

import type { ComponentType, SVGProps } from "react";

/** 统一规格：调用方只需传 className / style 调尺寸与颜色。 */
const BASE: SVGProps<SVGSVGElement> = {
  viewBox: "0 0 16 16",
  width: 16,
  height: 16,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": "true",
  focusable: "false",
};

type IconProps = SVGProps<SVGSVGElement>;

/** 执行 / 播放（跑场景） */
export function IconPlay(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M5.5 3.4v9.2l7.5-4.6z" />
    </svg>
  );
}

/** 故障演示（齿轮） */
export function IconGear(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="2.1" />
      <path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M12.4 3.6l-1.1 1.1M4.7 11.3l-1.1 1.1" />
    </svg>
  );
}

/** 知识图谱（节点与连线） */
export function IconGraph(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="1.7" />
      <circle cx="3" cy="3.6" r="1.4" />
      <circle cx="13" cy="4.4" r="1.4" />
      <circle cx="4.2" cy="12.6" r="1.4" />
      <path d="M6.6 6.9 4.1 4.7M9.4 7.1l2.3-1.7M6.9 9.3l-1.6 2.1" />
    </svg>
  );
}

/** Agent / 推荐入口（四角星） */
export function IconSpark(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 1.8c.5 2.9 1.6 4 4.5 4.5-2.9.5-4 1.6-4.5 4.5-.5-2.9-1.6-4-4.5-4.5 2.9-.5 4-1.6 4.5-4.5z" />
      <path d="M12.4 11.2c.2 1.1.6 1.5 1.7 1.7-1.1.2-1.5.6-1.7 1.7-.2-1.1-.6-1.5-1.7-1.7 1.1-.2 1.5-.6 1.7-1.7z" />
    </svg>
  );
}

/** 说明条（信息） */
export function IconInfo(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M8 7.2v4M8 5.1v.1" />
    </svg>
  );
}

/** 空态默认图标（虚线圆） */
export function IconEmpty(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="6" strokeDasharray="2.6 2.6" />
      <path d="M8 5.4v5.2M5.4 8h5.2" />
    </svg>
  );
}

/** 完成（勾） */
export function IconCheck(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M3.4 8.4l3 3 6.2-6.8" />
    </svg>
  );
}

/** 右箭头（用于 CTA 尾部与步骤条） */
export function IconArrow(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M3 8h9.4M9.1 4.6 12.9 8l-3.8 3.4" />
    </svg>
  );
}

/* ── 导航（每项一个独立隐喻：此前"故障演示"与"设置"共用 ⚙，读者无法靠形状区分） ── */

/** 总览（四格仪表盘） */
export function IconLayout(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="2.3" y="2.3" width="11.4" height="11.4" rx="2.1" />
      <path d="M2.3 8h11.4M8 2.3v11.4" />
    </svg>
  );
}

/** 测试资产（行式清单） */
export function IconList(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2.4 4.2h1.5M6.2 4.2h7.4M2.4 8h1.5M6.2 8h7.4M2.4 11.8h1.5M6.2 11.8h7.4" />
    </svg>
  );
}

/** 故障演示（信号随时间抖动：故障发生过程） */
export function IconWave(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M1.6 8.2h2.2l1.5-4.3 2.3 8.2 1.7-5.2 1.2 1.3h3.9" />
    </svg>
  );
}

/* ── 状态与动作 ── */

/** 测试连接 / 获取（闪电） */
export function IconBolt(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M9.1 1.9 3.6 8.9h3.6l-.7 5.2 5.5-7H8.4z" />
    </svg>
  );
}

/** 警告（三角感叹号） */
export function IconWarn(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 2.5 1.5 13.6h13z" />
      <path d="M8 6.3v3.4M8 11.9v.1" />
    </svg>
  );
}

/** 关闭 / 清除 */
export function IconClose(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M4.2 4.2 11.8 11.8M11.8 4.2 4.2 11.8" />
    </svg>
  );
}

/** 暂停（停转） */
export function IconPause(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M6.1 3.6v8.8M9.9 3.6v8.8" />
    </svg>
  );
}

/** 重播 */
export function IconReplay(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M3.1 8a4.9 4.9 0 1 1 1.5 3.5" />
      <path d="M2.9 12.5V9.6h2.9" />
    </svg>
  );
}

/** 适配视野（四角外扩框） */
export function IconExpand(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2.4 6.1V2.4h3.7M9.9 2.4h3.7v3.7M13.6 9.9v3.7H9.9M6.1 13.6H2.4V9.9" />
    </svg>
  );
}

/** 单选：选中（实心点） */
export function IconDot(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="3.1" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** 单选：未选（空心圈） */
export function IconRing(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="4.7" />
    </svg>
  );
}

/** 结论 / 重点（星） */
export function IconStar(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 2.1l1.8 3.7 4.1.6-3 2.9.7 4.1L8 11.4l-3.6 2 .7-4.1-3-2.9 4.1-.6z" />
    </svg>
  );
}

/** 折叠指示（右侧尖角，配 rotate-90 表示展开） */
export function IconCaret(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M6.1 3.7 10.4 8l-4.3 4.3" />
    </svg>
  );
}

/* ── 视图模式（成对语义：平面 = 方框，立体 = 立方体） ── */

/** 2D 平面视图 */
export function IconFlat(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="2.6" y="2.6" width="10.8" height="10.8" rx="1.8" />
    </svg>
  );
}

/** 3D 立体视图 */
export function IconCube(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 1.9 14 5.1v5.8L8 14.1 2 10.9V5.1z" />
      <path d="M2 5.1 8 8.3l6-3.2M8 8.3v5.8" />
    </svg>
  );
}

/* ── 第二组：白盒轨迹与联锁流程用到的语义（原先是 ⌖ ⌕ ↺ ⛔ ◇ ⧉ ⟲ ⇄ ↩ 与 emoji） ── */

/** 检索 / 查找（放大镜） */
export function IconSearch(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="7.1" cy="7.1" r="4.6" />
      <path d="M10.5 10.5 14.2 14.2" />
    </svg>
  );
}

/** 解析 / 锚点定位（准星） */
export function IconTarget(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="5.5" />
      <circle cx="8" cy="8" r="1.3" />
      <path d="M8 1.5v2.9M8 11.6v2.9M1.5 8h2.9M11.6 8h2.9" />
    </svg>
  );
}

/** 回顾 / 回到开头（逆时针回转，与 IconReplay 成对） */
export function IconRewind(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M12.9 8a4.9 4.9 0 1 0-1.5 3.5" />
      <path d="M13.1 12.5V9.6H10.2" />
    </svg>
  );
}

/** 处置 / 防护（盾）：故障被"处置"是保护动作，不用 ⛔（那是"禁止"，语义相反） */
export function IconShield(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 1.9 13.4 4.1v4.1c0 3-2.2 5.1-5.4 6-3.2-.9-5.4-3-5.4-6V4.1z" />
    </svg>
  );
}

/** 组合 / 叠加（两层） */
export function IconLayers(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="2.3" y="2.3" width="8.4" height="8.4" rx="1.7" />
      <path d="M5.3 13.7h6.7a1.7 1.7 0 0 0 1.7-1.7V5.3" />
    </svg>
  );
}

/** 交换 / 双向（⇄） */
export function IconSwap(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2.6 5.6h9.2M9.2 3.1l2.6 2.5-2.6 2.5" />
      <path d="M13.4 10.4H4.2M6.8 7.9 4.2 10.4l2.6 2.5" />
    </svg>
  );
}

/** 密钥 / 凭据（原为 emoji 🔒：彩色且不可控） */
export function IconLock(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="3.2" y="7" width="9.6" height="7" rx="1.8" />
      <path d="M5.6 7V5.4a2.4 2.4 0 0 1 4.8 0V7" />
    </svg>
  );
}

/** 列车（品牌标识，原为 emoji 🚄） */
export function IconTrain(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="3.7" y="2.1" width="8.6" height="9.4" rx="2" />
      <path d="M3.7 7.3h8.6M6.2 11.5 4.7 13.8M9.8 11.5l1.5 2.3" />
      <path d="M6.5 4.8h3" />
    </svg>
  );
}

/* ===== 实体类型字形（2026-10）==================================================
 *
 * 为什么要有这一套：图谱的类型此前**只有色点**——17 种类型挤在一行色点里，
 * 除了"颜色不同"读不出任何信息；类型名要靠文字逐个读。给每类一枚几何符号后，
 * "这是什么类型"变成可扫读的，而且**同一套符号在图例 / 类型计数 / 命中卡里重复出现**，
 * 与画布节点的对应关系仍然由颜色承担（图例里符号与色点同色）。
 *
 * 造型取向：全部由直线与圆构成、1.5 线宽、16 视框内留 2px 余量，
 * 取"列车控制"的语汇——帧、方波、ECU 引脚、表决三通道、量程标尺、联锁环。
 * 规格与其它图标完全一致（见文件头），因此可以和导航图标并排使用而不显拼凑。
 * ========================================================================== */

/** 报文：总线上的一帧 */
export function IconKindMessage(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2 12h12" />
      <rect x="3" y="4.5" width="10" height="4.5" rx="1" />
    </svg>
  );
}

/** 信号：方波 */
export function IconKindSignal(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2 9.5h2.6V6.5h3.2v3h3.2V6.5H14" />
    </svg>
  );
}

/** 设备：控制单元与引脚 */
export function IconKindDevice(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="3" y="3.4" width="10" height="7.4" rx="1.2" />
      <path d="M5.4 10.8v2.4M8 10.8v2.4M10.6 10.8v2.4" />
    </svg>
  );
}

/** 被测功能：准星 */
export function IconKindFunction(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="8" cy="8" r="4" />
      <path d="M8 1.8v2.2M8 12v2.2M1.8 8h2.2M12 8h2.2" />
    </svg>
  );
}

/** 安全需求：带勾的规格页 */
export function IconKindRequirement(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M4.2 2.6h5.2l3.2 3.2v7.6H4.2z" />
      <path d="M9.4 2.6v3.2h3.2" />
      <path d="M6.3 10.2l1.4 1.4 2.6-2.9" />
    </svg>
  );
}

/** 故障：基线出现尖峰 */
export function IconKindFault(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M1.8 8.4h2.6l1.4-3.6 2.2 7 1.4-3.4h4.8" />
    </svg>
  );
}

/** 场景：编排好的时序 */
export function IconKindScenario(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2.2 12.8h11.6" />
      <path d="M4.8 12.8V7.6M8 12.8V4.6M11.2 12.8V9.4" />
    </svg>
  );
}

/** 运行记录：带条目的日志 */
export function IconKindRun(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M4.6 4.4h8.8M4.6 8h8.8M4.6 11.6h5.2" />
      <path d="M2.2 4.4h.02M2.2 8h.02M2.2 11.6h.02" />
    </svg>
  );
}

/** 驾驶模式：带球头的控制杆 */
export function IconKindMode(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M3.4 13.2h9.2" />
      <path d="M8 13.2V8.4" />
      <circle cx="8" cy="5.6" r="2.4" />
    </svg>
  );
}

/** 状态：状态机两节点 */
export function IconKindState(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="3.9" cy="8" r="2.1" />
      <circle cx="12.1" cy="8" r="2.1" />
      <path d="M6.2 8h3.6" />
    </svg>
  );
}

/** 联锁规则：互锁环 */
export function IconKindInterlock(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="2.4" y="6.2" width="6.4" height="3.6" rx="1.8" />
      <rect x="7.2" y="6.2" width="6.4" height="3.6" rx="1.8" />
    </svg>
  );
}

/** 关键阈值：量程上的限值 */
export function IconKindThreshold(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M2 12.4h12" />
      <path d="M8.4 3.2v9.2" strokeDasharray="2 1.5" />
      <path d="M6.6 5.6h3.6" />
    </svg>
  );
}

/** 安全机制：三通道表决 */
export function IconKindMechanism(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <circle cx="4" cy="10.6" r="1.7" />
      <circle cx="8" cy="10.6" r="1.7" />
      <circle cx="12" cy="10.6" r="1.7" />
      <path d="M4 7.4V5.2h8v2.2" />
    </svg>
  );
}

/** 标准：规范书 */
export function IconKindStandard(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M3 3.6h4.2v9H3zM8.8 3.6H13v9H8.8z" />
    </svg>
  );
}

/** 安全危害：警示三角 */
export function IconKindHazard(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 2.8 14 13H2z" />
      <path d="M8 6.8v2.6M8 11.2h.02" />
    </svg>
  );
}

/** 领域概念：认识一件事 */
export function IconKindConcept(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M8 2.6a3.5 3.5 0 0 1 2.1 6.3v1.3H5.9V8.9A3.5 3.5 0 0 1 8 2.6z" />
      <path d="M6.7 12.6h2.6" />
    </svg>
  );
}

/** 列车系统：系统树 */
export function IconKindSystem(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <rect x="6" y="2.6" width="4" height="3.2" rx=".8" />
      <rect x="2.4" y="10.2" width="4" height="3.2" rx=".8" />
      <rect x="9.6" y="10.2" width="4" height="3.2" rx=".8" />
      <path d="M8 5.8v2.4M4.4 10.2V8.2h7.2v2" />
    </svg>
  );
}

/** 现象：观察到的异常表现 */
export function IconKindSymptom(p: IconProps) {
  return (
    <svg {...BASE} {...p}>
      <path d="M1.9 8S4.3 4.6 8 4.6 14.1 8 14.1 8 11.7 11.4 8 11.4 1.9 8 1.9 8z" />
      <circle cx="8" cy="8" r="1.6" />
    </svg>
  );
}

/** kind → 字形（与 lib/explanations.ts 的 KIND_META 同键；未收录的类型由调用方兜底）。 */
export const KIND_GLYPH: Record<string, ComponentType<IconProps>> = {
  message: IconKindMessage,
  signal: IconKindSignal,
  device: IconKindDevice,
  function: IconKindFunction,
  requirement: IconKindRequirement,
  fault: IconKindFault,
  scenario: IconKindScenario,
  run: IconKindRun,
  mode: IconKindMode,
  state: IconKindState,
  interlock: IconKindInterlock,
  threshold: IconKindThreshold,
  mechanism: IconKindMechanism,
  standard: IconKindStandard,
  hazard: IconKindHazard,
  concept: IconKindConcept,
  system: IconKindSystem,
  symptom: IconKindSymptom,
};