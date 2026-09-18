/**
 * 浮层定位：把下拉/弹层**夹在可裁剪祖先的可视范围内**。
 *
 * ## 为什么需要它（本轮真实缺陷）
 *
 * 模型选择器原来是 `absolute right-0`——锚在触发器右边缘、固定 400px 宽向左伸。
 * 页面主滚动区是 `overflow: auto` 的容器（从内容区左边界开始），于是：
 * **当触发器变窄（模型名短）、右边缘左移时，浮层左边缘会跑到滚动容器之外，被直接裁掉**——
 * 用户看到的是"弹窗错位/只剩半截字"（实测：`qwen3.8-27b` 这种短名字下，
 * 「跟随设置（deepseek-v4-pro-0813）」只剩 `4-pro-0813`）。
 *
 * 同类坑对任何"锚在元素上的浮层"都成立，所以这里做成共用件：
 * 定位**不能**只相对触发器算，还要受"最近的可裁剪祖先"约束。
 *
 * ## 分层
 *
 * - `clampPopover`：**纯函数**（只做几何），带单元测试——边界条件是这类 bug 的全部来源；
 * - `useClampedPopover`：浏览器侧薄封装（量 rect、找裁剪祖先、开时与 resize 时重算）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface ClampInput {
  /** 触发器可视范围（相对视口） */
  triggerLeft: number;
  triggerRight: number;
  /** 允许占用范围（相对视口）：取"最近的可裁剪祖先"与视口的交集 */
  minLeft: number;
  maxRight: number;
  /** 期望宽度（放不下时自动收窄） */
  preferWidth: number;
  /** 对齐方式：`right` = 右边缘对齐触发器（默认），`left` = 左边缘对齐 */
  align?: "right" | "left";
  /** 与边界的最小留白 */
  pad?: number;
}

export interface ClampResult {
  /** 浮层左边缘（相对视口） */
  left: number;
  /** 实际可用宽度（≥ 0） */
  width: number;
}

/** 纯几何计算：给定触发器与允许范围，算出浮层该放哪、多宽。 */
export function clampPopover({
  triggerLeft,
  triggerRight,
  minLeft,
  maxRight,
  preferWidth,
  align = "right",
  pad = 8,
}: ClampInput): ClampResult {
  const lo = minLeft + pad;
  const hi = maxRight - pad;
  const available = Math.max(0, hi - lo);
  const width = Math.min(preferWidth, available);
  const anchor = align === "right" ? triggerRight - width : triggerLeft;
  // 先按对齐方式落位，再夹进 [lo, hi-width]
  const maxLeft = Math.max(lo, hi - width);
  const left = Math.min(Math.max(anchor, lo), maxLeft);
  return { left, width };
}

/** 找最近的可裁剪祖先（overflow 非 visible 的那一层）的可视范围。 */
function clippingBounds(el: HTMLElement | null): { minLeft: number; maxRight: number } {
  let cur = el?.parentElement ?? null;
  while (cur) {
    const s = getComputedStyle(cur);
    const clipX = /(auto|scroll|hidden|clip)/.test(s.overflowX);
    const clipY = /(auto|scroll|hidden|clip)/.test(s.overflowY);
    if (clipX || clipY) {
      const r = cur.getBoundingClientRect();
      return { minLeft: r.left, maxRight: r.right };
    }
    cur = cur.parentElement;
  }
  return { minLeft: 0, maxRight: window.innerWidth };
}

/**
 * 打开时（以及窗口尺寸变化时）算一次浮层位置；返回该挂到浮层上的 ref 与内联样式。
 *
 * 用法：
 *   const { anchorRef, popRef, style } = useClampedPopover(open, 400);
 *   <div ref={anchorRef}>…触发器…</div>
 *   {open && <div ref={popRef} className="absolute …" style={style}>…</div>}
 */
export function useClampedPopover<T extends HTMLElement = HTMLElement>(open: boolean, preferWidth: number) {
  const anchorRef = useRef<T | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const [style, setStyle] = useState<{ left: number; width: number } | null>(null);

  const compute = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    const bounds = clippingBounds(anchor);
    const { left, width } = clampPopover({
      triggerLeft: r.left,
      triggerRight: r.right,
      minLeft: bounds.minLeft,
      maxRight: bounds.maxRight,
      preferWidth,
    });
    // 转成"相对定位父级"的偏移：定位父级就是 anchor 自身所在的 relative 容器
    const parent = popRef.current?.offsetParent as HTMLElement | null;
    const base = parent ? parent.getBoundingClientRect().left : r.left;
    setStyle({ left: left - base, width });
  }, [preferWidth]);

  useEffect(() => {
    if (!open) return;
    // 首帧还没渲染出来时 offsetParent 拿不到，等一帧再量
    const t = requestAnimationFrame(compute);
    window.addEventListener("resize", compute);
    window.addEventListener("scroll", compute, true);
    return () => {
      cancelAnimationFrame(t);
      window.removeEventListener("resize", compute);
      window.removeEventListener("scroll", compute, true);
    };
  }, [open, compute]);

  return { anchorRef, popRef, style };
}
