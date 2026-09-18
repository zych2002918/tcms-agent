import { describe, expect, it } from "vitest";
import { clampPopover } from "./popover";

/**
 * 这些用例来自一个真实缺陷：模型选择浮层原本 `right-0` 锚在触发器上、
 * 固定 400px 宽，触发器变窄时浮层左边缘跑到滚动容器之外被裁掉
 * （用户看到"弹窗错位、只剩半截字"）。边界条件就是这类 bug 的全部来源，
 * 所以这里逐个钉住。
 */
describe("clampPopover", () => {
  const bounds = { minLeft: 196, maxRight: 1465 };

  it("空间足够时：右对齐触发器（默认）", () => {
    const r = clampPopover({ ...bounds, triggerLeft: 700, triggerRight: 900, preferWidth: 400 });
    expect(r.width).toBe(400);
    expect(r.left).toBe(500); // 900 - 400
  });

  it("右对齐会越界时：夹进左边界（这正是本次缺陷的场景）", () => {
    // 触发器右边缘 510、400 宽 → 想放 110，但允许范围从 196 起
    const r = clampPopover({ ...bounds, triggerLeft: 355, triggerRight: 510, preferWidth: 400 });
    expect(r.left).toBe(204); // 196 + pad(8)
    expect(r.width).toBe(400); // 空间够（196..1465），只是左移
  });

  it("允许范围比期望宽度还窄时：收窄而不是溢出", () => {
    const r = clampPopover({ minLeft: 300, maxRight: 600, triggerLeft: 320, triggerRight: 580, preferWidth: 400 });
    expect(r.width).toBe(284); // 600-8 - (300+8)
    expect(r.left).toBe(308);
  });

  it("贴右边界时不会越界（左对齐也会被夹回）", () => {
    const r = clampPopover({ ...bounds, triggerLeft: 1300, triggerRight: 1460, preferWidth: 400 });
    expect(r.left + r.width).toBeLessThanOrEqual(1465 - 8);
    // 左对齐时若右边缘越界，则整体左移（宁可不对齐触发器，也不能溢出被裁）
    const l = clampPopover({ ...bounds, triggerLeft: 1300, triggerRight: 1460, preferWidth: 400, align: "left" });
    expect(l.left + l.width).toBeLessThanOrEqual(1465 - 8);
    expect(l.left).toBe(1465 - 8 - 400);
  });

  it("零宽/退化输入不产生负数或崩溃", () => {
    const r = clampPopover({ minLeft: 500, maxRight: 500, triggerLeft: 500, triggerRight: 500, preferWidth: 400 });
    expect(r.width).toBe(0);
    expect(Number.isFinite(r.left)).toBe(true);
  });
});
