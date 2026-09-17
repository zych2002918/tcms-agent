/** FaultLab 播放器纯逻辑（状态机化：可单测、与组件解耦）。
 *
 * 设计动机（P0-2）：回放 = 一组确定性纯函数（推进 / 钳位 / 事件窗 / 故障窗口），
 * 组件只负责「状态 → 渲染」；时间语义与 UI 交错（ref/interval）分离后可用单测锁定，
 * 例如「seek 到某事件时刻应看到该事件窗」这类确定性行为。
 */

/** 钳位回放时刻到 [0, dur]。 */
export const clampTo = (v: number, dur: number): number =>
  Math.max(0, Math.min(Math.max(dur, 0), v));

export interface PlaybackTick {
  t: number;
  atEnd: boolean; // 是否已推进到结尾（用于停止播放）
}

/** 推进一帧：t+dt 按 dur 钳位；dur<=0 时恒 0 且 atEnd。 */
export function advancePlayback(t: number, dt: number, dur: number): PlaybackTick {
  if (dur <= 0) return { t: 0, atEnd: true };
  const next = clampTo(t + dt, dur);
  return { t: next, atEnd: next >= dur };
}

/** 事件时刻窗：|e.t - t| <= half 的事件（“此刻发生了什么”）。
 *  events 需已按 t 升序（本函数不排序，保持输入顺序）。 */
export function eventWindow<T extends { t: number }>(events: readonly T[], t: number, half = 0.4): T[] {
  return events.filter((e) => Math.abs(e.t - t) <= half);
}

/** 故障窗口最小输入：仅需 kind/fault/t（DemoEvent 的结构子集）。 */
export interface SpanMark {
  kind: string;
  fault: string;
  t: number;
}

export interface FaultSpan {
  inj: number;
  rec: number;
}

/** 由事件序列构建 inject→recover 窗口（语义与 FaultLab 一致）：
 *  inject 记起 {inj, rec:∞}；recover 仅在已有窗口时收尾；未恢复者 rec=∞。 */
export function buildSpans(events: readonly SpanMark[]): Map<string, FaultSpan> {
  const spans = new Map<string, FaultSpan>();
  for (const e of events) {
    if (e.kind === "inject") spans.set(e.fault, { inj: e.t, rec: Infinity });
    else if (e.kind === "recover") {
      const s = spans.get(e.fault);
      if (s) s.rec = e.t;
    }
  }
  return spans;
}

/** 故障在 t 时刻是否处于激活窗口（inj <= t <= rec）。 */
export function spanActive(spans: ReadonlyMap<string, FaultSpan>, key: string, t: number): boolean {
  const s = spans.get(key);
  return s !== undefined && t >= s.inj && t <= s.rec;
}

/** t 时刻处于激活的故障键，按注入时刻升序（确定性展示顺序）。 */
export function activeSpanKeys(spans: ReadonlyMap<string, FaultSpan>, t: number): string[] {
  return [...spans.entries()]
    .filter(([, s]) => t >= s.inj && t <= s.rec)
    .sort((a, b) => a[1].inj - b[1].inj)
    .map(([k]) => k);
}
