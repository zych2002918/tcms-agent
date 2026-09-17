/** 3D 图谱“等价平滑 3D”的纯几何/动画函数（P2-1）。
 *
 * 现状是手写 SVG 伪 3D（球面散布 + 透视投影），我们先把几何与缓动抽成纯函数：
 * 可单测锁定“投影深度/近大远小/相机缩放/缓动收敛”，组件只消费 —— 为将来换
 * 真 3D(three/R3F) 预留同一套语义（球面布局/相机距离/视角目标）。
 */

export interface Rot3 {
  x: number; // 俯仰（clamp）
  y: number; // 自转角（无 wrap，连续累加）
}
export interface P3 {
  x: number;
  y: number;
  z: number;
}

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
export const CAM_DEFAULT = 2.6;
export const CAM_MIN = 1.7;
export const CAM_MAX = 7.0;
export const ROT_DEFAULT: Rot3 = { x: -0.35, y: 0.6 };

/** 费波那契球面：把 count 个点均匀散布在半径 R 球面（稳定、确定性）。 */
export function fibonacciSphere(count: number, R: number): P3[] {
  const n = Math.max(count, 1);
  const golden = Math.PI * (3 - Math.sqrt(5));
  const out: P3[] = [];
  for (let i = 0; i < n; i++) {
    const a = i * golden;
    const pol = Math.acos(1 - (2 * (i + 0.5)) / n);
    const y = Math.cos(pol) * R;
    const rxy = Math.sin(pol) * R;
    out.push({ x: Math.cos(a) * rxy, y, z: Math.sin(a) * rxy });
  }
  return out;
}

export interface Projected {
  sx: number;
  sy: number;
  depth: number; // 0（最远）~1（最近），用于近大远小/透明度
  persp: number;
}

/** 旋转(rot) + 透视(cam) 投影 → 屏幕坐标（纯函数，组件/测试共用同一公式）。 */
export function project(p: P3, rot: Rot3, cam: number, R: number, w: number, h: number): Projected {
  const cosX = Math.cos(rot.x);
  const sinX = Math.sin(rot.x);
  const cosY = Math.cos(rot.y);
  const sinY = Math.sin(rot.y);
  const x1 = p.x * cosY + p.z * sinY;
  const z1 = -p.x * sinY + p.z * cosY;
  const y2 = p.y * cosX - z1 * sinX;
  const z2 = p.y * sinX + z1 * cosX;
  const persp = 1 / (cam - z2 / R);
  const scl = (Math.min(w, h) * 0.42) / R;
  return { sx: w / 2 + x1 * persp * scl, sy: h / 2 + y2 * persp * scl, depth: (z2 / R + 1) / 2, persp };
}

/** easeInOutCubic：整段视角过渡的缓动曲线。 */
export function easeInOutCubic(t: number): number {
  const u = clamp(t, 0, 1);
  return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
}

/** 每帧向目标靠拢（指数趋近，速度 speed>0）；确定性、可测收敛。 */
export function approach(current: number, target: number, dt: number, speed = 3): number {
  return current + (target - current) * Math.min(1, dt * speed);
}
