import { describe, expect, it } from "vitest";

import {
  advancePlayback,
  activeSpanKeys,
  buildSpans,
  clampTo,
  eventWindow,
  spanActive,
} from "./playerState";

describe("clampTo / advancePlayback（回放推进确定性）", () => {
  it("钳位到 [0, dur]", () => {
    expect(clampTo(-1, 10)).toBe(0);
    expect(clampTo(5, 10)).toBe(5);
    expect(clampTo(99, 10)).toBe(10);
  });

  it("普通推进累加并封顶", () => {
    const a = advancePlayback(1.0, 0.5, 10);
    expect(a).toEqual({ t: 1.5, atEnd: false });
    const b = advancePlayback(9.8, 0.5, 10);
    expect(b.t).toBe(10);
    expect(b.atEnd).toBe(true); // 到结尾 → 播放器应停止
  });

  it("dur<=0 恒停在 0（防除零/死循环）", () => {
    expect(advancePlayback(3, 0.2, 0)).toEqual({ t: 0, atEnd: true });
  });

  it("结尾再推一帧不越界且仍是 atEnd", () => {
    const a = advancePlayback(10, 1, 10);
    expect(a).toEqual({ t: 10, atEnd: true });
  });
});

describe("eventWindow（事件时刻窗 → “此刻发生了什么”）", () => {
  const evs = [
    { t: 1.0, kind: "inject" },
    { t: 1.3, kind: "detect" },
    { t: 3.0, kind: "recover" },
  ];
  it("在注入时刻 ±0.4s 内命中该瞬间事件", () => {
    const got = eventWindow(evs, 1.1);
    expect(got.map((e) => e.t)).toEqual([1.0, 1.3]);
  });
  it("远离事件窗返回空", () => {
    expect(eventWindow(evs, 2.0)).toEqual([]);
  });
  it("窗口边界包含（<= half）", () => {
    const b = eventWindow(evs, 1.4);
    expect(b.map((e) => e.t)).toEqual([1.0, 1.3]);
  });
});

describe("buildSpans / active（故障激活窗口 → 高亮点/chip 的数据源）", () => {
  const evs = [
    { kind: "inject", fault: "a", t: 1.0 },
    { kind: "inject", fault: "b", t: 2.0 },
    { kind: "recover", fault: "a", t: 5.0 },
  ];
  const spans = buildSpans(evs);

  it("inject 建窗，recover 收尾；未恢复保持 ∞", () => {
    expect(spans.get("a")).toEqual({ inj: 1.0, rec: 5.0 });
    expect(spans.get("b")!.rec).toBe(Infinity);
  });

  it("无 inject 的 recover 不产生窗口（与 FaultLab 语义一致）", () => {
    const s2 = buildSpans([{ kind: "recover", fault: "orphan", t: 9 }]);
    expect(s2.has("orphan")).toBe(false);
  });

  it("spanActive：窗口内激活、恢复后/注入前不激活", () => {
    expect(spanActive(spans, "a", 3)).toBe(true);
    expect(spanActive(spans, "a", 6)).toBe(false);
    expect(spanActive(spans, "a", 0.5)).toBe(false);
    expect(spanActive(spans, "b", 3)).toBe(true);
  });

  it("activeSpanKeys 按注入时刻升序、确定性输出", () => {
    expect(activeSpanKeys(spans, 3)).toEqual(["a", "b"]);
    expect(activeSpanKeys(spans, 1.5)).toEqual(["a"]);
    expect(activeSpanKeys(spans, 9)).toEqual(["b"]); // a 已恢复
  });
});
