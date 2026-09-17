import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type FaultLabCurvePoint, type FaultLabEvent, type FaultLabResp } from "../api";
import { Panel, Tag, EmptyState, SkeletonRows } from "../components/ui";
import { advancePlayback, activeSpanKeys, buildSpans, eventWindow } from "../lib/playerState";

/* ================= t4 资产化：任意序列 / 跳转演示初始化 =================
 * 通道契约（与 fe-jump t5 写入端对齐，2026-09 第三轮 ③）：
 *  - 内置场景直连：/faultlab?scenario=<file>[&from=scenario-exec|agent-exec]
 *  - 任意步骤序列：sessionStorage 键 tcms.faultlab.draft
 *      { name?, from?: "scenario-exec"|"agent-exec",
 *        steps: [{ at, action, fault, node, level, expect, impact }] }
 *    与 POST /api/faultlab/demo-steps body 同构（faultlabDemoSteps 后端合入前用本地 fetch）。
 *    写端写入后 navigate("/faultlab")，读端 onMount 一次性消费并清除 → 资产组合渲染。
 *  - 来源 Tag：from=scenario-exec →「来自场景执行」；agent-exec →「来自 Agent 执行」。
 *    用户手动重新加载（下拉/演示按钮）后来源态清空，不粘滞。
 * ------------------------------------------------------------------ */
type FaultLabStepPayload = {
  at: number;
  action: "inject" | "recover";
  fault?: string | null;
  node?: string | null;
  level?: string | null;
  expect?: string | null;
  impact?: string | null;
};
type FaultLabDemoStepsBody = { name?: string; steps: FaultLabStepPayload[] };
type FaultLabDemoStepsFn = (body: FaultLabDemoStepsBody) => Promise<FaultLabResp>;
type DraftSource = "scenario-exec" | "agent-exec";
type DraftPayload = { name?: string; from?: DraftSource; steps: FaultLabStepPayload[] };
const DRAFT_KEY = "tcms.faultlab.draft";

/** demo-steps 本地 fetch 兜底：api.faultlabDemoSteps 由后端契约接线（TODO(t2)）后删除。 */
const demoStepsLocal = async (body: FaultLabDemoStepsBody): Promise<FaultLabResp> => {
  const fn = (api as unknown as { faultlabDemoSteps?: FaultLabDemoStepsFn }).faultlabDemoSteps;
  if (fn) return fn(body);
  const r = await fetch("/api/faultlab/demo-steps", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json() as Promise<FaultLabResp>;
};

/** 读取 + 清除共享演示请求通道（t4：跳转/演示初始化）。返回 null = 无待消费请求。 */
const takeDraft = (): { name?: string; from?: DraftSource; steps: FaultLabStepPayload[] } | null => {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(DRAFT_KEY);
    const o = JSON.parse(raw) as Partial<DraftPayload>;
    if (!Array.isArray(o.steps) || !o.steps.length) return null;
    return { name: o.name || undefined, from: o.from, steps: o.steps };
  } catch {
    return null;
  }
};

/** 归一化步骤：容忍字段缺省/null，确保 {at, action} 合法可发给 demo-steps。 */
const normStep = (x: unknown): FaultLabStepPayload | null => {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  if (typeof o.at !== "number" || (o.action !== "inject" && o.action !== "recover")) return null;
  const s: FaultLabStepPayload = { at: o.at, action: o.action };
  if (o.fault != null) s.fault = String(o.fault);
  if (o.node != null) s.node = String(o.node);
  if (o.level != null) s.level = String(o.level);
  if (o.expect != null) s.expect = String(o.expect);
  if (o.impact != null) s.impact = String(o.impact);
  return s;
};

const KIND_COLOR: Record<string, string> = {
  inject: "var(--warn)",
  detect: "var(--info)",
  action: "var(--bad)",
  recover: "var(--ok)",
  note: "var(--ink-dim)",
};
const KIND_LABEL: Record<string, string> = {
  inject: "注入",
  detect: "检测",
  action: "处置",
  recover: "恢复",
  note: "说明",
};

/* ---------- t2 扩展字段（本地约定：后端合入前优雅降级） ----------
 * faultlab.py 计划输出：事件 e.source{kind,ref,desc}；demo.engine{asserted,version,
 * trace,assertions,notes}；demo.pipeline{steps,constants}。字段缺失时隐藏对应区块，
 * 不外露「道歉式」诚实标注。 */
type FaultEventSource = { kind?: string; ref?: string; desc?: string };
type FaultLabEventEx = FaultLabEvent & { source?: FaultEventSource };
type FaultLabEngineEx = {
  asserted?: boolean;
  version?: string | null;
  trace?: unknown;
  assertions?: { fault?: string; ts?: number; expected?: string; actual?: string; passed?: boolean }[];
  notes?: string[];
};
type FaultLabConstRow = { name?: string; value?: string | number; unit?: string; source?: string; desc?: string };
type FaultLabPipelineEx = {
  title?: string;
  steps?: { kind?: string; name?: string; label?: string; desc?: string }[];
  constants?: { real?: FaultLabConstRow[]; schematic?: FaultLabConstRow[] };
};
type FaultLabDemoEx = Omit<FaultLabResp["demo"], "events"> & {
  engine?: FaultLabEngineEx;
  pipeline?: FaultLabPipelineEx;
  events: FaultLabEventEx[];
};
type FaultLabRespEx = Omit<FaultLabResp, "demo"> & { demo: FaultLabDemoEx };

/* 来源标注（替代「示意」标签：数据从哪来，而不是在道歉） */
const SOURCE_META: Record<string, { label: string; color: string }> = {
  scenario_yaml: { label: "场景 YAML", color: "var(--info)" },
  fault_dict: { label: "故障字典", color: "var(--vio)" },
  engine_assert: { label: "引擎断言", color: "var(--ok)" },
  derived_phys: { label: "示意模型", color: "var(--ink-dim)" },
  note: { label: "备注", color: "var(--ink-dim)" },
};
/* 数据管线步骤（t2 schema：kind ∈ asset / engine / model） */
const STEP_META: Record<string, { label: string; color: string }> = {
  asset: { label: "真实资产", color: "var(--info)" },
  engine: { label: "真实引擎", color: "var(--ok)" },
  model: { label: "示意模型", color: "var(--ink-dim)" },
};

/* 故障 → 列车部位高亮锚点 + 中文名（在 520×150 的 SVG 视口内）
 * t4：22 个故障键全覆盖（对照 tcms/faults.yaml），每个键有专属部位与标签；
 * 同族故障沿对应部件错开 ≥20px，多故障同时激活时不重叠、仍可读。
 * 布局参考：驾驶室/鼻锥 x42-78 · 车窗 y52-72 · 总线干线 y82(x88-432) ·
 * 车门 y92-110 · 车底电池 y121 · 转向架 y112 · 受电弓 y10-38 · 天线 y21。
 */
type SpotTone = "red" | "amber";
const SPOT_RED = "#f4645a";
const SPOT_AMBER = "#f5b84c";
const FAULT_SPOT_META: Record<string, { zh: string; tone: SpotTone; anchor?: [number, number] }> = {
  overspeed: { zh: "超速", tone: "red", anchor: [52, 62] }, // 驾驶室 / ATP 速度监督
  traction_loss: { zh: "牵引丢失", tone: "amber", anchor: [150, 112] }, // 牵引转向架
  door_fault: { zh: "车门故障", tone: "red" }, // 门本身红框闪烁（不重复画点）
  door_sensor_noise: { zh: "门传感器噪声", tone: "amber", anchor: [297, 101] }, // 2 号门门磁传感器
  eb_failure: { zh: "紧急制动执行失败", tone: "red", anchor: [206, 112] }, // 前制动轴 / EBR 回路
  brake_actuator_stuck: { zh: "制动执行器卡滞", tone: "amber", anchor: [330, 112] }, // 后制动轴执行器
  traction_brake_conflict: { zh: "牵引制动冲突", tone: "red", anchor: [70, 72] }, // 驾驶室手柄联锁
  pantograph_arc: { zh: "受电弓拉弧", tone: "red", anchor: [380, 10] }, // 弓头（降弓点）
  soc_low: { zh: "SOC 偏低", tone: "amber", anchor: [246, 121] }, // 车底动力电池前段
  temp_high: { zh: "电池温度偏高", tone: "amber", anchor: [276, 121] }, // 车底动力电池后段（热管理）
  heartbeat_loss_vcu: { zh: "VCU 心跳丢失", tone: "red", anchor: [220, 22] }, // 车顶通信天线
  node_restart_storm: { zh: "节点重启风暴", tone: "red", anchor: [368, 24] }, // 车顶中继节点箱
  crc_error_frame: { zh: "CRC 校验错误", tone: "amber", anchor: [236, 82] }, // 总线干线前段
  bit_flip_frame: { zh: "位翻转丢帧", tone: "amber", anchor: [316, 82] }, // 总线干线中段
  rolling_counter_gap: { zh: "计数器跳变", tone: "amber", anchor: [356, 82] }, // 总线干线中后段
  bus_short: { zh: "总线短路", tone: "red", anchor: [300, 82] }, // 总线干线中央（短路点）
  bus_open_circuit: { zh: "总线断路", tone: "red", anchor: [172, 82] }, // 总线干线前段（断点）
  bus_noise_burst: { zh: "总线噪声突发", tone: "amber", anchor: [404, 82] }, // 总线干线末段
  arbitration_error: { zh: "仲裁错误", tone: "amber", anchor: [140, 82] }, // 总线干线起点
  short_frame: { zh: "短帧 DLC 不足", tone: "amber", anchor: [264, 82] }, // 总线干线前中段
  speed_sensor_drift: { zh: "速度传感器漂移", tone: "amber", anchor: [320, 105] }, // 后轮速度传感器
  sensor_stuck: { zh: "传感器卡死", tone: "amber", anchor: [94, 58] }, // 前部传感器舱
};
const FALLBACK_SPOT = { zh: "系统异常", tone: "amber" as SpotTone, anchor: [260, 55] as [number, number] };

interface FaultSpot {
  key: string;
  zh: string; // 具体异常名（事件 fault_name / 部位表中文 / 兜底，不再是"系统异常"）
  tone: SpotTone;
  anchor?: [number, number];
  where?: string; // 哪里：13 系统域 · 故障字典子系统
  level?: string; // 等级（info~critical）
  detail?: string; // 什么异常的现象/检测说明
}

/** 从事件里取该故障注入时刻的具体异常上下文（故障名/哪里/等级/现象） */
function spotFromEvent(
  e: FaultLabEvent | undefined,
  fallback: { zh: string; tone: SpotTone; anchor?: [number, number] }
): { zh: string; where: string; level: string; detail: string } {
  const zh = e?.fault_name || fallback.zh;
  const where = [e?.domain_zh, e?.subsystem].filter(Boolean).join(" · ");
  return { zh, where, level: e?.level ?? "", detail: e?.detail ?? "" };
}

function nearestPoint(curve: FaultLabCurvePoint[], t: number): FaultLabCurvePoint {
  let best = curve[0];
  for (const p of curve) {
    if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  }
  return best;
}

/** 脉冲圆：定位到具体故障部位（CSS 环形扩散，克制且语义化）；
 *  鼠标悬停/点击显示具体异常说明（哪里+什么）。 */
function PulseDot({ x, y, color, spot, onSelect }: { x: number; y: number; color: string; spot: FaultSpot; onSelect?: (key: string) => void }) {
  const title = `${spot.where ? `【${spot.where}】` : ""}${spot.zh}${spot.detail ? `：${spot.detail.slice(0, 60)}${spot.detail.length > 60 ? "…" : ""}` : ""}`;
  return (
    <g
      aria-label={title}
      style={{ cursor: onSelect ? "pointer" : "default" }}
      onClick={(ev) => {
        ev.stopPropagation();
        onSelect?.(spot.key);
      }}
    >
      <title>{title}</title>
      <circle cx={x} cy={y} r="7" fill="transparent" />
      <circle cx={x} cy={y} r="5" fill={color} opacity="0.9" className="pulse-dot" />
      <circle cx={x} cy={y} r="6" fill="none" stroke={color} strokeWidth="1.6" className="fault-ring" />
    </g>
  );
}

function SpotChip({ spot, active, onSelect }: { spot: FaultSpot; active: boolean; onSelect: (key: string) => void }) {
  const red = spot.tone === "red";
  const cls = red ? "text-bad border-bad/40 bg-bad/10" : "text-warn border-warn/40 bg-warn/10";
  const dot = red ? "bg-bad" : "bg-warn";
  const label = `${spot.where ? `【${spot.where}】` : ""}${spot.zh}`;
  const title = spot.detail ? `${label}：${spot.detail}` : label;
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium cursor-pointer transition-opacity ${cls} ${active ? "ring-1 ring-current" : "hover:opacity-85"}`}
      role="status"
      onClick={() => onSelect(spot.key)}
      title={`点击查看异常说明：${title}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full pulse-dot ${dot}`} />
      高亮：{spot.zh}
      {spot.where && <span className="opacity-75">· {spot.where}</span>}
    </button>
  );
}

/**
 * 列车侧视动画 + 驾驶台：
 * - SVG 列车（车体/车窗/驾驶室/4 车门/受电弓/轮子/EB光带 + 总线干线/通信天线锚点）
 * - 故障部位高亮：spots 命中 key 时在锚点画脉冲环，坏门红框闪烁
 * - 驾驶台仪表条：当前速度大字 + 限速、心跳/总线/受电弓/紧急制动通道、故障 chip 灯带
 */
function TrainGlyph({
  pt,
  derateSpeed,
  limitKmh,
  spots,
  selKey,
  onSpotSelect,
}: {
  pt: FaultLabCurvePoint;
  derateSpeed: number;
  limitKmh: number;
  spots: FaultSpot[];
  selKey?: string | null;
  onSpotSelect?: (key: string) => void;
}) {
  const moving = pt.speed_kmh > 0.5;
  const ebActive = pt.eb === 1;
  const derate = pt.action === "derate";
  const shutdown = pt.action === "shutdown";
  const alarmOn = pt.alarms.length > 0 || spots.length > 0;
  const overLimit = pt.speed_kmh > limitKmh;
  const speedCls = overLimit ? "text-bad" : derate ? "text-warn" : "text-ink";
  return (
    <div className="relative select-none">
      <svg width="100%" viewBox="0 0 520 150" style={{ display: "block", background: "#0a1120", borderRadius: 12 }} role="img" aria-label="列车状态示意（故障部位高亮）">
        {/* 轨道 */}
        <line x1="0" y1="132" x2="520" y2="132" stroke="#233152" strokeWidth="3" />
        <line x1="0" y1="137" x2="520" y2="137" stroke="#1a2540" strokeWidth="1" />
        {Array.from({ length: 14 }).map((_, i) => (
          <line key={i} x1={i * 40} y1="132" x2={i * 40 - 6} y2="137" stroke="#1a2540" strokeWidth="2" />
        ))}
        {/* 总线干线（车窗与门之间的水平总线；短路/断线时红闪） */}
        <line x1="88" y1="82" x2="432" y2="82" stroke={pt.bus_ok ? "#1f2c4a" : SPOT_RED} strokeWidth="1.6" className={pt.bus_ok ? undefined : "fault-blink"} />
        {/* 通信天线（VCU/心跳的视觉锚点） */}
        <g>
          <line x1="220" y1="38" x2="220" y2="25" stroke="#233152" strokeWidth="2" />
          <circle cx="220" cy="21" r="2.6" fill={pt.heartbeat_ok ? "#2dd4a0" : SPOT_RED} className={pt.heartbeat_ok ? undefined : "pulse-dot"} />
        </g>
        {/* 车体（含受电弓状态） */}
        <g>
          <rect x="60" y="38" width="400" height="72" rx="8" fill="#131c33" stroke={shutdown ? "#f4645a" : alarmOn ? "#f5b84c" : "#233152"} strokeWidth={2} />
          {/* 车窗 */}
          {[100, 160, 220, 280, 340, 400].map((x) => (
            <rect key={x} x={x} y="52" width="34" height="20" rx="4" fill="#0a1120" stroke="#233152" />
          ))}
          {/* 驾驶室 */}
          <path d="M60 46 q -18 6 -18 26 v 24 h 18 z" fill="#0d1424" stroke="#233152" />
          {/* 车门状态（4 门，故障=红框闪烁，正常=绿点） */}
          {[0, 1, 2, 3].map((i) => {
            const x = 110 + i * 80;
            const bad = pt.door_fault_count > i;
            return (
              <g key={i}>
                <rect x={x + 14} y="92" width="26" height="18" rx="3" fill={bad ? "#f4645a" : "#16203a"} stroke={bad ? "#f4645a" : "#233152"} className={bad ? "fault-blink" : undefined} />
                <circle cx={x + 27} cy="101" r="2.5" fill={bad ? "#fff" : "#2dd4a0"} />
              </g>
            );
          })}
          {/* 受电弓 */}
          <g>
            <line x1="340" y1="38" x2="352" y2="16" stroke="#8ca0c0" strokeWidth="3" />
            <line x1="420" y1="38" x2="408" y2="16" stroke="#8ca0c0" strokeWidth="3" />
            <line x1="352" y1="16" x2="408" y2="16" stroke={pt.pantograph_ok ? "#8ca0c0" : SPOT_RED} strokeWidth="3.5" className={pt.pantograph_ok ? undefined : "fault-blink"} />
            <line x1="430" y1="38" x2="430" y2="16" stroke="#233152" strokeWidth="3" />
            <circle cx={pt.pantograph_ok ? 380 : 430} cy="10" r="4" fill={pt.pantograph_ok ? "#2dd4a0" : "#f4645a"} />
          </g>
        </g>
        {/* 轮子 + 运动效果 */}
        {[120, 200, 320, 400].map((x) => (
          <g key={x} className={moving ? "wheel-spin" : undefined}>
            <circle cx={x} cy="122" r="11" fill="#0d1424" stroke="#233152" strokeWidth="2.5" />
            <line x1={x} y1="122" x2={x + (moving ? 7 : 0)} y2={moving ? 117 : 122} stroke="#4ca6ff" strokeWidth="2" style={{ transformOrigin: `${x}px 122px` }} />
          </g>
        ))}
        {/* 速度尾迹 */}
        {moving &&
          Array.from({ length: 4 }).map((_, i) => (
            <circle key={i} cx={48 - i * 14} cy={84 + (i % 2) * 8} r={3 - i * 0.5} fill="#4ca6ff" opacity={0.5 - i * 0.1} />
          ))}
        {/* 故障部位脉冲环（每个激活故障一个锚点；door_fault 由门框闪烁表达，不重复画） */}
        {spots.map((s) =>
          s.anchor && s.key !== "door_fault" ? (
            <PulseDot key={s.key} x={s.anchor[0]} y={s.anchor[1]} color={s.tone === "red" ? SPOT_RED : SPOT_AMBER} spot={s} onSelect={onSpotSelect} />
          ) : null
        )}
        {/* EB 光带 */}
        {ebActive && (
          <>
            <rect x="60" y="30" width="400" height="4" rx="2" fill="#f4645a" className="pulse-glow" />
            <text x="260" y="25" textAnchor="middle" fill="#f4645a" fontSize="13" fontWeight="700">
              ⚠ 紧急制动请求
            </text>
          </>
        )}
        {derate && !ebActive && (
          <text x="260" y="25" textAnchor="middle" fill="#f5b84c" fontSize="12">
            降级运行（限速 {derateSpeed} km/h）
          </text>
        )}
        {overLimit && (
          <text x="260" y="46" textAnchor="middle" fill={SPOT_RED} fontSize="11" fontWeight="700" className="fault-blink">
            超速 {pt.speed_kmh.toFixed(0)} &gt; 限速 {limitKmh}
          </text>
        )}
      </svg>

      {/* 故障部位 chip 灯带（当前激活故障，中文标注；点击查看异常说明） */}
      {spots.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2">
          {spots.map((s) => (
            <SpotChip key={s.key} spot={s} active={selKey === s.key} onSelect={(k) => onSpotSelect?.(k)} />
          ))}
        </div>
      )}

      {/* 选中故障的异常说明（哪里异常 + 什么异常，真实字典字段；点击任意高亮点/chip 查看） */}
      {selKey &&
        (() => {
          const s = spots.find((x) => x.key === selKey);
          if (!s) return null;
          const red = s.tone === "red";
          return (
            <div role="alert" className={`mt-1.5 rounded-md border px-3 py-2 text-[12.5px] leading-5 ${red ? "border-bad/50 bg-bad/10" : "border-warn/50 bg-warn/10"}`}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className={red ? "text-bad" : "text-warn"}>{red ? "⚠" : "▲"}</span>
                <span className="text-ink font-semibold">
                  {s.where ? `【${s.where}】` : ""}出现 {s.zh}
                </span>
                {s.level && <span className="tag text-ink-dim border-line bg-surface/60">{s.level}</span>}
              </div>
              {s.detail && <div className="mt-0.5 text-ink-dim">说明：{s.detail}</div>}
              <div className="mt-0.5 text-[10.5px] text-ink-faint">“哪里”= 13 系统域 · 故障字典子系统；“什么”= 真实故障名与现象（点击其它高亮点切换）。</div>
            </div>
          );
        })()}

      {/* 驾驶台仪表条 */}
      <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-1.5">
        <div className="panel px-2.5 py-1.5 bg-surface-2/40 col-span-2 sm:col-span-3 md:col-span-1 flex flex-col justify-center" role="group" aria-label="驾驶台速度表">
          <div className="text-[10px] text-ink-faint">当前速度</div>
          <div className="flex items-baseline gap-1 leading-none">
            <span className={`num text-[26px] font-bold ${overLimit ? "fault-blink" : ""} ${speedCls}`}>
              {pt.speed_kmh.toFixed(0)}
            </span>
            <span className="text-[10px] text-ink-dim">km/h</span>
          </div>
          <div className="text-[9.5px] text-ink-faint num mt-0.5">
            {derate ? `降级限速 ${derateSpeed}` : `线路限速 ${limitKmh}`} · 缸压 {pt.brake_kpa.toFixed(0)} kPa
          </div>
        </div>
        <MiniStat label="心跳" ok={pt.heartbeat_ok} text={pt.heartbeat_ok ? "正常" : "丢失"} />
        <MiniStat label="总线" ok={pt.bus_ok} text={pt.bus_ok ? "正常" : "异常"} />
        <MiniStat label="受电弓" ok={pt.pantograph_ok} text={pt.pantograph_ok ? "正常" : "故障"} />
        <MiniStat
          label="紧急制动"
          ok={pt.eb !== 1}
          text={pt.eb === 1 ? (pt.brake_kpa > 0 ? `已施加 ${pt.brake_kpa.toFixed(0)} kPa` : "命令未落地 · 缸压 0") : "未触发"}
          tone={pt.brake_kpa > 0 || pt.eb === 1 ? "bad" : "ok"}
        />
      </div>
    </div>
  );
}

function MiniStat({ label, ok, text, tone }: { label: string; ok?: boolean; text: string; tone?: "ok" | "bad" }) {
  const cls = tone === "bad" || (!tone && !ok) ? "text-bad" : tone === "ok" || ok ? "text-ok" : "text-bad";
  return (
    <div className="panel px-1 py-1.5 bg-surface-2/40 min-w-0">
      <div className="text-[10px] text-ink-faint">{label}</div>
      <div className={`text-[11px] font-semibold truncate ${cls}`}>{text}</div>
    </div>
  );
}

function Curves({ curve, t, limitKmh }: { curve: FaultLabCurvePoint[]; t: number; limitKmh: number }) {
  const W = 640;
  const H = 130;
  const maxT = curve.length ? curve[curve.length - 1].t : 1;
  const x = (tt: number) => 30 + (tt / maxT) * (W - 50);
  const ySpeed = (v: number) => H - 12 - (v / 180) * (H - 34);
  const yKpa = (v: number) => H - 12 - (v / 400) * (H - 34);
  const speedPath = curve.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${ySpeed(p.speed_kmh).toFixed(1)}`).join(" ");
  const kpaPath = curve.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${yKpa(p.brake_kpa).toFixed(1)}`).join(" ");
  const cur = nearestPoint(curve, t);
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block", background: "#0a1120", borderRadius: 12 }}>
      <text x={8} y={16} fill="#8ca0c0" fontSize="10">km/h</text>
      {[0, 60, 120, 180].map((v) => (
        <g key={v}>
          <line x1="28" y1={ySpeed(v)} x2={W - 14} y2={ySpeed(v)} stroke="#1a2540" strokeWidth="0.6" />
          <text x={2} y={ySpeed(v) + 3} fill="#5d6f8f" fontSize="9">{v}</text>
        </g>
      ))}
      {/* 限速参考线（后端真实阈值） */}
      <line x1="28" y1={ySpeed(limitKmh)} x2={W - 14} y2={ySpeed(limitKmh)} stroke="#f5b84c" strokeWidth="1" strokeDasharray="4 3" />
      <text x={W - 60} y={ySpeed(limitKmh) - 3} fill="#f5b84c" fontSize="9">{limitKmh} 限速</text>
      <path d={speedPath} fill="none" stroke="#4ca6ff" strokeWidth="2" />
      <path d={kpaPath} fill="none" stroke="#2dd4a0" strokeWidth="1.6" />
      {/* 游标 */}
      <line x1={x(t)} y1="6" x2={x(t)} y2={H - 6} stroke="#e8eef9" strokeWidth="1.4" opacity="0.7" />
      <circle cx={x(t)} cy={ySpeed(cur.speed_kmh)} r="4" fill="#4ca6ff" stroke="#070b16" strokeWidth="1.5" />
      <text x={x(t) + 5} y={14} fill="#e8eef9" fontSize="10" fontWeight="700">{cur.speed_kmh.toFixed(0)} km/h</text>
    </svg>
  );
}

/** 事件来源标注：展示这条事件的数据从哪来（而非「这是示意」的道歉） */
function SourceNote({ e }: { e: FaultLabEventEx }) {
  if (e.source?.desc) {
    const m = SOURCE_META[e.source.kind || ""] || { label: e.source.kind || "数据来源", color: "var(--ink-dim)" };
    return (
      <div className="flex items-center gap-1.5 text-[10.5px] mt-0.5 min-w-0">
        <span className="inline-block h-1.5 w-1.5 rounded-full shrink-0" style={{ background: m.color }} />
        <span className="font-medium shrink-0" style={{ color: m.color }}>{m.label}</span>
        <span className="text-ink-faint truncate">{e.source.desc}</span>
      </div>
    );
  }
  if (e.derived) {
    return (
      <div className="text-[10.5px] text-ink-faint mt-0.5 flex items-center gap-1.5">
        <span className="inline-block h-1.5 w-1.5 rounded-full shrink-0" style={{ background: SOURCE_META.derived_phys.color }} />
        <span className="font-medium" style={{ color: SOURCE_META.derived_phys.color }}>示意模型</span>
        <span className="truncate">依据真实阈值 / 枚举重建（可核对页尾数据管线）</span>
      </div>
    );
  }
  return null;
}

export function FaultLabPage() {
  const [searchParams] = useSearchParams();
  const [scenarios, setScenarios] = useState<{ file: string; name: string; steps: number; fault_keys: string[] }[]>([]);
  const [sel, setSel] = useState("");
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [data, setData] = useState<FaultLabRespEx | null>(null);
  const [t, setT] = useState(0); // 当前回放时刻（场景秒）
  const [playing, setPlaying] = useState(false);
  const [scrubbing, setScrubbing] = useState(false); // 拖动进度条中（暂停播放，便于观察）
  const [speed, setSpeed] = useState(1); // 回放倍速
  const [selSpot, setSelSpot] = useState<string | null>(null); // 被点击查看异常说明的高亮点 key
  const [err, setErr] = useState("");
  const [entry, setEntry] = useState<{ mode: "demo" | "steps"; label: string; tone: "info" | "vio"; note: string } | null>(null);
  const rafRef = useRef<number>(0);
  const lastRef = useRef<number>(0);
  const playingRef = useRef(false);
  const tRef = useRef(0);
  const loadRef = useRef<((file: string, keepEntry?: boolean) => void) | null>(null); // 由后续 effect 注入，boot 与列表加载共用
  const bootedRef = useRef(false); // boot 只消费一次（URL 直达 / 共享通道）
  playingRef.current = playing;
  tRef.current = t;

  /** 消费待演示步骤：sessionStorage tcms.faultlab.draft → POST demo-steps → 资产组合动画 */
  const loadSteps = useCallback(async (draft: { name?: string; from?: DraftSource; steps: FaultLabStepPayload[] }) => {
    if (phase === "loading") return;
    setPhase("loading");
    setPlaying(false);
    setErr("");
    setEntry({
      mode: "steps",
      label: draft.from === "agent-exec" ? "来自 Agent 执行" : "来自场景执行",
      tone: draft.from === "agent-exec" ? "vio" : "info",
      note: draft.name ? `自定义序列 · ${draft.name}` : "自定义故障序列",
    });
    try {
      const r = (await demoStepsLocal({ name: draft.name, steps: draft.steps })) as unknown as FaultLabRespEx;
      setData(r);
      setT(0);
      setPhase("ready");
      setPlaying(true); // 加载即自动播放：让观众立刻看到「注入→检测→处置→恢复」的过程
    } catch (e) {
      setErr(String(e));
      setPhase("error");
    }
  }, [phase]);

  // 回放主循环（rAF，按场景秒推进；playing=false 时不推进；时刻推进 = 纯函数 advancePlayback）
  useEffect(() => {
    const loop = (now: number) => {
      if (playingRef.current && data) {
        if (!lastRef.current) lastRef.current = now;
        const dt = (now - lastRef.current) / 1000 * speed;
        const { t: next, atEnd } = advancePlayback(tRef.current, dt, data.demo.duration);
        setT(next);
        if (atEnd) setPlaying(false);
      }
      lastRef.current = playingRef.current ? now : 0;
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [data, speed]);

  const load = async (file: string, keepEntry = false) => {
    if (!file || phase === "loading") return;
    setPhase("loading");
    setPlaying(false);
    setErr("");
    // 手动加载（下拉 + 演示按钮）清掉「来自…」来源态；URL 直达等程序化加载保留已设的 entry
    if (!keepEntry) setEntry(null);
    try {
      const r = (await api.faultlabDemo(file)) as unknown as FaultLabRespEx;
      setData(r);
      setT(0);
      setSelSpot(null);
      setPhase("ready");
      // 加载即自动播放：让观众立刻看到「注入→检测→处置→恢复」的过程
      setPlaying(true);
    } catch (e) {
      setErr(String(e));
      setPhase("error");
    }
  };
  loadRef.current = load;

  // 场景列表就绪后，若 URL 带 ?scenario=file 且尚无数据 → 直达演示（t4 内置场景跳转）
  useEffect(() => {
    if (scenarios.length === 0) return;
    const qScenario = searchParams.get("scenario");
    if (qScenario && !data && phase === "idle") {
      const file = scenarios.find((s) => s.file === qScenario) ? qScenario : null;
      if (file) {
        setSel(file);
        const from = searchParams.get("from");
        if (from === "scenario-exec" || from === "agent-exec") {
          setEntry({
            mode: "demo",
            label: from === "agent-exec" ? "来自 Agent 执行" : "来自场景执行",
            tone: from === "agent-exec" ? "vio" : "info",
            note: scenarios.find((s) => s.file === file)?.name ?? "内置场景",
          });
        }
        // keepEntry=true：保留上面（若有）设的来源 Tag，load 不清它
        loadRef.current?.(file, true);
      }
    }
  }, [scenarios, searchParams, data, phase]);

  // 场景列表（基础装载，仅一次）
  useEffect(() => {
    api.faultlabScenarios().then((s) => {
      setScenarios(s);
      if (s.length) setSel(s[0].file);
    }).catch((e) => setErr(String(e)));
  }, []);

  // boot：消费共享通道 tcms.faultlab.draft（跳转/外部传入的任意序列）——只消费一次，避免手动加载后再触发
  useEffect(() => {
    if (bootedRef.current) return;
    bootedRef.current = true;
    const draft = takeDraft();
    if (draft) {
      const steps = draft.steps.map(normStep).filter((x): x is FaultLabStepPayload => x !== null);
      if (steps.length) {
        loadSteps(draft);
      }
    }
  }, [loadSteps]);

  const currentPt = useMemo(() => (data ? nearestPoint(data.curve, t) : null), [data, t]);
  const activeEvents = useMemo(() => {
    if (!data) return [];
    return eventWindow(data.demo.events, t);
  }, [data, t]);

  /** 当前激活故障集合：由 inject→recover 时间窗推导（纯函数 buildSpans/activeSpanKeys）
   *  + 通道状态兜底（门/心跳/总线/受电弓） */
  const spots = useMemo<FaultSpot[]>(() => {
    if (!data || !currentPt) return [];
    const evOf = (fk: string): FaultLabEvent | undefined =>
      data.demo.events.find((e) => e.kind === "inject" && e.fault === fk);
    const spans = buildSpans(data.demo.events);
    const out: FaultSpot[] = [];
    const seen = new Set<string>();
    const pushSpot = (key: string, fallbackTone: SpotTone) => {
      const m = FAULT_SPOT_META[key];
      const fallback = FALLBACK_SPOT;
      const ctx = spotFromEvent(evOf(key), { zh: (m ? m.zh : evOf(key)?.fault_name) || fallback.zh, tone: m?.tone ?? fallbackTone, anchor: m?.anchor ?? fallback.anchor });
      out.push({
        key,
        zh: ctx.zh,
        tone: m?.tone ?? fallbackTone,
        anchor: m?.anchor ?? fallback.anchor,
        where: ctx.where || undefined,
        level: ctx.level || undefined,
        detail: ctx.detail || undefined,
      });
      seen.add(key);
    };
    for (const key of activeSpanKeys(spans, t)) {
      if (!seen.has(key)) pushSpot(key, FALLBACK_SPOT.tone);
    }
    const pushIf = (flag: boolean, key: string, tone: SpotTone) => {
      if (flag && !seen.has(key)) pushSpot(key, tone);
    };
    pushIf(currentPt.door_fault_count > 0, "door_fault", "red");
    pushIf(!currentPt.heartbeat_ok, "heartbeat_loss_vcu", "red");
    pushIf(!currentPt.bus_ok, "bus_short", "red");
    pushIf(!currentPt.pantograph_ok, "pantograph_arc", "red");
    return out;
  }, [data, t, currentPt]);

  const seek = useCallback((tt: number) => {
    setT(Math.max(0, Math.min(tt, data?.demo.duration ?? 0)));
  }, [data]);

  const dur = data?.demo.duration ?? 1;

  /* t2 扩展字段（不存在时优雅隐藏对应区块） */
  const engine = data?.demo.engine;
  const pipeline = data?.demo.pipeline;
  const pipelineSteps = Array.isArray(pipeline) ? pipeline : (pipeline?.steps ?? []);
  const engineAsserted = engine?.asserted ?? data?.engine_asserted ?? false;
  const engineVersion = engine?.version;
  const engineAssertions = engine?.assertions ?? [];
  const engineNotes = engine?.notes ?? [];
  const constRows = pipeline?.constants;
  const constReal = constRows?.real ?? [];
  const constSchematic = constRows?.schematic ?? [];
  const stepColor = (kind?: string) => (kind ? STEP_META[kind]?.color ?? SOURCE_META[kind]?.color ?? "var(--ink-dim)" : "var(--ink-dim)");

  return (
    <div className="mx-auto w-full max-w-[1760px] space-y-4">
      <Panel title="故障演示 · FaultLab" bodyClass="p-3">
        <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
          <select className="select flex-1" value={sel} onChange={(e) => setSel(e.target.value)} aria-label="选择演示场景">
            {scenarios.map((s) => (
              <option key={s.file} value={s.file}>
                {s.name} — {s.file}
              </option>
            ))}
          </select>
          <button className="btn justify-center" onClick={() => load(sel)} disabled={phase === "loading" || !sel}>
            {phase === "loading" ? "加载中…" : "▶ 演示此场景"}
          </button>
        </div>
        {entry && phase === "ready" && data && (
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10.5px]" style={{ borderColor: "var(--line)", background: "var(--surface-2)" }}>
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${entry.tone === "vio" ? "bg-vio" : "bg-info"}`} />
            <span className="text-ink-dim">
              当前演示 = <span className="text-ink font-medium">{entry.label}</span>
              {entry.note ? ` · ${entry.note}` : ""}
              <span className="text-ink-faint">（下拉选择 + 「演示此场景」可回到手动挑选）</span>
            </span>
          </div>
        )}
        <p className="mt-2 text-[11px] text-ink-faint leading-5">
          动画 = 真实场景资产 + 故障部位资产 + 处置效果资产组合渲染：事件时刻来自真实场景 YAML（或你从场景执行 / Agent 执行跳转过来的本次执行步骤）；
          处置结果由真实引擎断言（若已执行），否则取故障字典 action；
          速度/压力曲线是按真实阈值（160 km/h 限速、300 kPa 制动缸）的示意物理模型。每条事件与下方「数据管线 · 引擎观察窗」都可溯源。
          拖动进度条会暂停回放，方便停在故障发生的瞬间观察部位高亮。
        </p>
      </Panel>

      {err && <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ {err}</div>}

      {phase === "loading" && (
        <Panel title="正在重建演示…" bodyClass="py-2">
          <SkeletonRows rows={3} cols={3} />
          <p className="text-[11px] text-ink-faint mt-1">
            装载场景/步骤 → 真实执行（若引擎可用）→ 组合故障部位与处置效果资产 → 生成事件时间线与通道曲线
          </p>
        </Panel>
      )}

      {phase === "error" && !err && (
        <Panel><EmptyState icon="⚠" title="演示加载失败" desc="请确认后端与引擎状态后重试。" /></Panel>
      )}

      {phase === "ready" && data && currentPt && (
        <div className="step-in space-y-4">
          {/* 顶部摘要：场景 + 处置来源（t4：跳转/外部序列 → 来源 Tag） */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Tag tone="info">{data.demo.scenario_name}</Tag>
            <code className="kbd-mono">{data.demo.scenario}</code>
            <span className="text-ink-faint">总时长 {data.demo.duration}s · {data.demo.events.length} 个事件</span>
            {entry && (
              <Tag tone={entry.tone} title={entry.note}>
                {entry.label}
              </Tag>
            )}
            {engineAsserted ? (
              <Tag tone="ok">处置已由真实引擎断言</Tag>
            ) : (
              <Tag tone="warn">引擎未启用 · 处置来自故障字典</Tag>
            )}
          </div>

          {/* 列车动画 + 驾驶台 */}
          <Panel bodyClass="p-3">
            <TrainGlyph
              pt={currentPt}
              derateSpeed={data.demo.params.derate_speed}
              limitKmh={data.demo.params.limit_kmh}
              spots={spots}
              selKey={selSpot}
              onSpotSelect={(k) => setSelSpot((prev) => (prev === k ? null : k))}
            />
          </Panel>

          {/* 故障注入瞬间提示（哪里异常 + 什么异常）：由事件窗 activeEvents 驱动，
              故障名/子系统/系统域 = 故障字典派生字段（fault_name/subsystem/domain_zh），
              现象 = 场景 impact 或字典 desc（detail）；无事件瞬间不显示。 */}
          {(() => {
            const now = activeEvents.filter((e) => e.kind === "inject");
            if (now.length === 0) return null;
            return (
              <div role="alert" aria-live="assertive" className="panel border-bad/50 bg-bad/10 px-3 py-2 step-in">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] leading-6">
                  {now.map((e, i) => {
                    const faultName = e.fault_name ?? e.label.replace(/^注入故障：/, "") ?? e.fault;
                    const where = e.domain_zh || e.subsystem
                      ? [e.domain_zh, e.subsystem].filter(Boolean).join(" · ")
                      : (FAULT_SPOT_META[e.fault]?.zh ?? "列车子系统");
                    const isRed = FAULT_SPOT_META[e.fault]?.tone === "red";
                    return (
                      <span key={e.fault} className="inline-flex flex-wrap items-center gap-1.5">
                        <span className="text-bad font-semibold">{i === 0 ? "⚡ 注入故障" : "· 叠加"}</span>
                        <span className="text-ink">
                          「<b className={isRed ? "text-bad" : "text-warn"}>{where}</b>」出现
                          <b className="text-ink"> {faultName}</b>
                        </span>
                        {e.level && <Tag tone={isRed ? "bad" : "warn"}>{e.level}</Tag>}
                        {e.detail && <span className="text-ink-faint text-[11px]">· {e.detail.slice(0, 80)}{e.detail.length > 80 ? "…" : ""}</span>}
                      </span>
                    );
                  })}
                </div>
                <div className="mt-1 text-[11px] text-ink-faint">
                  “哪里”= 13 系统域 · 故障字典子系统（真实）；“什么”= 真实故障名与现象。单击时间轴刻度可停在注入瞬间重看。
                </div>
              </div>
            );
          })()}

          {/* 速度/压力曲线 */}
          <Panel title="速度与制动缸压力（示意回放）" bodyClass="p-2">
            <Curves curve={data.curve} t={t} limitKmh={data.demo.params.limit_kmh} />
            {/* 控制条 */}
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <button className="btn-ghost btn-sm" onClick={() => { setPlaying((p) => !p); if (!playing && t >= dur) seek(0); }} disabled={!data}>
                {playing ? "⏸ 暂停" : "▶ 播放"}
              </button>
              <button className="btn-ghost btn-sm" onClick={() => { setPlaying(false); seek(0); }}>↺ 重置</button>
              <div className="flex items-center gap-1">
                {[0.5, 1, 2, 4].map((s) => (
                  <button key={s} className={`btn-ghost btn-sm ${speed === s ? "!text-info !border-info/50" : ""}`} onClick={() => setSpeed(s)}>
                    {s}×
                  </button>
                ))}
              </div>
              <span className="text-[11px] text-ink-faint num ml-auto">{t.toFixed(1)}s / {dur.toFixed(0)}s</span>
            </div>
            {/* 时间轴 scrubber：拖动即暂停（onPointerDown 置 playing=false），松手保持暂停，让用户看清再点播放 */}
            <input
              type="range"
              min={0}
              max={dur}
              step={0.1}
              value={t}
              onChange={(e) => seek(parseFloat(e.target.value))}
              onPointerDown={() => { setPlaying(false); setScrubbing(true); }}
              onPointerUp={() => setScrubbing(false)}
              onPointerCancel={() => setScrubbing(false)}
              className={`w-full mt-2 faultlab-range ${scrubbing ? "cursor-grabbing" : "cursor-pointer"}`}
              aria-label="回放进度（拖动时暂停）"
            />
            {/* 事件刻度条 */}
            <div className="relative h-6 mt-0.5" aria-hidden>
              {data.demo.events.map((e, i) => {
                const left = (e.t / dur) * 100;
                return (
                  <span
                    key={i}
                    onClick={() => seek(e.t)}
                    className="absolute top-1/2 -translate-y-1/2 h-3.5 w-[3px] rounded-full cursor-pointer hover:scale-y-150 transition-transform"
                    style={{ left: `calc(${left}% - 1px)`, background: KIND_COLOR[e.kind] }}
                    title={`${e.t}s ${KIND_LABEL[e.kind]}：${e.label}`}
                  />
                );
              })}
              {/* 播放头 */}
              <span
                className="absolute top-0 bottom-0 w-[2px] bg-ink/70"
                style={{ left: `calc(${(t / dur) * 100}% - 1px)` }}
              />
            </div>
            <div className="flex gap-3 mt-1 flex-wrap">
              {(Object.keys(KIND_COLOR) as (keyof typeof KIND_COLOR)[]).map((k) => (
                <span key={k} className="inline-flex items-center gap-1 text-[10px] text-ink-faint">
                  <span className="h-2 w-2 rounded-sm" style={{ background: KIND_COLOR[k] }} /> {KIND_LABEL[k]}
                </span>
              ))}
            </div>
          </Panel>

          {/* 事件解释（当前时刻） */}
          <Panel title="此刻发生了什么" bodyClass="p-3">
            {activeEvents.length === 0 ? (
              <div className="text-xs text-ink-dim leading-5">
                {currentPt.action === "none" && currentPt.alarms.length === 0 && spots.length === 0
                  ? "正常巡航中：车速平稳，各通道健康。拖动上方时间轴跳到事件处，可暂停观察故障部位如何被高亮。"
                  : `处置中：${currentPt.action}${spots.length ? " · 高亮 " + spots.map((s) => s.zh).join("、") : ""}${currentPt.alarms.length ? " · 告警 " + currentPt.alarms.join("、") : ""}`}
              </div>
            ) : (
              <div className="space-y-2">
                {activeEvents.map((e, i) => (
                  <div key={i} className="flex items-start gap-2.5">
                    <Tag tone={e.kind === "inject" ? "warn" : e.kind === "action" ? "bad" : e.kind === "recover" ? "ok" : e.kind === "detect" ? "info" : "dim"}>
                      {KIND_LABEL[e.kind]}
                    </Tag>
                    <div className="flex-1 text-[12.5px] leading-5 min-w-0">
                      <span className="text-ink font-medium">{e.label}</span>
                      {e.detail && <div className="text-ink-dim">{e.detail}</div>}
                      <SourceNote e={e} />
                    </div>
                    <span className="text-ink-faint text-[10px] num shrink-0">{e.t.toFixed(1)}s</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* 完整事件时间线（叙事流：一屏看清故障生命周期） */}
          <Panel title="完整事件时间线" bodyClass="p-3">
            <div className="space-y-1">
              {data.demo.events.map((e, i) => {
                const isNear = Math.abs(e.t - t) < 0.5;
                const isPast = e.t <= t;
                return (
                  <button
                    key={i}
                    onClick={() => seek(e.t)}
                    className={`w-full text-left flex items-start gap-2.5 rounded-lg px-2.5 py-1.5 transition-colors ${
                      isNear ? "bg-info/10 border border-info/30" : isPast ? "hover:bg-surface-2/60 opacity-90" : "opacity-55 hover:bg-surface-2/40"
                    }`}
                  >
                    <span className="text-ink-faint text-[10px] num pt-0.5 w-10 shrink-0">{e.t.toFixed(1)}s</span>
                    <Tag tone={e.kind === "inject" ? "warn" : e.kind === "action" ? "bad" : e.kind === "recover" ? "ok" : e.kind === "detect" ? "info" : "dim"}>
                      {KIND_LABEL[e.kind]}
                    </Tag>
                    <span className={`flex-1 text-[12.5px] leading-5 min-w-0 ${isNear ? "text-ink" : "text-ink-dim"}`}>
                      <span className={isNear ? "text-ink font-medium" : ""}>{e.label}</span>
                      {isNear && e.detail && <div className="text-ink-dim text-[11.5px]">{e.detail}</div>}
                      {/* 事件来源：数据从哪来，替代「（示意）」道歉 */}
                      {e.source?.desc ? (
                        <span className="flex items-center gap-1.5 text-[10px] mt-0.5 min-w-0">
                          <span className="inline-block h-1 w-1.5 rounded-full shrink-0" style={{ background: stepColor(e.source.kind) }} />
                          <span className="font-medium shrink-0" style={{ color: stepColor(e.source.kind) }}>{SOURCE_META[e.source.kind || ""]?.label ?? e.source.kind}</span>
                          <span className="text-ink-faint truncate">{e.source.desc}</span>
                        </span>
                      ) : e.derived ? (
                        <span className="flex items-center gap-1 text-[10px] text-ink-faint mt-0.5">
                          <span className="inline-block h-1.5 w-1.5 rounded-full shrink-0" style={{ background: SOURCE_META.derived_phys.color }} />
                          <span style={{ color: SOURCE_META.derived_phys.color }} className="font-medium">示意模型</span>
                          <span className="truncate">依据真实阈值 / 枚举重建</span>
                        </span>
                      ) : null}
                    </span>
                  </button>
                );
              })}
            </div>
          </Panel>

          {/* 引擎观察窗（数据管线透明化，替代「诚实性标注」道歉面板） */}
          <Panel title="数据管线 · 引擎观察窗" bodyClass="p-3">
            {pipelineSteps.length > 0 && (
              <div className="flex flex-wrap items-stretch gap-1.5">
                {pipelineSteps.map((s, i) => (
                  <Fragment key={i}>
                    {i > 0 && <span className="self-center text-ink-faint text-xs shrink-0">→</span>}
                    <div className="rounded-lg border border-line bg-surface-2/60 px-2.5 py-1.5 text-[11px] min-w-0 flex-1 basis-40">
                      <div className="flex items-center gap-1.5">
                        <span className="inline-block h-1.5 w-1.5 rounded-full shrink-0" style={{ background: stepColor(s.kind) }} />
                        <span className="text-ink-dim font-medium truncate">{s.label ?? s.name ?? s.kind ?? "步骤"}</span>
                      </div>
                      {s.desc && <div className="text-[10px] text-ink-faint mt-0.5 leading-4">{s.desc}</div>}
                    </div>
                  </Fragment>
                ))}
              </div>
            )}
            <div className="mt-2.5 border-t border-line-soft pt-2.5 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                {engineAsserted ? (
                  <Tag tone="ok">✓ 引擎真实断言已挂载</Tag>
                ) : (
                  <Tag tone="warn">引擎未启用 · 处置来自故障字典</Tag>
                )}
                {engineVersion && <span className="text-[11px] text-ink-dim num">引擎 {engineVersion}</span>}
                {engineAsserted && (
                  <span className="text-[11px] text-ink-faint">
                    {engineAssertions.length > 0 ? `${engineAssertions.length} 条真实断言（展开核对）` : "断言已由引擎执行"}
                  </span>
                )}
              </div>
              {engineAssertions.length > 0 && (
                <details className="group text-xs">
                  <summary className="cursor-pointer text-info inline-flex items-center gap-1 select-none">
                    展开 {engineAssertions.length} 条断言明细
                  </summary>
                  <div className="mt-1.5 space-y-1 max-h-44 overflow-auto pr-1">
                    {engineAssertions.map((a, i) => (
                      <div key={i} className="flex items-start gap-2 rounded-md bg-surface-2/40 px-2 py-1 text-[11px]">
                        <span className={`font-bold shrink-0 ${a.passed === false ? "text-bad" : "text-ok"}`}>
                          {a.passed === false ? "FAIL" : "PASS"}
                        </span>
                        <code className="kbd-mono shrink-0">{a.fault}</code>
                        <span className="text-ink-dim truncate">
                          期望 {a.expected ?? "-"} → 实际 {a.actual ?? "-"}
                          {a.ts != null && <span className="text-ink-faint num"> @ {a.ts}s</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {engineNotes.length > 0 && (
                <ul className="space-y-0.5">
                  {engineNotes.map((n, i) => (
                    <li key={i} className="text-[10.5px] text-ink-faint flex gap-1.5">
                      <span className="text-ink-faint shrink-0">·</span>
                      <span className="leading-4">{n}</span>
                    </li>
                  ))}
                </ul>
              )}
              {(constReal.length > 0 || constSchematic.length > 0) && (
                <div className="text-[10px] leading-4 text-ink-faint space-y-1">
                  {constReal.length > 0 && (
                    <div>
                      <span className="text-ok/80 font-medium">真实常量：</span>
                      {constReal.slice(0, 5).map((r) => `${r.name}=${String(r.value)}${r.unit ? " " + r.unit : ""}（${r.source}）`).join(" · ")}
                      {constReal.length > 5 && <span> · 等 {constReal.length} 项</span>}
                    </div>
                  )}
                  {constSchematic.length > 0 && (
                    <div>
                      <span className="text-ink-dim font-medium">示意模型常量：</span>
                      {constSchematic.slice(0, 5).map((r) => `${r.name}=${String(r.value)}${r.unit ? " " + r.unit : ""}`).join(" · ")}
                      {constSchematic.length > 5 && <span> · 等 {constSchematic.length} 项</span>}
                    </div>
                  )}
                </div>
              )}
              {/* 数据来源总述（客观陈述，非道歉） */}
              {data.honesty_note && (
                <p className="text-[10.5px] text-ink-faint leading-4 border-t border-line-soft pt-2">
                  {data.honesty_note}
                </p>
              )}
            </div>
          </Panel>
        </div>
      )}

      {phase === "idle" && (
        <Panel>
          <EmptyState
            icon="⚙"
            title="选一个真实故障场景，看它如何发生"
            desc="FaultLab 把真实场景 YAML（或场景执行 / Agent 执行跳转过来的步骤序列）变成可播放的事件时间线：故障注入 → 检测 → 处置 → 恢复。动画由场景资产 + 故障部位资产 + 处置效果资产组合渲染；列车状态、故障部位高亮、速度曲线会随时间轴同步推进；拖动进度条即暂停，方便停在关键瞬间。"
          />
        </Panel>
      )}
    </div>
  );
}
