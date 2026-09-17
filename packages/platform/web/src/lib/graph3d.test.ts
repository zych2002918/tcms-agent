import { describe, expect, it } from "vitest";

import {
  CAM_DEFAULT,
  approach,
  clamp,
  easeInOutCubic,
  fibonacciSphere,
  project,
} from "./graph3d";

describe("graph3d 几何", () => {
  it("费波那契球面：点数正确、全部落在半径 R 球面", () => {
    const pts = fibonacciSphere(40, 200);
    expect(pts.length).toBe(40);
    for (const p of pts) {
      const r = Math.hypot(p.x, p.y, p.z);
      expect(r).toBeGreaterThan(195);
      expect(r).toBeLessThan(205);
    }
  });

  it("project：朝向相机的点 depth 更近（front>back），屏幕中心随相机距离缩放", () => {
    const front = { x: 0, y: 0, z: 200 };
    const back = { x: 0, y: 0, z: -200 };
    const rot = { x: 0, y: 0 };
    const f = project(front, rot, CAM_DEFAULT, 200, 960, 600);
    const b = project(back, rot, CAM_DEFAULT, 200, 960, 600);
    expect(f.depth).toBeGreaterThan(b.depth);
    expect(b.depth).toBeLessThan(0.5);
    expect(f.persp).toBeGreaterThan(0);
    // 相机拉远 → 同一点投影更小（缩放语义；用偏心点避免恒在画面中心）
    const off = { x: 100, y: 40, z: 200 };
    const n1 = project(off, rot, CAM_DEFAULT, 200, 960, 600);
    const n2 = project(off, rot, CAM_DEFAULT + 2, 200, 960, 600);
    const d1 = Math.hypot(n1.sx - 480, n1.sy - 300);
    const d2 = Math.hypot(n2.sx - 480, n2.sy - 300);
    expect(d2).toBeLessThan(d1);
  });

  it("clamp 边界", () => {
    expect(clamp(-5, 0, 10)).toBe(0);
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(99, 0, 10)).toBe(10);
  });
});

describe("graph3d 缓动/趋近", () => {
  it("easeInOutCubic：端点与中间单调", () => {
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5, 5);
    expect(easeInOutCubic(0.2)).toBeLessThan(easeInOutCubic(0.6));
    expect(clamp(easeInOutCubic(-1), 0, 1)).toBe(0);
  });

  it("approach：指数趋近目标且不越过（确定性收敛）", () => {
    let v = 0;
    const target = 10;
    let guard = 0;
    while (guard < 1000) {
      const next = approach(v, target, 0.1, 2);
      if (Math.abs(next - target) < 1e-9) break;
      expect(next).toBeGreaterThan(v); // 单调上升
      expect(next).toBeLessThanOrEqual(target);
      v = next;
      guard++;
    }
    expect(guard).toBeLessThan(1000); // 一定收敛
  });
});
