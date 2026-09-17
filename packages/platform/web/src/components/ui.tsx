/** 共享 UI 基元（Tailwind 类封装） */

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
export function StatusDot({ tone, pulse }: { tone: "ok" | "warn" | "bad" | "info"; pulse?: boolean }) {
  const map = {
    ok: "bg-ok",
    warn: "bg-warn",
    bad: "bg-bad",
    info: "bg-info",
  };
  return <span className={`inline-block h-2 w-2 rounded-full ${map[tone]} ${pulse ? "pulse-dot" : ""}`} />;
}

/** 面板（标题 + 内容） */
export function Panel({
  title,
  right,
  children,
  className = "",
  bodyClass = "",
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-line-soft">
          <h2 className="section-title flex items-center gap-2">{title}</h2>
          {right && <div className="flex items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={`p-4 ${bodyClass}`}>{children}</div>
    </section>
  );
}

/** 统计卡 */
export function StatCard({
  value,
  label,
  tone = "info",
  hint,
}: {
  value: ReactNode;
  label: string;
  tone?: "ok" | "warn" | "bad" | "info" | "vio";
  hint?: string;
}) {
  const numColor = { ok: "text-ok", warn: "text-warn", bad: "text-bad", info: "text-info", vio: "text-vio" }[tone];
  return (
    <div className="panel px-4 py-3" title={hint}>
      <div className={`stat-num ${numColor}`}>{value}</div>
      <div className="text-xs text-ink-dim mt-0.5">{label}</div>
    </div>
  );
}

/** 空态 / 引导 */
export function EmptyState({
  icon = "◌",
  title,
  desc,
  action,
}: {
  icon?: string;
  title: string;
  desc?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      <div className="text-3xl text-ink-faint mb-3">{icon}</div>
      <div className="text-sm font-medium text-ink">{title}</div>
      {desc && <div className="text-xs text-ink-dim mt-1 max-w-sm leading-5">{desc}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** 步骤条（流程感） */
export function StepFlow({ steps, active }: { steps: { label: string; state: "done" | "active" | "todo" }[]; active: number }) {
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

/** 加载骨架（简单 pulse 行） */
export function SkeletonRows({ rows = 4, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 p-1">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }).map((__, c) => (
            <div key={c} className="h-3.5 flex-1 rounded bg-surface-2 pulse-dot" style={{ animationDelay: `${(r + c) * 0.08}s` }} />
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
