/**
 * 可搜索选择器：一百多个长文本选项，原生 `<select>` 不够用。
 *
 * ## 为什么需要它（而不是原生 select）
 *
 * 场景有 104 个、名字长、还常常要按**故障键**找（"door"、"overspeed"）。
 * 原生下拉只能靠滚动找人，搜不了，也显示不出"共多少个"这类决策信息。
 *
 * ## 为什么收敛成一个组件
 *
 * 场景执行页与故障演示页都需要同一个东西，各自写了一份约 120 行的实现——
 * 两份实现必然漂移（这正是本项目反复踩过的坑："改一处、忘一处"）。
 * 现在只有这一份：外观、键盘行为、浮层层级、无匹配文案都在这里统一。
 *
 * ## 交互约定
 *
 * - 浮层用 `panel-float` + `z-[var(--z-popover)]`（层级取统计划度，不写裸 z 值）；
 * - 打开即聚焦筛选框；**点浮层外**或 **Esc** 关闭；**回车选中第一条**；
 * - 触发器常显「共 N 个」，用户点之前就知道规模；
 * - 定位父级**不能有 `overflow-hidden`**，否则浮层会被裁一半（调用方注意）。
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useClampedPopover } from "../lib/popover";

export interface PickerItem {
  /** 唯一键（场景场景 = 文件名） */
  id: string;
  /** 主标题（场景 = 中文名） */
  title: string;
  /** 触发器/选项里的小字副标题（场景 = 文件名，等宽显示） */
  subtitle?: string;
  /** 选项右侧的小字（如步数） */
  meta?: ReactNode;
  /** 参与筛选的额外文本（如故障键、描述） */
  search?: string[];
  /** title 属性用的完整说明 */
  desc?: string;
}

export function SearchPicker({
  items,
  value,
  onChange,
  disabled,
  /** 触发器左侧的固定前缀（如「场景」）；不传则不显示 */
  prefix,
  /** 未选中时的占位文案 */
  placeholder = "请选择",
  /** 浮层 aria-label */
  dialogLabel = "选择一项",
  /** 筛选框 aria-label（也是 e2e 的抓手） */
  filterLabel = "筛选",
  filterPlaceholder = "输入关键词筛选…",
  /** 无匹配时的文案（`{q}` 会替换成当前关键词） */
  emptyText = "没有匹配「{q}」的项，换个词试试。",
  className = "",
}: {
  items: PickerItem[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  prefix?: string;
  placeholder?: string;
  dialogLabel?: string;
  filterLabel?: string;
  filterPlaceholder?: string;
  emptyText?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 点外面 / Esc 关闭（键盘也能关）
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // 打开即聚焦筛选框：键盘用户不用再 Tab 一次
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const kw = q.trim().toLowerCase();
  const list = useMemo(() => {
    if (!kw) return items;
    return items.filter(
      (it) =>
        it.title.toLowerCase().includes(kw) ||
        it.id.toLowerCase().includes(kw) ||
        (it.search ?? []).some((s) => s.toLowerCase().includes(kw)),
    );
  }, [items, kw]);

  const cur = items.find((it) => it.id === value);
  const pick = (id: string) => {
    onChange(id);
    setOpen(false);
    setQ("");
  };

  // 与 ModelPicker 同一套定位：夹在可裁剪祖先内（写死 left/right 会在窄容器里被裁）
  const { anchorRef, popRef, style: popStyle } = useClampedPopover<HTMLButtonElement>(open, 560);

  return (
    <div className={`relative min-w-0 flex-1 ${className}`} ref={boxRef}>
      <button
        ref={anchorRef}
        type="button"
        className="select w-full flex items-center gap-2 text-left disabled:cursor-not-allowed disabled:text-ink-faint"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={cur?.desc || (cur ? `${cur.title}（${cur.id}）` : placeholder)}
      >
        {prefix && <span className="shrink-0 text-[11px] text-ink-faint">{prefix}</span>}
        <span className={`min-w-0 flex-1 truncate ${cur ? "text-ink" : "text-ink-faint"}`}>
          {cur ? cur.title : items.length ? placeholder : "加载中…"}
        </span>
        {cur?.subtitle && (
          <span className="hidden md:inline shrink-0 max-w-[280px] truncate kbd-mono text-[11px] text-ink-faint">
            {cur.subtitle}
          </span>
        )}
        <span className="shrink-0 text-[11px] text-ink-faint num whitespace-nowrap">
          共 {items.length} 个
        </span>
        <span className="shrink-0 text-[10px] text-ink-faint" aria-hidden>
          ▾
        </span>
      </button>

      {open && (
        <div
          ref={popRef}
          style={{
            ...(popStyle ?? { left: 0, width: 560 }),
            visibility: popStyle ? "visible" : "hidden",
          }}
          className="panel-float absolute z-[var(--z-popover)] mt-1.5 overflow-hidden p-0 step-in"
          role="dialog"
          aria-label={dialogLabel}
        >
          <div className="border-b border-line-soft p-2">
            <input
              ref={inputRef}
              className="input py-1 text-[12px]"
              placeholder={filterPlaceholder}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && list.length > 0) pick(list[0].id);
              }}
              aria-label={filterLabel}
            />
          </div>

          <div className="max-h-[320px] overflow-y-auto" role="listbox" aria-label={dialogLabel}>
            {list.length === 0 && (
              <div className="px-3 py-3 text-[11.5px] text-ink-faint">
                {emptyText.replace("{q}", q)}
              </div>
            )}
            {list.map((it) => {
              const active = it.id === value;
              return (
                <button
                  key={it.id}
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => pick(it.id)}
                  title={it.desc || `${it.title}（${it.id}）`}
                  className={`block w-full px-3 py-1.5 text-left transition-colors hover:bg-surface-2 ${
                    active ? "bg-info/5" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`shrink-0 text-[11px] ${active ? "text-info" : "text-ink-faint"}`}>
                      {active ? "●" : "○"}
                    </span>
                    <span
                      className={`min-w-0 flex-1 truncate text-[12.5px] ${
                        active ? "text-info" : "text-ink"
                      }`}
                    >
                      {it.title}
                    </span>
                    {it.meta != null && (
                      <span className="num shrink-0 text-[10.5px] text-ink-faint">{it.meta}</span>
                    )}
                  </div>
                  {it.subtitle && (
                    <div className="kbd-mono mt-0.5 truncate pl-4 text-[10.5px] text-ink-faint">
                      {it.subtitle}
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex items-center gap-2 border-t border-line-soft px-3 py-1.5 text-[10.5px] text-ink-faint">
            <span>
              显示 <span className="num">{list.length}</span> / 共{" "}
              <span className="num">{items.length}</span> 个
            </span>
            <span className="ml-auto">回车选中第一条 · Esc 关闭</span>
          </div>
        </div>
      )}
    </div>
  );
}
