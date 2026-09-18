/**
 * Agent 实时白盒：把运行中**真实发生的每一步**摊开。
 *
 * ## 这个组件存在的理由
 *
 * 此前运行期间只显示一段轮换文案（"正在检索证据…"），那是**进度动画**不是**白盒**——
 * 用户看不到 Agent 到底查了什么、在哪些候选里选了什么、为什么选、真实执行注入了
 * 哪些故障。后来后端接了 SSE，但白盒只在「任务列表」那条路径上；用户天天用的
 * **自由目标**路径仍然是"请求处理中…"，于是出现那个刺眼的落差：
 * **系统白盒了，用户看到的还是等待动画。**
 *
 * 现在两条路径共用同一套事件协议与这一个组件。
 *
 * ## 设计取向（用户视角，不是把 JSON 糊在屏幕上）
 *
 * - **默认给人话**：真实查询串、候选与选中项、注入了哪些故障、断言 expect→actual；
 *   原始 JSON 收在"原始载荷"里，需要核对的人再展开；
 * - **时间是真的**：每条步骤带后端记录的时刻与耗时，运行中显示"已等待 x 秒"
 *   （这是事实：距上一条事件多久）。**绝不编造进度条**——那正是要消灭的东西；
 * - **谁在决策写在脸上**：模型/后端来自后端回填的 llm 身份，前端不猜；
 * - **可审计**：一键导出整条轨迹 JSON，便于贴到问题单或评审里。
 */

import { useEffect, useRef, useState } from "react";
import type { AgentLlmIdentity, AgentStreamTraceEntry } from "../api";
import { Tag } from "./ui";

const STEP_META: Record<
  string,
  { label: string; icon: string; tone: "info" | "ok" | "warn" | "vio" | "bad" | "dim" }
> = {
  parse: { label: "目标解析", icon: "⌖", tone: "info" },
  plan: { label: "任务装载", icon: "▤", tone: "dim" },
  recall: { label: "记忆召回", icon: "↺", tone: "vio" },
  retrieve: { label: "知识底座检索", icon: "⌕", tone: "info" },
  act: { label: "决策 · 选场景", icon: "◈", tone: "vio" },
  exec: { label: "真实执行", icon: "▶", tone: "warn" },
  verify: { label: "断言核对", icon: "✓", tone: "ok" },
  reflect: { label: "反思 / 自检", icon: "↻", tone: "vio" },
  report: { label: "结论", icon: "★", tone: "bad" },
};

const TONE_CLASS: Record<string, { dot: string; chip: string }> = {
  info: { dot: "border-info/45 bg-info/10 text-info", chip: "text-info" },
  ok: { dot: "border-ok/45 bg-ok/10 text-ok", chip: "text-ok" },
  warn: { dot: "border-warn/45 bg-warn/10 text-warn", chip: "text-warn" },
  vio: { dot: "border-vio/45 bg-vio/10 text-vio", chip: "text-vio" },
  bad: { dot: "border-bad/45 bg-bad/10 text-bad", chip: "text-bad" },
  dim: { dot: "border-line bg-surface-2 text-ink-faint", chip: "text-ink-faint" },
};

type Payload = Record<string, unknown>;

const arr = (v: unknown): Payload[] => (Array.isArray(v) ? (v as Payload[]) : []);
const str = (v: unknown, d = ""): string => (v == null ? d : String(v));

/** 每步的"人话"白盒摘要（比 detail 一行更具体，且是结构化事实） */
function StepFacts({ step, p }: { step: string; p: Payload }) {
  if (step === "parse") {
    return (
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-ink-faint">输入目标</dt>
        <dd className="text-ink-dim">「{str(p.goal)}」</dd>
        {p.no_match ? (
          <>
            <dt className="text-ink-faint">结果</dt>
            <dd className="text-warn">未锚定到真实故障 —— 不硬猜，给候选让你选</dd>
          </>
        ) : (
          <>
            <dt className="text-ink-faint">命中故障</dt>
            <dd className="text-ink-dim">
              <span className="kbd-mono text-info">{str(p.fault)}</span>
              {p.fault_name ? <span className="text-ink-faint"> · {str(p.fault_name)}</span> : null}
            </dd>
            {p.expected ? (
              <>
                <dt className="text-ink-faint">期望处置</dt>
                <dd className="kbd-mono text-ink-dim">{str(p.expected)}</dd>
              </>
            ) : null}
            {p.confidence != null ? (
              <>
                <dt className="text-ink-faint">置信度</dt>
                <dd className="text-ink-dim tabular-nums">
                  {str(p.confidence)}
                  {p.matched_on ? <span className="text-ink-faint"> · 命中依据 {str(p.matched_on)}</span> : null}
                  {p.resolver ? <span className="text-ink-faint"> · 解析器 {str(p.resolver)}</span> : null}
                </dd>
              </>
            ) : null}
          </>
        )}
      </dl>
    );
  }

  if (step === "plan") {
    return (
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
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
        {p.scenario_pool != null ? (
          <>
            <dt className="text-ink-faint">场景池</dt>
            <dd className="text-ink-dim">{str(p.scenario_pool)} 个可执行场景</dd>
          </>
        ) : null}
      </dl>
    );
  }

  if (step === "retrieve") {
    const hits = arr(p.hits);
    return (
      <div className="text-xs">
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
      <div className="text-xs">
        <div className="text-ink-dim">
          决策后端 <code className="kbd-mono">{str(p.backend)}</code>
          <span className="text-ink-faint">
            {" "}
            · 候选 {cands.length} 个 · 用到证据 {str(p.evidence_used)} 条
          </span>
        </div>
        {p.strategy ? <div className="mt-1 text-ink-dim italic">「{str(p.strategy)}」</div> : null}
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
      <div className="text-xs">
        <div className="text-ink-dim">
          真实执行 <code className="kbd-mono text-warn">{str(p.scenario)}</code>
          {p.scenario_name ? <span className="text-ink-faint">（{str(p.scenario_name)}）</span> : null}
          <span className="text-ink-faint"> · 第 {str(p.attempt)} 次尝试</span>
        </div>
        {inj.length ? (
          <div className="mt-1">
            <span className="text-ink-faint">该场景实际注入 {inj.length} 个故障：</span>
            <ul className="mt-0.5 space-y-0.5">
              {inj.map((f, i) => (
                <li key={i} className="flex items-baseline gap-2">
                  <span className="text-ink-faint shrink-0 tabular-nums">
                    @{Number(f.at ?? 0).toFixed(1)}s
                  </span>
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
      <div className="text-xs">
        <div className="text-ink-dim">
          相关断言 {rel.length} 条 / 全部 {str(p.assertions_total)} 条
          <span className={p.achieved ? "text-ok" : "text-bad"}> · achieved={String(p.achieved)}</span>
        </div>
        <ul className="mt-1 space-y-0.5">
          {rel.map((a, i) => (
            <li key={i} className="flex items-baseline gap-2">
              <span className={a.passed ? "text-ok shrink-0" : "text-bad shrink-0"}>
                {a.passed ? "✓" : "✗"}
              </span>
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

function StepRow({ entry, last }: { entry: AgentStreamTraceEntry; last: boolean }) {
  const [open, setOpen] = useState(false);
  const meta = STEP_META[entry.step] ?? { label: entry.step, icon: "·", tone: "dim" as const };
  const tone = TONE_CLASS[meta.tone] ?? TONE_CLASS.dim;
  const p = (entry.payload ?? {}) as Payload;
  const hasFacts = Object.keys(p).length > 0;
  const facts = hasFacts ? <StepFacts step={entry.step} p={p} /> : null;
  const factsEmpty = hasFacts && facts === null;

  return (
    <li className="relative pl-8 pb-2.5 last:pb-0 step-in">
      {/* 时间轴圆点（图标即语义） */}
      <span
        className={`absolute left-0 top-[3px] grid h-[19px] w-[19px] place-items-center rounded-full border text-[10px] ${tone.dot} ${
          last ? "shadow-[0_0_0_3px_var(--info-soft)]" : ""
        }`}
        aria-hidden
      >
        {meta.icon}
      </span>
      <div className="flex items-baseline gap-2 min-w-0">
        <span className={`text-[11px] font-semibold shrink-0 ${tone.chip}`}>{meta.label}</span>
        <span className="text-[12.5px] text-ink-dim flex-1 min-w-0 truncate" title={entry.detail}>
          {entry.detail}
        </span>
        <span className="text-[11px] text-ink-faint tabular-nums shrink-0">{entry.t.toFixed(2)}s</span>
        {hasFacts && !factsEmpty ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[11px] text-ink-faint hover:text-ink shrink-0 underline decoration-dotted underline-offset-2"
            aria-expanded={open}
          >
            {open ? "收起" : "白盒详情"}
          </button>
        ) : null}
      </div>
      {open && !factsEmpty ? (
        <div className="mt-1.5 rounded-[var(--radius-md)] border border-line-soft bg-surface-2/60 px-3 py-2">
          {facts}
          <details className="mt-2 group">
            <summary className="text-[11px] text-ink-faint cursor-pointer select-none hover:text-ink-dim">
              原始载荷（JSON）
            </summary>
            <pre className="mt-1 max-h-56 overflow-auto text-[10.5px] leading-relaxed text-ink-faint kbd-mono whitespace-pre-wrap break-all">
              {JSON.stringify(p, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </li>
  );
}

/** 运行中：距上一条事件已经等了多久（这是事实，不是编出来的进度） */
function WaitingTick({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(t);
  }, []);
  const d = Math.max(0, now - since);
  return <span className="tabular-nums">{d.toFixed(1)}s</span>;
}

export function LiveWhiteBox({
  entries,
  running,
  error,
  onFallbackHint,
  llm,
  goalText,
}: {
  entries: AgentStreamTraceEntry[];
  running: boolean;
  error?: string;
  /** 流不可用时给用户的说明（不静默降级成假进度） */
  onFallbackHint?: string;
  /** 后端回填的"谁在决策"（模型/后端）；缺省时不显示，不猜 */
  llm?: AgentLlmIdentity | null;
  /** 本次的目标（标题里显示，方便对照"我让它干什么 / 它干了什么"） */
  goalText?: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [copied, setCopied] = useState(false);
  const last = entries.length ? entries[entries.length - 1] : null;
  //: 上一条真实步骤**到达前端**的时刻（本地时钟）。等待时长用它是诚实的：
  //: 它回答的是"界面已经多久没新消息了"，而不是假装知道后端在干什么。
  const lastArrival = useRef<number>(Date.now() / 1000);
  const prevLen = useRef(0);
  useEffect(() => {
    if (entries.length !== prevLen.current) {
      prevLen.current = entries.length;
      lastArrival.current = Date.now() / 1000;
    }
  }, [entries.length]);

  if (!running && entries.length === 0) return null;
  const modelText =
    llm?.backend === "llm"
      ? `${llm.model ?? "（未命名模型）"}${llm.override ? " · 本次指定" : " · 跟随设置"}`
      : llm?.backend === "mock"
        ? "离线规则臂（未配置 key，不是 LLM 决策）"
        : "";

  const exportTrace = () => {
    const text = JSON.stringify({ goal: goalText, llm, steps: entries }, null, 2);
    void navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      () => undefined,
    );
  };

  return (
    <section className="panel overflow-hidden">
      {/* 头部：状态 + 谁在决策 + 可审计动作 */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 border-b border-line-soft">
        <span className="relative flex h-2 w-2 shrink-0">
          {running && (
            <span className="absolute inline-flex h-2 w-2 rounded-full bg-info opacity-60 pulse-dot" />
          )}
          <span className={`relative inline-flex h-2 w-2 rounded-full ${running ? "bg-info" : "bg-ok"}`} />
        </span>
        <h2 className="text-[13px] font-semibold text-ink">
          {running ? "正在运行 · 实时白盒" : "运行完毕 · 白盒轨迹"}
        </h2>
        <span className="text-[11px] text-ink-faint">
          {entries.length} 步
          {last ? <span className="tabular-nums"> · 最近 {last.t.toFixed(2)}s</span> : null}
        </span>
        {modelText ? (
          <Tag tone={llm?.backend === "llm" ? (llm.override ? "vio" : "info") : "dim"} title="本次运行实际使用的决策后端">
            {llm?.backend === "llm" ? "LLM" : "离线"} · {modelText}
          </Tag>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          {entries.length > 0 && (
            <button type="button" className="btn-soft" onClick={exportTrace} title="复制整条轨迹 JSON（可贴进问题单/评审）">
              {copied ? "✓ 已复制" : "导出轨迹"}
            </button>
          )}
          {entries.length > 0 && (
            <button type="button" className="btn-soft" onClick={() => setCollapsed((v) => !v)}>
              {collapsed ? `展开 ${entries.length} 步` : "收起"}
            </button>
          )}
        </div>
      </header>

      {/* 说明行：这一块为什么可信 */}
      <p className="px-4 pt-2 text-[11px] text-ink-faint leading-4">
        下面每一条都是<b className="text-ink-dim font-medium">真实发生</b>的步骤（后端 SSE 推来的，不是前端编的进度）；点「白盒详情」可展开核对原始载荷。
      </p>

      {error ? (
        <div className="mx-4 mt-2 rounded-[var(--radius-md)] border border-warn/35 bg-warn/10 px-3 py-1.5 text-xs text-warn">
          ⚠ 实时流中断：{error}（已收到的步骤仍然有效）
        </div>
      ) : null}
      {onFallbackHint ? <div className="px-4 pt-2 text-xs text-ink-faint">{onFallbackHint}</div> : null}

      {!collapsed ? (
        <div className="px-4 py-3">
          {entries.length ? (
            <div className="relative">
              {/* 时间轴竖线：只画在圆点之间，不用整块背景 */}
              <span
                className="absolute left-[9px] top-[12px] bottom-[10px] w-px bg-line"
                aria-hidden
              />
              <ol className="relative">
                {entries.map((e, i) => (
                  <StepRow key={i} entry={e} last={running && i === entries.length - 1} />
                ))}
              </ol>
            </div>
          ) : (
            <div className="text-xs text-ink-faint">
              {running ? "已连上后端白盒流，等待第一条真实步骤…（不显示编造的进度）" : "本次运行没有产生轨迹"}
            </div>
          )}
        </div>
      ) : null}

      {/* 运行中的底部状态行：如实说"在等什么、等了多久" */}
      {running ? (
        <div className="px-4 py-2 border-t border-line-soft text-[11px] text-ink-faint flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot shrink-0" />
          {last ? (
            <span>
              下一步进行中 · 距上一条真实步骤 <WaitingTick since={lastArrival.current} />（等待期间不编造进度）
            </span>
          ) : (
            <span>
              已连上白盒流，等待第一条真实步骤 · <WaitingTick since={lastArrival.current} />
            </span>
          )}
        </div>
      ) : null}
    </section>
  );
}
