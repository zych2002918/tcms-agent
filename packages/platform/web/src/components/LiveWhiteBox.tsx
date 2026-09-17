import { useState } from "react";
import { Tag } from "./ui";
import type { AgentStreamTraceEntry } from "../api";

/**
 * Agent 实时白盒：把运行中**真实发生的每一步**摊开。
 *
 * 为什么需要它：此前运行期间只显示一段轮换文案（"正在检索证据…"），
 * 那是**进度动画**不是**白盒**——用户看不到 Agent 到底查了什么、在哪些候选里
 * 选了什么、为什么选、真实执行注入了哪些故障。这个组件消费后端的 SSE 流，
 * 每来一条真实轨迹就渲染一条，并且**每一步都可以展开看结构化载荷**。
 *
 * 设计取向：默认给**人话**（查询串、候选、选中项、注入的故障、断言 expect→actual），
 * 原始 JSON 收在"原始载荷"里 —— 白盒不等于把 JSON 糊在屏幕上。
 */

const STEP_META: Record<string, { label: string; icon: string; tone: "info" | "ok" | "warn" | "vio" | "bad" | "dim" }> = {
  plan: { label: "任务装载", icon: "▤", tone: "dim" },
  retrieve: { label: "知识底座检索", icon: "⌕", tone: "info" },
  act: { label: "决策 · 选场景", icon: "◈", tone: "vio" },
  exec: { label: "真实执行", icon: "▶", tone: "warn" },
  verify: { label: "断言核对", icon: "✓", tone: "ok" },
  reflect: { label: "反思 / 自检", icon: "↻", tone: "vio" },
  report: { label: "结论", icon: "★", tone: "bad" },
};

type Payload = Record<string, unknown>;

const arr = (v: unknown): Payload[] => (Array.isArray(v) ? (v as Payload[]) : []);
const str = (v: unknown, d = ""): string => (v == null ? d : String(v));

/** 每步的"人话"白盒摘要（比 detail 一行更具体，且是结构化事实） */
function StepFacts({ step, p }: { step: string; p: Payload }) {
  if (step === "plan") {
    return (
      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-ink-faint">目标故障</dt>
        <dd className="kbd-mono text-ink-dim">{str(p.target_fault)}</dd>
        <dt className="text-ink-faint">期望处置</dt>
        <dd className="kbd-mono text-ink-dim">{str(p.expected_action)}</dd>
        {p.kb_query ? (
          <>
            <dt className="text-ink-faint">检索问句</dt>
            <dd className="text-ink-dim">「{str(p.kb_query)}」</dd>
          </>
        ) : null}
        <dt className="text-ink-faint">场景池</dt>
        <dd className="text-ink-dim">{str(p.scenario_pool)} 个可执行场景</dd>
      </dl>
    );
  }

  if (step === "retrieve") {
    const hits = arr(p.hits);
    return (
      <div className="mt-1.5 text-xs">
        <div className="text-ink-dim">
          真实查询：<code className="kbd-mono text-info">「{str(p.query)}」</code>
          <span className="text-ink-faint"> · 命中 {hits.length} 条</span>
          {p.route_source ? (
            <span className="text-ink-faint"> · 域路由来源 {str(p.route_source)}</span>
          ) : null}
          {Array.isArray(p.domains) && p.domains.length ? (
            <span className="text-ink-faint"> · 域 {(p.domains as string[]).join("/")}</span>
          ) : null}
        </div>
        <ul className="mt-1 space-y-0.5">
          {hits.slice(0, 6).map((h, i) => (
            <li key={i} className="flex items-baseline gap-2">
              <span className="text-ink-faint w-30 shrink-0 truncate kbd-mono" title={str(h.doc_id)}>
                {str(h.doc_id)}
              </span>
              <span className="text-info tabular-nums shrink-0">{Number(h.score ?? 0).toFixed(2)}</span>
              {Array.isArray(h.sources) && (h.sources as string[]).length ? (
                <span className="text-ink-faint shrink-0">[{(h.sources as string[]).join("+")}]</span>
              ) : null}
              <span className="text-ink-faint truncate">{str(h.excerpt).slice(0, 54)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (step === "act") {
    const cands = arr(p.candidates);
    return (
      <div className="mt-1.5 text-xs">
        <div className="text-ink-dim">
          决策后端 <code className="kbd-mono">{str(p.backend)}</code>
          <span className="text-ink-faint"> · 候选 {cands.length} 个 · 用到证据 {str(p.evidence_used)} 条</span>
        </div>
        <div className="mt-1 text-ink-dim italic">「{str(p.strategy)}」</div>
        <ul className="mt-1 space-y-0.5">
          {cands.map((c, i) => (
            <li key={i} className="flex items-baseline gap-2">
              <span className={c.is_chosen ? "text-ok shrink-0" : "text-ink-faint shrink-0"}>
                {c.is_chosen ? "✔ 选中" : "· 落选"}
              </span>
              <span className="kbd-mono text-ink-dim truncate">{str(c.file)}</span>
              <span className="text-ink-faint shrink-0">注入 {str(c.inject_count)} 个故障</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (step === "exec") {
    const inj = arr(p.injected);
    return (
      <div className="mt-1.5 text-xs">
        <div className="text-ink-dim">
          真实执行 <code className="kbd-mono text-warn">{str(p.scenario)}</code>
          {p.scenario_name ? <span className="text-ink-faint">（{str(p.scenario_name)}）</span> : null}
          <span className="text-ink-faint"> · 第 {str(p.attempt)} 次尝试</span>
        </div>
        {inj.length ? (
          <div className="mt-1">
            <span className="text-ink-faint">
              该场景实际注入 {inj.length} 个故障：
            </span>
            <ul className="mt-0.5 space-y-0.5">
              {inj.map((f, i) => (
                <li key={i} className="flex items-baseline gap-2">
                  <span className="text-ink-faint shrink-0 tabular-nums">@{Number(f.at ?? 0).toFixed(1)}s</span>
                  <span className="text-warn shrink-0">{str(f.fault)}</span>
                  <span className="text-ink-dim shrink-0">{str(f.level_label) || str(f.level)}</span>
                  <span className="text-ink-faint truncate">{str(f.impact)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    );
  }

  if (step === "verify") {
    const rel = arr(p.relevant);
    return (
      <div className="mt-1.5 text-xs">
        <div className="text-ink-dim">
          相关断言 {rel.length} 条 / 全部 {str(p.assertions_total)} 条
          <span className={p.achieved ? "text-ok" : "text-bad"}> · achieved={String(p.achieved)}</span>
        </div>
        <ul className="mt-1 space-y-0.5">
          {rel.map((a, i) => (
            <li key={i} className="flex items-baseline gap-2">
              <span className={a.passed ? "text-ok shrink-0" : "text-bad shrink-0"}>{a.passed ? "✓" : "✗"}</span>
              <span className="kbd-mono text-ink-dim shrink-0">{str(a.fault)}</span>
              <span className="text-ink-faint">
                期望 {str(a.expect)} → 实际 <span className="text-ink-dim">{str(a.actual)}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return null;
}

function StepRow({ entry, idx }: { entry: AgentStreamTraceEntry; idx: number }) {
  const [open, setOpen] = useState(false);
  const meta = STEP_META[entry.step] ?? { label: entry.step, icon: "·", tone: "dim" as const };
  const p = (entry.payload ?? {}) as Payload;
  const hasFacts = Object.keys(p).length > 0;
  return (
    <li className="border-line/60 border-b last:border-0 py-2 step-in">
      <div className="flex items-baseline gap-2">
        <span className="text-ink-faint tabular-nums text-xs w-5 shrink-0">{idx + 1}</span>
        <Tag tone={meta.tone}>
          {meta.icon} {meta.label}
        </Tag>
        <span className="text-xs text-ink-dim flex-1 min-w-0">{entry.detail}</span>
        <span className="text-[11px] text-ink-faint tabular-nums shrink-0">{entry.t.toFixed(2)}s</span>
        {hasFacts ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[11px] text-ink-faint hover:text-ink-dim shrink-0 underline decoration-dotted"
            aria-expanded={open}
          >
            {open ? "收起" : "白盒详情"}
          </button>
        ) : null}
      </div>
      {open && hasFacts ? (
        <div className="mt-1.5 ml-7 rounded border border-line/60 bg-surface-2/50 px-3 py-2">
          <StepFacts step={entry.step} p={p} />
          <details className="mt-2">
            <summary className="text-[11px] text-ink-faint cursor-pointer select-none hover:text-ink-dim">
              原始载荷（JSON）
            </summary>
            <pre className="mt-1 max-h-56 overflow-auto text-[10px] leading-relaxed text-ink-faint kbd-mono whitespace-pre-wrap break-all">
              {JSON.stringify(p, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </li>
  );
}

export function LiveWhiteBox({
  entries,
  running,
  error,
  onFallbackHint,
}: {
  entries: AgentStreamTraceEntry[];
  running: boolean;
  error?: string;
  /** 流不可用时给用户的说明（不静默降级成假进度） */
  onFallbackHint?: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (!running && entries.length === 0) return null;

  const last = entries.length ? entries[entries.length - 1] : null;
  return (
    <div className="panel px-4 py-3 step-in">
      <div className="flex items-center gap-2">
        <span className="flex h-2.5 w-2.5 shrink-0">
          <span className={`h-2.5 w-2.5 rounded-full ${running ? "bg-info pulse-dot" : "bg-ok"}`} />
        </span>
        <span className="text-sm font-medium text-ink">
          {running ? "Agent 正在运行 · 实时白盒" : "Agent 运行完毕 · 白盒轨迹"}
        </span>
        <span className="text-[11px] text-ink-faint">
          下面每一条都是**真实发生**的步骤（来自后端 SSE 流），点「白盒详情」可展开核对
        </span>
        {entries.length > 0 ? (
          <button
            type="button"
            className="ml-auto text-[11px] text-ink-faint hover:text-ink-dim underline decoration-dotted"
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? `展开 ${entries.length} 步` : "收起"}
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="mt-2 text-xs text-warn">⚠ 实时流中断：{error}</div>
      ) : null}
      {onFallbackHint ? <div className="mt-2 text-xs text-ink-faint">{onFallbackHint}</div> : null}

      {!collapsed ? (
        entries.length ? (
          <ol className="mt-1">
            {entries.map((e, i) => (
              <StepRow key={i} entry={e} idx={i} />
            ))}
          </ol>
        ) : (
          <div className="mt-2 text-xs text-ink-faint">
            {running ? "已连接后端白盒流，等待第一条真实步骤…" : "本次运行没有产生轨迹"}
          </div>
        )
      ) : null}

      {running && last ? (
        <div className="mt-1 text-[11px] text-ink-faint">最新：{last.detail}</div>
      ) : null}
    </div>
  );
}
