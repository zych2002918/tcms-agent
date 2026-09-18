/** 共享 UI 基元（Tailwind 类封装）。
 *
 * ## 设计取向（产品/用户视角，不是"能显示就行"）
 *
 * 本文件是全站视觉与交互的**唯一收敛点**：页面只挑基元，不各自造轮子。
 * 三条纪律：
 * 1. **语义色只表示状态**（成功/警告/失败/信息），不做装饰——统计数字一律中性；
 * 2. **说明性文字有固定去处**（`Callout`）：一句话在明面，长解释收进可展开层，
 *    不再往页面里贴"文档段落"；
 * 3. **空态要紧凑且有用**：能预告结构、能给可点示例、能显示环境事实，
 *    而不是一张占半屏的大白卡。
 */

import type { ReactNode } from "react";

/** 信号色 tag（语义即颜色） */
export function Tag({
  children,
  tone = "info",
  title,
  onClick,
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "bad" | "info" | "vio" | "dim";
  title?: string;
  onClick?: () => void;
}) {
  const tones: Record<string, string> = {
    ok: "text-ok border-ok/40 bg-ok/10",
    warn: "text-warn border-warn/40 bg-warn/10",
    bad: "text-bad border-bad/40 bg-bad/10",
    info: "text-info border-info/40 bg-info/10",
    vio: "text-vio border-vio/40 bg-vio/10",
    dim: "text-ink-dim border-line bg-surface-2",
  };
  return (
    <span
      className={`tag ${tones[tone]} ${onClick ? "cursor-pointer hover:brightness-125" : ""}`}
      title={title}
      onClick={onClick}
    >
      {children}
    </span>
  );
}

/** 状态点（执行/在线/信号） */
export function StatusDot({
  tone,
  pulse,
}: {
  tone: "ok" | "warn" | "bad" | "info";
  pulse?: boolean;
}) {
  const map = {
    ok: "bg-ok",
    warn: "bg-warn",
    bad: "bg-bad",
    info: "bg-info",
  };
  return (
    <span className={`inline-block h-2 w-2 rounded-full ${map[tone]} ${pulse ? "pulse-dot" : ""}`} />
  );
}

/** 面板（标题 + 可选副标题 + 内容） */
export function Panel({
  title,
  sub,
  right,
  children,
  className = "",
  bodyClass = "",
  dense = false,
}: {
  title?: ReactNode;
  /** 副标题/一行说明：放在标题右侧或下方的一句人话 */
  sub?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  dense?: boolean;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || right) && (
        <header
          className={`flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-line-soft ${
            dense ? "px-3 py-2" : "px-4 py-2.5"
          }`}
        >
          <h2 className="section-title flex items-baseline gap-2 min-w-0">
            <span className="truncate">{title}</span>
            {sub ? <span className="text-[11px] font-normal text-ink-faint truncate">{sub}</span> : null}
          </h2>
          {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
        </header>
      )}
      <div className={`${dense ? "p-3" : "p-4"} ${bodyClass}`}>{children}</div>
    </section>
  );
}

/** 统计卡：**默认中性配色**（数字大小即层级，不靠颜色装饰） */
export function StatCard({
  value,
  label,
  tone = "neutral",
  hint,
  onClick,
}: {
  value: ReactNode;
  label: string;
  tone?: "ok" | "warn" | "bad" | "info" | "vio" | "neutral";
  hint?: string;
  onClick?: () => void;
}) {
  const numColor =
    tone === "neutral"
      ? "text-ink"
      : { ok: "text-ok", warn: "text-warn", bad: "text-bad", info: "text-info", vio: "text-vio" }[tone];
  return (
    <div
      className={`panel px-3.5 py-3 ${onClick ? "panel-hover cursor-pointer" : ""}`}
      title={hint}
      onClick={onClick}
    >
      <div className={`stat-num ${numColor}`}>{value}</div>
      <div className="text-[11.5px] text-ink-dim mt-0.5">{label}</div>
    </div>
  );
}

/** 空态 / 引导。
 *
 * `compact`：紧凑版（一行标题 + 一句说明 + 动作），用在已经有内容的页面里，
 * 不再撑出一张占半屏的大白卡；`steps` 用来**预告将要出现的结构**（比空话有用）。
 */
export function EmptyState({
  icon = "◌",
  title,
  desc,
  action,
  compact = false,
  steps,
}: {
  icon?: string;
  title: string;
  desc?: string;
  action?: ReactNode;
  compact?: boolean;
  steps?: { icon: string; title: string; desc: string }[];
}) {
  if (compact) {
    return (
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-line bg-surface-2 text-[14px] text-ink-faint">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-ink">{title}</div>
          {desc && <div className="text-[12px] text-ink-dim mt-0.5 leading-5">{desc}</div>}
          {steps && steps.length > 0 && (
            <ul className="mt-3 grid gap-1.5 sm:grid-cols-2">
              {steps.map((s) => (
                <li
                  key={s.title}
                  className="flex items-start gap-2 rounded-[var(--radius-md)] border border-line-soft bg-surface-2/50 px-2.5 py-2"
                >
                  <span className="text-ink-faint text-[12px] shrink-0 mt-0.5">{s.icon}</span>
                  <div className="min-w-0">
                    <div className="text-[12px] text-ink">{s.title}</div>
                    <div className="text-[11px] text-ink-faint leading-4">{s.desc}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {action && <div className="mt-3">{action}</div>}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      <div className="text-3xl text-ink-faint mb-3">{icon}</div>
      <div className="text-sm font-medium text-ink">{title}</div>
      {desc && <div className="text-xs text-ink-dim mt-1 max-w-sm leading-5">{desc}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** 说明条：**一句人话在明面，长解释收进可展开层**。
 *
 * 为什么需要它：页面里成段的灰字没人读（实测截图里最刺眼的问题之一）。
 * 这里强制把"要用户现在知道的一句"与"想深挖才看的细节"分开。
 */
export function Callout({
  tone = "info",
  icon,
  title,
  children,
  details,
  className = "",
}: {
  tone?: "info" | "warn" | "ok" | "dim";
  icon?: string;
  title: ReactNode;
  children?: ReactNode;
  /** 展开后才显示的细节（可以是段落、列表、代码块） */
  details?: ReactNode;
  className?: string;
}) {
  const tones = {
    info: "border-info/30 bg-info/8 text-info",
    warn: "border-warn/35 bg-warn/10 text-warn",
    ok: "border-ok/35 bg-ok/10 text-ok",
    dim: "border-line bg-surface-2 text-ink-dim",
  }[tone];
  return (
    <div className={`rounded-[var(--radius-md)] border px-3 py-2 ${tones} ${className}`}>
      <div className="flex items-start gap-2">
        {icon && <span className="shrink-0 text-[12px] leading-5">{icon}</span>}
        <div className="min-w-0 flex-1 text-[12px] leading-5">
          <span className="font-medium">{title}</span>
          {children && <span className="text-ink-dim"> {children}</span>}
        </div>
      </div>
      {details && (
        <details className="mt-1.5 ml-5">
          <summary className="text-[11px] text-ink-faint cursor-pointer select-none hover:text-ink-dim">
            查看细节
          </summary>
          <div className="mt-1 text-[11.5px] text-ink-dim leading-5">{details}</div>
        </details>
      )}
    </div>
  );
}

/** 分段控件（真正的 Tab）：选中态一眼可辨，键盘可达。
 *
 * 为什么需要它：原来用两个 `.btn` 并列表示"内置/手动"，用户分不清哪个是当前状态。
 */
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  className = "",
}: {
  items: { value: T; label: string; hint?: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={`inline-flex items-center gap-0.5 rounded-[var(--radius-md)] border border-line bg-surface-2 p-0.5 ${className}`}
    >
      {items.map((it) => {
        const active = it.value === value;
        return (
          <button
            key={it.value}
            role="tab"
            aria-selected={active}
            title={it.hint}
            onClick={() => onChange(it.value)}
            className={`inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1 text-[12px] transition-colors select-none ${
              active
                ? "bg-surface text-ink shadow-[var(--shadow-xs)] font-medium"
                : "text-ink-dim hover:text-ink"
            }`}
          >
            {it.label}
            {it.count != null && (
              <span className={`tabular-nums text-[10.5px] ${active ? "text-ink-faint" : "text-ink-faint"}`}>
                {it.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** 步骤条（流程感） */
export function StepFlow({
  steps,
  active,
}: {
  steps: { label: string; state: "done" | "active" | "todo" }[];
  active: number;
}) {
  void active;
  return (
    <ol className="flex items-center gap-1 flex-wrap">
      {steps.map((s, i) => (
        <li key={s.label} className="flex items-center gap-1">
          {i > 0 && <span className="text-ink-faint mx-0.5 text-xs">→</span>}
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs border transition-all ${
              s.state === "done"
                ? "text-ok border-ok/30 bg-ok/5"
                : s.state === "active"
                  ? "text-ink border-info/50 bg-info/10"
                  : "text-ink-faint border-line"
            }`}
          >
            {s.state === "done" && <span className="text-ok">✓</span>}
            {s.state === "active" && <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" />}
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** 加载骨架（形状应镜像最终内容，防布局跳动） */
export function SkeletonRows({ rows = 4, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 p-1">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }).map((__, c) => (
            <div key={c} className="skeleton h-3.5 flex-1" style={{ animationDelay: `${(r + c) * 0.08}s` }} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** 副文本解释行（小白可懂） */
export function Explain({ text }: { text: string }) {
  return <p className="text-xs text-ink-dim leading-5">{text}</p>;
}

/** 键值行（把技术细节排整齐，而不是糊成一句话） */
export function KV({ k, v, mono = false }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-2 min-w-0 text-[12px]">
      <span className="text-ink-faint shrink-0">{k}</span>
      <span className={`text-ink-dim truncate ${mono ? "kbd-mono" : ""}`} title={typeof v === "string" ? v : undefined}>
        {v}
      </span>
    </div>
  );
}
