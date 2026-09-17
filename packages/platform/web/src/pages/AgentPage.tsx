import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentComposeResp, type AgentFreeResp, type AgentRunResp } from "../api";
import { Panel, Tag, EmptyState, SkeletonRows } from "../components/ui";

type AgentRun = AgentRunResp["runs"][number];

/** Q7：四维雷达 SVG（达成/证据/执行/反思），fresume 式质量可视化 */
function RadarFour({ axes }: { axes: { goal_achieved: number; evidence_used: number; exec_pass: number; reflection: number } }) {
  const SIZE = 96;
  const CX = SIZE / 2;
  const CY = SIZE / 2;
  const R = 34;
  const vals = [axes.goal_achieved, axes.evidence_used, axes.exec_pass, axes.reflection];
  const labels = ["达成", "证据", "执行", "反思"];
  const colors = ["var(--ok)", "var(--info)", "var(--vio)", "var(--warn)"];
  // 4 轴：上、右、下、左
  const pt = (i: number, ratio: number): [number, number] => {
    const ang = (Math.PI / 2) + (i * 2 * Math.PI) / 4;
    return [CX + Math.cos(ang) * R * ratio, CY - Math.sin(ang) * R * ratio];
  };
  const poly = vals.map((v, i) => pt(i, Math.max(0.06, v / 100)).join(",")).join(" ");
  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label="四维质量雷达">
      {[0.33, 0.66, 1].map((g) => (
        <polygon key={g} points={[0, 1, 2, 3].map((i) => pt(i, g).join(",")).join(" ")} fill="none" stroke="var(--line)" strokeWidth="0.6" />
      ))}
      {[0, 1, 2, 3].map((i) => {
        const [x, y] = pt(i, 1.12);
        return (
          <text key={i} x={x} y={y} textAnchor="middle" dominantBaseline="middle" fontSize="8" fill="var(--ink-faint)">
            {labels[i]}
          </text>
        );
      })}
      <polygon points={poly} fill="var(--info)" fillOpacity="0.18" stroke="var(--info)" strokeWidth="1.3" />
      {vals.map((v, i) => {
        const [x, y] = pt(i, Math.max(0.06, v / 100));
        return <circle key={i} cx={x} cy={y} r="2" fill={colors[i]} />;
      })}
    </svg>
  );
}

type SysStatus = {
  engine: { ok: boolean; version?: string };
  llm_key: boolean;
};

const STEP_META: Record<string, { label: string; tone: "info" | "ok" | "warn" | "vio" | "bad" }> = {
  plan: { label: "规划", tone: "vio" },
  retrieve: { label: "检索证据", tone: "info" },
  act: { label: "决策", tone: "info" },
  exec: { label: "真实执行", tone: "warn" },
  verify: { label: "验证", tone: "ok" },
  reflect: { label: "反思", tone: "bad" },
  report: { label: "汇报", tone: "ok" },
};

/** 管线顺序（与后端真实轨迹的 step 对齐：plan→retrieve→act→exec→verify→reflect→report） */
const STEP_ORDER = ["plan", "retrieve", "act", "exec", "verify", "reflect", "report"];

/** 跳 FaultLab：run.scenario 是真实场景文件名（含 .yaml）→ 直达 ?scenario= 演示本次执行的场景动画
 * 通道契约与 fe-faultlab t4 对齐：from=agent-exec → FaultLab 顶部标注「来自 Agent 执行」 */
function faultlabHref(scenario: string, from: string): string {
  const p = new URLSearchParams({ scenario, from });
  return `/faultlab?${p.toString()}`;
}

const DIM_LABELS: Record<string, string> = {
  result_grounded: "真实断言",
  evidence_used: "证据使用",
  threshold_aware: "阈值感知",
  domain_aware: "领域知识",
  requirement_trace: "需求追溯",
  honesty: "诚实性",
};

/** 运行中「Agent 思考中…」的步骤提示（随动画轮换，给用户"进程在走"的感觉） */
const RUN_HINTS = [
  "正在把你的目标拆成「故障 → 期望处置」…",
  "正在从知识底座检索证据（GraphRAG）…",
  "正在挑选覆盖该故障的真实场景…",
  "正在真实引擎上执行并核对断言…",
  "正在评审结果：阈值 / 联锁 / 需求追溯…",
];

/** 单条 run 的「管线进程视图」：把逐条 reveal 映射到横排 step 点亮 */
function StepPipeline({ trace, showCount }: { trace: AgentRun["trace"]; showCount: number }) {
  if (trace.length === 0) return null;
  const shown = trace.slice(0, showCount);
  const doneSteps = new Set(shown.map((t) => t.step));
  const lastStep = shown.length > 0 ? shown[shown.length - 1].step : null;
  const stillRevealing = showCount < trace.length;
  const toneCls: Record<string, string> = {
    ok: "text-ok border-ok/40 bg-ok/10",
    warn: "text-warn border-warn/40 bg-warn/10",
    bad: "text-bad border-bad/40 bg-bad/10",
    info: "text-info border-info/40 bg-info/10",
    vio: "text-vio border-vio/40 bg-vio/10",
    dim: "text-ink-dim border-line bg-surface-2",
  };
  return (
    <ol className="flex flex-wrap items-center gap-y-1.5 gap-x-0 px-4 pt-3 pb-0.5" aria-label="执行管线">
      {STEP_ORDER.map((s, i) => {
        const meta = STEP_META[s] ?? { label: s, tone: "info" as const };
        const isDone = doneSteps.has(s);
        const isActive = isDone && stillRevealing && s === lastStep;
        const isTodo = !isDone;
        return (
          <li key={s} className="flex items-center gap-x-0">
            {i > 0 && <span className="text-ink-faint mx-1 text-[10px]">→</span>}
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] leading-4 whitespace-nowrap transition-all ${
                isActive
                  ? "text-info border-info/70 bg-info/15"
                  : isDone
                    ? toneCls[meta.tone] ?? toneCls.info
                    : "text-ink-faint border-line bg-transparent"
              }`}
            >
              {isActive ? (
                <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" />
              ) : isDone ? (
                <span className="text-[9px]">✓</span>
              ) : (
                <span className="h-1.5 w-1.5 rounded-full border border-ink-faint/50" />
              )}
              {isTodo ? <span className="opacity-60">{meta.label}</span> : meta.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** 自由目标 → 解析卡：给人看 Agent 怎么理解自然语言 */
function GoalParseCard({ resp }: { resp: AgentFreeResp }) {
  const p = resp.parsed;
  if (!p) return null;
  const conf = typeof p.confidence === "number" ? Math.round(p.confidence * 100) : null;
  return (
    <div className="panel border-info/30 bg-info/5 px-4 py-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] text-ink-faint font-medium uppercase tracking-wide">Agent 理解你的目标</span>
        <span className="text-[11px] text-ink-dim">“{resp.goal}”</span>
        {conf !== null && (
          <span className="ml-auto" title="解析命中的依据充分性分数（排序分，非概率）">
            <Tag tone={conf >= 60 ? "ok" : "warn"}>依据分 {conf}</Tag>
          </span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="text-ink-dim">锚定故障</span>
        <code className="kbd-mono !text-[13px] !text-bad border border-bad/30 bg-bad/10 rounded px-1.5 py-0.5">
          {p.fault_name ? `${p.fault_name}（${p.fault}）` : p.fault}
        </code>
        <span className="text-ink-faint">→</span>
        <span className="text-ink-dim">期望处置</span>
        <code className="kbd-mono !text-[13px] !text-ok border border-ok/30 bg-ok/10 rounded px-1.5 py-0.5">
          {p.expected_zh ? `${p.expected_zh}（${p.expected}）` : p.expected}
        </code>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-ink-faint">
        {p.resolver && (
          <span>
            解析方式：{p.resolver === "llm" ? "LLM 语义理解" : "规则匹配"}
          </span>
        )}
        {p.matched_on && <span>命中依据：{p.matched_on}</span>}
        <span className="text-ink-faint">下面按这条理解走完整流程：检索证据 → 真实执行 → 评审。</span>
      </div>
    </div>
  );
}

export function AgentPage() {
  const [tasks, setTasks] = useState<{ task_id: string; title: string; goal: string; target_fault: string; expected_action: string }[]>([]);
  const [sel, setSel] = useState("");
  const [sys, setSys] = useState<SysStatus | null>(null);
  const [phase, setPhase] = useState<"idle" | "running" | "done">("idle");
  const [result, setResult] = useState<AgentRunResp | null>(null);
  const [freeResp, setFreeResp] = useState<AgentFreeResp | null>(null);
  const [composeResp, setComposeResp] = useState<AgentComposeResp | null>(null);
  const [composing, setComposing] = useState(false);
  // 用户对"未锚定子句"的点选并入：{clause=该句原文, key=候选真实键}；点一个只并一个，
  // 其余未锚定子句保留在响应里继续可点（不再"选一个、另一个消失"）。
  const [composePicks, setComposePicks] = useState<{ clause: string; key: string }[]>([]);
  // 症状诊断（无码症状 → 图谱因果链；确定性规则，derived 显式标注）
  const [diagQ, setDiagQ] = useState("");
  const [diagResp, setDiagResp] = useState<Awaited<ReturnType<typeof api.agentDiagnose>> | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const [diagErr, setDiagErr] = useState("");
  const [diagLlm, setDiagLlm] = useState(false); // LLM 候选内仲裁开关（需已配置 key）
  const diagSidRef = useRef<string | null>(null); // P1-1 多轮诊断锚点会话 id（追问沿用上一轮）
  const [visible, setVisible] = useState(0); // 事件流逐条揭示
  const [err, setErr] = useState("");
  const [goal, setGoal] = useState("");
  const [goalHint, setGoalHint] = useState(""); // 自由目标没锚定 → 换说法引导
  const [hintIdx, setHintIdx] = useState(0); // 运行中步骤提示轮换
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  /** 场景文件名 → 中文名（“场景名=简短释义”展示用；拿不到就回落文件主干） */
  const [scenName, setScenName] = useState<Record<string, string>>({});

  useEffect(() => {
    api.agentTasks().then((t) => { setTasks(t); if (t.length) setSel(t[0].task_id); }).catch(() => undefined);
    api.systemStatus().then(setSys).catch(() => undefined);
    api.scenarios()
      .then((list) => setScenName(Object.fromEntries(list.map((s) => [s.file, s.name]))))
      .catch(() => undefined);
    return () => timers.current.forEach(clearTimeout);
  }, []);

  // 运行中：步骤提示轮换（简单 interval；离开 running 自动停）
  useEffect(() => {
    if (phase !== "running") return;
    const id = setInterval(() => setHintIdx((i) => i + 1), 2000);
    return () => clearInterval(id);
  }, [phase]);

  const current = tasks.find((t) => t.task_id === sel);
  const engineBlocked = sys !== null && !sys.engine.ok;
  /** file → 中文场景名（优先）；未收录回落 file 去 .yaml */
  const scenLabel = (f: string | null | undefined) => (f && scenName[f]) || (f ? f.replace(/\.yaml$/, "") : "—");

  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };

  /** 按真实轨迹时间逐条揭示（事件流感：顺序/耗时是后端真实记录） */
  const scheduleReveal = (runs: AgentRun[]) => {
    setVisible(0);
    const run0 = runs[0];
    if (run0) {
      const totalMs = run0.duration_ms || 1200;
      run0.trace.forEach((_, i) => {
        const delay = 250 + (totalMs / Math.max(run0.trace.length, 1)) * 0.9;
        timers.current.push(setTimeout(() => setVisible(i + 1), i * delay));
      });
    } else {
      setVisible(1);
    }
  };

  const begin = () => {
    clearTimers();
    setPhase("running");
    setResult(null);
    setFreeResp(null);
    setErr("");
    setGoalHint("");
    setVisible(0);
  };

  const run = async (taskId?: string) => {
    const id = taskId ?? sel;
    if (!id || phase === "running") return;
    // 引擎缺失 → 不执行，引导（不产生 0% 的假失败）
    if (sys && !sys.engine.ok) {
      setErr("engine_missing");
      return;
    }
    begin();
    try {
      const r = await api.agentRun(id);
      setResult(r);
      scheduleReveal(r.runs);
      setPhase("done");
    } catch (e) {
      setErr(String(e));
      setPhase("done");
    }
  };

  const runFreeGoal = async (g: string) => {
    const goalText = (g ?? "").trim();
    if (!goalText || phase === "running") return;
    setGoal(goalText); // 同步输入框，方便用户看到“去查证”的是哪句
    if (sys && !sys.engine.ok) {
      setErr("engine_missing");
      return;
    }
    begin();
    try {
      // 契约：命中 → 200 恒带 parsed；规则未命中 → 200 no_match + suggested_faults（RAG 候选）
      const r = await api.agentFree(goalText);
      setFreeResp(r);
      if (r.no_match) {
        setGoalHint(r.detail ?? "");
      } else {
        scheduleReveal(r.runs);
      }
      setPhase("done");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // 后端意外 422/400（异常路径）→ 中文空态引导；其余 → 错误条
      if (/^422:|^400:/.test(msg)) {
        setGoalHint(msg.replace(/^4\d\d:\s*/, ""));
      } else {
        setErr(msg);
      }
      setPhase("done");
    }
  };

  const runFree = () => runFreeGoal(goal);

  const runs: AgentRun[] = result?.runs ?? freeResp?.runs ?? [];

  /** Q3：时序连锁原子化（/api/agent/compose_seq）——分句逐故障，绝不只取第一个。
   *  picks：本轮已点选的未锚定子句并入 [{clause, key}]；空 = 从头组合。
   *  组合中永远把用户**原始句**当 message 发后端，picks 按句累积——点一个候选
   *  不会把其他未锚定子句丢掉。 */
  const runCompose = async (g?: string, picks?: { clause: string; key: string }[]) => {
    const goalText = (g ?? goal).trim();
    const nextPicks = picks ?? [];
    if (!goalText || phase === "running" || composing) return;
    if (sys && !sys.engine.ok) {
      setErr("engine_missing");
      return;
    }
    setComposePicks(nextPicks);
    setComposing(true);
    setErr("");
    setGoalHint("");
    setComposeResp(null);
    begin();
    try {
      const r = await api.agentComposeSeq(goalText, nextPicks);
      setComposeResp(r);
      setPhase("done");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/^422:|^400:/.test(msg)) setGoalHint(msg.replace(/^4\d\d:\s*/, ""));
      else setErr(msg);
      setPhase("done");
    } finally {
      setComposing(false);
    }
  };

  /** 用户点选某未锚定子句的域候选 → 只并这一句（原句不变），其余子句仍可点 */
  const pickComposeClause = async (clause: string, key: string) => {
    if (!composeResp?.goal || phase === "running" || composing) return;
    const already = composePicks.some((p) => p.clause === clause && p.key === key);
    if (already) return;
    void runCompose(composeResp.goal, [...composePicks, { clause, key }]);
  };

  /** 把组合步骤送到 FaultLab 播放（与场景编排同通道） */
  const composeToFaultLab = (steps?: { at: number; action: string; fault?: string | null; node?: string | null; expect?: string | null }[]) => {
    if (!steps || steps.length === 0) return;
    try {
      sessionStorage.setItem("tcms.faultlab.draft", JSON.stringify({ name: "Agent 时序组合", from: "agent-compose", steps }));
    } catch {
      /* sessionStorage 不可用时仅跳页 */
    }
    window.location.href = "/faultlab";
  };

  const runDiagnose = async () => {
    const q = diagQ.trim();
    if (!q || diagBusy) return;
    setDiagBusy(true);
    setDiagErr("");
    setDiagResp(null);
    try {
      // P1-1：携带会话 id → 服务端锚点记忆；追问"刚才/那个部位"可沿用上一轮症状
      const r = await api.agentDiagnose(q, diagLlm, diagSidRef.current);
      diagSidRef.current = r.session_id ?? diagSidRef.current;
      setDiagResp(r);
    } catch (e) {
      setDiagErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDiagBusy(false);
    }
  };

  const resetDiagnose = () => {
    diagSidRef.current = null;
    setDiagResp(null);
    setDiagErr("");
  };

  return (
    <div className="mx-auto w-full max-w-[1720px] space-y-4">
      {/* 自由目标（像 DSH 一样：给 Agent 一句话，它先理解再查证） */}
      <Panel title="用大白话，直接给 Agent 一个目标" bodyClass="p-3">
        <textarea
          className="input resize-none"
          rows={2}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="用大白话描述你想验证的：如「车门故障了还能发车吗？」「超速后系统该怎么办」「验证紧急制动失败必须停车」"
          aria-label="自由目标输入"
          disabled={phase === "running"}
        />
        <div className="mt-2 flex flex-col sm:flex-row gap-2 items-stretch sm:items-center flex-wrap">
          <button
            className="btn justify-center sm:w-auto"
            onClick={() => void runFree()}
            disabled={phase === "running" || !goal.trim() || engineBlocked}
            title={engineBlocked ? "需先启用 TCMS 引擎" : "让 Agent 先去理解你的目标，再检索证据、真实执行"}
          >
            {phase === "running" ? "执行中…" : "✦ 让 Agent 去查证"}
          </button>
          <button
            className="btn-ghost justify-center sm:w-auto"
            onClick={() => void runCompose()}
            disabled={composing || !goal.trim() || engineBlocked}
            title={engineBlocked ? "需先启用 TCMS 引擎" : "让 Agent 把这句话理解成「多个故障的组合场景」并真实执行"}
          >
            {composing ? "组合中…" : "⧉ 组合成场景并执行"}
          </button>
          <span className="text-[11px] text-ink-faint leading-4">
            查证 = 单故障验证；组合 = 一句话编排多故障时序（如「先车门故障再叠加超速最后恢复」）→ 原子资产组合 → 真实执行。
          </span>
        </div>
      </Panel>

      {/* 症状/无码故障多跳诊断（不走引擎执行：检索症状资产 → 图谱因果链 → 建议；诚实标注） */}
      <Panel title="没有故障码？描述异常现象 → 图谱多跳诊断" bodyClass="p-3">
        <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
          <input
            className="input flex-1"
            value={diagQ}
            onChange={(e) => setDiagQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void runDiagnose();
            }}
            placeholder="如：仪表盘闪烁但无故障码 / SOC 跳变 / 网络时断时续…（无码症状 → 候选故障链 + 诊断建议）"
            aria-label="症状描述输入"
            disabled={diagBusy}
          />
          <button className="btn justify-center" onClick={() => void runDiagnose()} disabled={diagBusy || !diagQ.trim()}>
            {diagBusy ? "推理中…" : "🔎 症状诊断"}
          </button>
          {(diagSidRef.current || diagResp) && (
            <button className="btn-ghost justify-center" onClick={resetDiagnose} title="清空多轮记忆与会话，开始全新诊断">
              ⟲ 新会话
            </button>
          )}
          <label className="inline-flex items-center gap-1.5 text-[11px] text-ink-dim cursor-pointer select-none" title="开启后 LLM 只在候选故障内重排诊断顺序（不引入候选外故障键）；需在设置中配置 API key">
            <input type="checkbox" className="accent-info" checked={diagLlm} onChange={(e) => setDiagLlm(e.target.checked)} disabled={diagBusy} />
            LLM 候选内仲裁
          </label>
        </div>
        {diagErr && <div className="mt-2 text-[12px] text-bad">⚠ {diagErr}</div>}
        {diagResp?.llm_generated && (
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-vio/40 bg-vio/10 px-2 py-0.5 text-[10.5px] text-vio">
            ✦ 本次结果已由真实 LLM 在候选内重排仲裁（llm_generated=true）
          </div>
        )}
        {diagResp?.session_anchor_used && (
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-info/40 bg-info/10 px-2 py-0.5 text-[10.5px] text-info">
            ↩ 沿上一轮症状锚点继续诊断（证据引用式多轮记忆，不存摘要）
          </div>
        )}
        {diagResp && (
          <div className="mt-3 step-in space-y-2">
            {diagResp.matched && diagResp.symptom && (
              <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
                <Tag tone="info">症状资产 {diagResp.symptom.key}</Tag>
                <span className="text-ink">{diagResp.symptom.name}</span>
                {diagResp.symptom.domains?.length ? <span className="text-ink-faint">涉及域：{diagResp.symptom.domains.join("、")}</span> : null}
                <Tag tone="dim">诚实标注 {diagResp.symptom.annotation ?? "—"}</Tag>
                {diagResp.symptom.description ? <span className="text-ink-dim">· {diagResp.symptom.description}</span> : null}
              </div>
            )}
            <div className="whitespace-pre-line text-[13px] text-ink leading-6">{diagResp.reply}</div>
            {diagResp.clarification?.needs_more && (
              <div className="rounded-md border border-warn/50 bg-warn/5 px-3 py-2 text-[12px] leading-5 text-ink-dim">
                ⚠ <span className="text-warn">候选不可区分 —— 不硬排第一。</span>
                {diagResp.clarification.hint}
                {diagResp.clarification.distinguishing_observations &&
                  diagResp.clarification.distinguishing_observations.length > 0 && (
                    <ul className="mt-1 list-disc pl-4">
                      {diagResp.clarification.distinguishing_observations.map((o) => (
                        <li key={o}>{o}</li>
                      ))}
                    </ul>
                  )}
              </div>
            )}
            {diagResp.candidates.length > 0 && (
              <div className="grid gap-1.5 sm:grid-cols-2">
                {diagResp.candidates.map((c) => (
                  <div key={c.fault} className={`rounded-lg border px-2.5 py-2 text-[11.5px] leading-4 ${c.derived ? "border-warn/40 bg-warn/5" : "border-line bg-surface/40"}`}>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-ink font-medium">{c.name}</span>
                      <code className="kbd-mono">{c.fault}</code>
                      {c.derived && <Tag tone="warn">derived 示意</Tag>}
                      <span className="ml-auto text-ink-faint num" title="候选排序的依据充分性分数（排序分，非概率）">
                        {c.domain_zh} · 第{c.hop}跳 · 排序分 {c.confidence.toFixed(2)}
                      </span>
                    </div>
                    {c.check && <div className="mt-1 text-ink-dim">{c.check}</div>}
                    {c.notes?.[0] && <div className="mt-0.5 text-ink-faint">{c.notes[0]}</div>}
                    {c.scenarios && c.scenarios.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.scenarios.slice(0, 3).map((s) => (
                          <span key={s} className="tag text-info border-info/30 bg-info/5" title={s}>
                            复现 {s.replace(".yaml", "")}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            {diagResp.no_match && (
              <div className="space-y-2">
                <div className="text-[12px] text-ink-dim leading-5">
                  未匹配到症状资产 —— 不会编造故障码。请补充：部位/工况/是否伴随告警后重试；或改用上面“让 Agent 去查证”验证具体故障。
                </div>
                {diagResp.recommendation && (
                  <div className="rounded-md border border-info/40 bg-info/5 px-3 py-2 space-y-2">
                    <div className="whitespace-pre-line text-[12px] text-ink leading-5">{diagResp.recommendation.reply}</div>
                    {(diagResp.recommendation.faults?.length ?? 0) > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {diagResp.recommendation.faults!.map((f) => (
                          <button
                            key={f.key}
                            type="button"
                            className="tag text-info border-info/40 bg-info/10 hover:bg-info/20 cursor-pointer transition-colors"
                            title={`等级 ${f.level ?? "?"} · 处置 ${f.action ?? "?"}（点此在图谱中查看该真实故障）`}
                            onClick={() => {
                              window.location.href = `/graph?focus=${encodeURIComponent(`fault:${f.key}`)}`;
                            }}
                          >
                            {f.name} ({f.key})
                          </button>
                        ))}
                      </div>
                    )}
                    {(diagResp.recommendation.scenarios?.length ?? 0) > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {diagResp.recommendation.scenarios!.map((s) => (
                          <button
                            key={s.file}
                            type="button"
                            className="tag text-warn border-warn/40 bg-warn/5 hover:bg-warn/10 cursor-pointer transition-colors"
                            title="在 FaultLab 播放该复现场景"
                            onClick={() => {
                              window.location.href = `/faultlab?scenario=${encodeURIComponent(s.file)}&from=kb`;
                            }}
                          >
                            ▶ {s.name || s.file.replace(".yaml", "")}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {diagResp.related_assets && diagResp.related_assets.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {diagResp.related_assets.map((a) => (
                      <button
                        key={a.doc_id}
                        type="button"
                        className="tag text-info border-info/40 bg-info/10 hover:bg-info/20 cursor-pointer transition-colors"
                        title={a.text}
                        onClick={() => {
                          window.location.href = `/graph?focus=${encodeURIComponent(a.doc_id)}`;
                        }}
                      >
                        {a.doc_id.replace(/^(fault|scenario):/, "").replace(".yaml", "")}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <p className="mt-2 text-[11px] text-ink-faint leading-4">
          全离线确定性规则：候选全部锚定真实故障字典（202）；derived 候选仅示意，不可当已确认故障码（诚实红线）。完整证据链在回复的 candidates/evidence 中。
        </p>
      </Panel>

      {/* 内置任务（保留原有入口） */}
      <Panel title="或选一个内置真实测试任务" bodyClass="p-3">
        <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
          <select className="select flex-1" value={sel} onChange={(e) => setSel(e.target.value)} aria-label="选择任务" disabled={phase === "running"}>
            {tasks.map((t) => (
              <option key={t.task_id} value={t.task_id}>
                {t.title}
              </option>
            ))}
          </select>
          <button className="btn justify-center" onClick={() => run()} disabled={phase === "running" || !sel || engineBlocked} title={engineBlocked ? "需先启用 TCMS 引擎" : undefined}>
            {phase === "running" ? "执行中…" : "▶ 执行此任务"}
          </button>
          <button className="btn-ghost justify-center" onClick={() => run(tasks[0]?.task_id)} disabled={phase === "running" || tasks.length === 0 || engineBlocked}>
            运行全部
          </button>
        </div>
        {current && (
          <div className="mt-2.5 text-xs text-ink-dim leading-5">
            <Tag tone="dim">{current.task_id}</Tag> 目标故障 <code className="kbd-mono">{current.target_fault}</code> 期望处置{" "}
            <code className="kbd-mono">{current.expected_action}</code>
            <div className="mt-1">{current.goal}</div>
          </div>
        )}
        <div className="mt-2 text-[11px] text-ink-faint">
          也可以直接在上方输入你自己的目标，Agent 会先理解再查证——两者走的是同一条执行流水线。
        </div>
      </Panel>

      {/* 引擎缺失引导 */}
      {sys && !sys.engine.ok && (
        <div className="panel border-warn/30 bg-warn/5 p-4">
          <div className="text-sm font-medium text-warn flex items-center gap-2">⚠ 需要先启用 TCMS 引擎</div>
          <p className="text-[12px] text-ink-dim mt-1 leading-5">
            Agent 需要真实引擎去执行场景（检索和评分不依赖引擎，但"真实执行"那一步需要它）。
            启用后成功率才有意义——现在直接跑只会得到 0% 的假结果。
          </p>
          <div className="mt-2 text-[12px] text-ink mono space-y-0.5 bg-surface px-3 py-2 rounded-lg">
            <div>· pip install -e ".[upstream]"  （安装 tcms-can-test 引擎）</div>
            <div>· 或设置 TCMS_UPSTREAM_DIR 指向其目录后重启</div>
          </div>
          <div className="mt-2">
            <Link to="/scenarios" className="text-[12px] text-info hover:underline">
              资产浏览与图谱不依赖引擎，可先去体验 →
            </Link>
          </div>
        </div>
      )}

      {err === "engine_missing" && !(sys && !sys.engine.ok) && (
        <div className="panel border-bad/30 bg-bad/5 px-4 py-2.5 text-sm text-bad">引擎状态已变化，请刷新后重试。</div>
      )}

      {err && err !== "engine_missing" && (
        <div className="panel border-bad/30 bg-bad/5 px-4 py-2.5 text-sm text-bad">⚠ {err}</div>
      )}

      {/* 运行中：Agent 思考中…（步骤提示轮换 + pulse） */}
      {phase === "running" && !result && !freeResp && !composeResp && !goalHint && (
        <div className="panel px-4 py-4 flex items-start gap-3 step-in">
          <span className="mt-1.5 flex h-2.5 w-2.5">
            <span className="h-2.5 w-2.5 rounded-full bg-info pulse-dot" />
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-ink">Agent 思考中…</div>
            <div className="text-xs text-ink-dim mt-0.5 h-4">{RUN_HINTS[hintIdx % RUN_HINTS.length]}</div>
            <div className="mt-1">
              <SkeletonRows rows={1} cols={3} />
            </div>
          </div>
        </div>
      )}

      {/* 自由目标没锚定 → 引导：RAG 候选（可点选续跑）或换说法示例 */}
      {goalHint && phase === "done" && (
        <div className="panel px-4 py-5 step-in">
          <EmptyState
            icon="?"
            title="这句未能锚定到具体故障"
            desc={`${goalHint}${freeResp?.suggested_faults?.length ? " —— 但 AI 检索到了几个可能相关的真实故障，点选即可让 Agent 去查证：" : " —— 试试让目标里出现故障对象（如：车门故障 / 超速 / 心跳丢失）和期望（如：不能发车 / 降级 / 停车）。"}`}
          />
          {freeResp && freeResp.suggested_faults && freeResp.suggested_faults.length > 0 && (
            <div className="flex flex-wrap gap-1.5 justify-center pb-3">
              {freeResp.suggested_faults.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  className="tag text-info border-info/40 bg-info/10 hover:bg-info/20 cursor-pointer transition-colors text-left"
                  title={`等级 ${s.level ?? "?"} · 处置 ${s.action ?? "?"}（点击直接用这个故障让 Agent 查证）`}
                  onClick={() => void runFreeGoal(`验证${s.name ?? s.key}（${s.key}）`)}
                >
                  {s.name ?? s.key} <span className="opacity-70">({s.key})</span>
                </button>
              ))}
            </div>
          )}
          {freeResp?.kb_answer && (
            <div className="px-2 pb-2 -mt-1 whitespace-pre-line text-[12.5px] text-ink-dim leading-5 max-w-xl mx-auto text-left border-t border-line-soft pt-2">
              {freeResp.kb_answer}
            </div>
          )}
          <div className="flex flex-wrap gap-1.5 justify-center">
            {["车门故障了还能发车吗", "超速后系统该怎么办", "验证紧急制动失败必须停车"].map((ex) => (
              <Tag key={ex} tone="dim" onClick={() => setGoal(ex)}>
                {ex}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {/* Q3：组合结果（一句话 → 原子资产组合 → 真实执行） */}
      {composeResp && (
        <div className="step-in space-y-4">
          <Panel
            title={
              <>
                ⧉ Agent 组合的场景 <code className="kbd-mono ml-1">{composeResp.goal.slice(0, 40)}</code>
              </>
            }
            right={
              composeResp.composed ? (
                composeResp.run?.all_passed ? <Tag tone="ok">✓ 真实执行全部通过</Tag> : <Tag tone="bad">✗ 有断言未通过</Tag>
              ) : (
                <Tag tone="warn">需澄清</Tag>
              )
            }
            bodyClass="p-4"
          >
            {composeResp.chain_note && (
              <div className="mb-2 text-[12px] text-info/90 leading-5">{composeResp.chain_note}</div>
            )}
            {composeResp.interlock_note && (
              <div className="mb-2 rounded-md border border-vio/40 bg-vio/5 px-3 py-2">
                <div className="text-[11px] font-medium text-vio mb-1">⚙ 联锁联合提示（处置取决于原因）</div>
                <div className="text-[12px] text-ink-dim leading-5">{composeResp.interlock_note}</div>
                {composeResp.interlock_scenarios && composeResp.interlock_scenarios.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {composeResp.interlock_scenarios.map((s) => (
                      <span
                        key={s.file}
                        className="tag text-vio border-vio/40 bg-vio/10"
                        title={`覆盖严重安全原因 ${s.cause_fault} 的现成联锁场景（可在场景页真实执行）`}
                      >
                        {s.name} <span className="opacity-60">({s.file})</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
            {composeResp.composed ? (
              <>
                {/* 组合步骤（原子资产错峰注入/恢复） */}
                <div className="text-[11px] text-ink-faint uppercase tracking-wide mb-1.5">组合步骤（原子资产）</div>
                <div className="space-y-1 mb-3">
                  {composeResp.steps?.map((s, i) => (
                    <div key={i} className="text-[12px] font-mono text-ink-dim">
                      <span className="num text-ink">{s.at}s</span>{" "}
                      <span className={s.action === "inject" ? "text-warn" : "text-ok"}>{s.action}</span>{" "}
                      <span className="text-ink">{s.fault}</span>
                      {s.node ? <span className="text-ink-faint">@{s.node}</span> : null}
                      {s.expect ? <span className="text-info"> → 期望 {s.expect}</span> : null}
                    </div>
                  ))}
                </div>
                {/* 执行结果 */}
                {composeResp.run && (
                  <div className="panel bg-surface-2/40 p-3">
                    <div className="text-[11px] text-ink-faint mb-1.5">
                      真实引擎执行（v{composeResp.run.engine_version}）· PASS {composeResp.run.passed} / FAIL {composeResp.run.failed}
                    </div>
                    {composeResp.run.assertions?.map((a, i) => (
                      <div key={i} className="text-[11.5px] text-ink-dim leading-5">
                        <span className={a.passed ? "text-ok" : "text-bad"}>{a.passed ? "✓" : "✗"}</span>{" "}
                        {a.fault} @{a.ts}s 期望 {a.expected} → 实际 {a.actual}
                      </div>
                    ))}
                  </div>
                )}
                {/* Q7：三栏溯源（源资产 / 图谱事实 / Agent 建议）——让组合"不是黑箱" */}
                {composeResp.provenance && composeResp.provenance.length > 0 && (
                  <div className="mt-3">
                    <div className="text-[11px] text-ink-faint uppercase tracking-wide mb-1.5">
                      三栏溯源 · 每个故障的事实从哪来（引用真实资产更可信，Agent 不臆造）
                    </div>
                    <div className="space-y-2">
                      {composeResp.provenance.map((p) => (
                        <div key={p.fault} className="grid grid-cols-1 md:grid-cols-3 gap-1.5">
                          {/* 栏① 源资产（故障字典逐字段） */}
                          <div className="panel bg-surface-2/40 px-2.5 py-2 text-[11px]">
                            <div className="flex items-center gap-1.5 mb-1">
                              <Tag tone="warn">{p.fault}</Tag>
                              <span className="font-medium text-ink truncate">{p.name}</span>
                            </div>
                            <div className="text-ink-dim leading-4 space-y-0.5">
                              <div><span className="text-ink-faint">字典：</span>{p.asset.fid} · {p.asset.level} · 处置 {p.asset.action} · SIL {p.asset.sil}</div>
                              <div className="line-clamp-2"><span className="text-ink-faint">描述：</span>{p.asset.desc}</div>
                            </div>
                          </div>
                          {/* 栏② 图谱事实（隶属系统 + 覆盖场景） */}
                          <div className="panel bg-surface-2/40 px-2.5 py-2 text-[11px]">
                            <div className="text-[10px] text-ink-faint mb-1">图谱事实</div>
                            <div className="text-ink-dim leading-4">
                              <div>隶属系统：<span className="text-ink">{p.graph_facts.system}</span></div>
                              <div className="mt-0.5 text-ink-faint">覆盖场景（{p.graph_facts.scenarios.length}）：</div>
                              <div className="flex flex-wrap gap-1 mt-0.5">
                                {p.graph_facts.scenarios.slice(0, 3).map((s) => (
                                  <span key={s} className="tag text-info border-info/30 bg-info/5" title={s}>
                                    {scenName[s] ?? s.replace(".yaml", "")}
                                  </span>
                                ))}
                                {p.graph_facts.scenarios.length > 3 && <span className="text-ink-faint">+{p.graph_facts.scenarios.length - 3}</span>}
                              </div>
                            </div>
                          </div>
                          {/* 栏③ Agent 建议（期望处置） */}
                          <div className="panel bg-surface-2/40 px-2.5 py-2 text-[11px]">
                            <div className="text-[10px] text-ink-faint mb-1">Agent 建议</div>
                            <div className="text-ink-dim leading-4">
                              <div>期望处置：<code className="text-ok">{p.agent_action}</code></div>
                              <div className="mt-0.5 text-ink-faint line-clamp-2">检测手段：{p.asset.detect}</div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                <p className="text-[13px] text-ink-dim leading-6">{composeResp.reply}</p>
                {composeResp.fault_matches && composeResp.fault_matches.length > 0 && (
                  <div className="mt-2 text-[11px] text-ink-faint">
                    识别到：{composeResp.fault_matches.map((f) => f.name ?? f.key).join("、")}
                    {composeResp.followup_question ? ` —— ${composeResp.followup_question}` : ""}
                  </div>
                )}
              </>
            )}
            {/* 未锚定的子句 → 该域真实候选，点选继续补组（不自动发明、不尬住） */}
            {composeResp.unresolved && composeResp.unresolved.length > 0 && (
              <div className="mt-3 space-y-2">
                <div className="text-[11.5px] text-warn leading-5">
                  还有 {composeResp.unresolved.length} 句没锚定到唯一故障——下方每句都能点选并入；选一个并入后，
                  其余句子仍保留可继续点选（逐个并入，不消失）。
                </div>
                {composeResp.unresolved.map((u, ui) => (
                  <div key={ui} className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2">
                    <div className="text-[11.5px] text-ink-dim">
                      这段没锚定到唯一故障：<code className="kbd-mono">{u.clause}</code>
                    </div>
                    {u.domain_candidates?.faults && u.domain_candidates.faults.length > 0 && (
                      <>
                        <div className="mt-1 text-[11px] text-ink-faint">
                          {u.domain_candidates.domain_zh ?? "该域"}候选（点选后按此句在原句中的位置并入时序，真实键）：
                        </div>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          {u.domain_candidates.faults.map((f) => (
                            <button
                              key={f.key}
                              type="button"
                              className="tag text-info border-info/40 bg-info/10 hover:bg-info/20 cursor-pointer"
                              title={`等级 ${f.level ?? "?"} · 处置 ${f.action ?? "?"}（点击后并入本轮时序，其他未锚定句仍可继续选）`}
                              onClick={() => void pickComposeClause(u.clause, f.key)}
                            >
                              + {f.name} ({f.key})
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                    {!u.domain_candidates?.faults?.length && (
                      <div className="mt-1 text-[11px] text-ink-faint">
                        该句不在故障字典/域词表内——请换说法点名故障名或设备+现象。
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            {/* 下一步可操作（Agent 工作台不让用户"尬住"） */}
            <div className="mt-3 pt-2 border-t border-line-soft flex flex-wrap items-center gap-1.5">
              {composeResp.steps && composeResp.steps.length > 0 && (
                <button
                  className="btn btn-sm"
                  onClick={() => composeToFaultLab(composeResp.steps!)}
                  title="把当前组合步骤作为动画序列播放"
                >
                  ▶ 去 FaultLab 播放这组步骤
                </button>
              )}
              <button
                className="btn-ghost btn-sm"
                onClick={() => void runCompose(composeResp.goal, [])}
                title="回到用户原始句重新原子化（清空已点选并入）"
              >
                ↻ 重新组合
              </button>
              {!composeResp.composed &&
                composeResp.fault_matches &&
                composeResp.fault_matches.map((fm) => (
                  <button
                    key={fm.key}
                    className="btn-ghost btn-sm"
                    onClick={() => void runFreeGoal(`验证${fm.name ?? fm.key}（${fm.key}）`)}
                  >
                    单独查证：{fm.name ?? fm.key}
                  </button>
                ))}
            </div>
          </Panel>
        </div>
      )}

      {/* 运行中 / 结果：真实事件流（含管线进程视图）；no_match 时 runs 为空，不渲染 */}
      {phase !== "idle" && !freeResp?.no_match && (result || freeResp) && (
        <div className="step-in space-y-4">
          {/* 自由目标命中 → 先给人看 Agent 怎么理解这句话（后端契约：命中恒带 parsed） */}
          {freeResp && <GoalParseCard resp={freeResp} />}

          {runs.map((run, ri) => {
            const isRevealed = ri === 0; // 只对触发的首个任务做逐条揭示动效
            const showCount = isRevealed && phase === "done" ? Math.max(visible, 1) : run.trace.length;
            return (
              <Panel
                key={`${run.task_id}-${ri}`}
                title={
                  <>
                    <code className="kbd-mono">{run.task_id}</code>
                    <span className="text-ink font-normal ml-1">
                      {run.fault} → {run.expected}
                    </span>
                    {run.achieved ? <Tag tone="ok">✓ 达成</Tag> : <Tag tone="bad">✗ 未达成</Tag>}
                  </>
                }
                right={
                  <span className="text-[11px] text-ink-faint">
                    {phase === "running" && ri === 0 ? (
                      <span className="flex items-center gap-1.5">
                        <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" /> Agent 工作中
                      </span>
                    ) : (
                      <>
                        评分 <span className="text-info font-semibold num">{run.score.score}</span> · {run.duration_ms}ms
                        {run.reflected && " · 经反思"}
                      </>
                    )}
                  </span>
                }
                bodyClass="p-0"
              >
                {/* 管线进程视图：随 reveal 逐段点亮 */}
                <StepPipeline trace={run.trace} showCount={showCount} />
                <div className="px-4 py-2.5 space-y-0 border-t border-line-soft">
                  {run.trace.slice(0, showCount).map((t, i) => {
                    const m = STEP_META[t.step] ?? { label: t.step, tone: "info" as const };
                    return (
                      <div key={i} className="step-in flex items-start gap-2.5 py-1">
                        <Tag tone={m.tone}>{m.label}</Tag>
                        <div className="flex-1 text-[12.5px] text-ink leading-5 min-w-0">
                          <span className="break-words">{t.detail}</span>
                          <span className="text-ink-faint text-[10px] ml-1.5 num">+{t.t.toFixed(1)}s</span>
                        </div>
                      </div>
                    );
                  })}
                  {phase === "running" && ri === 0 && (
                    <div className="pt-2">
                      <SkeletonRows rows={1} cols={3} />
                    </div>
                  )}
                </div>
                <div className="px-4 py-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
                  <div className="bg-surface-2/50 rounded-lg py-2">
                    <div className="text-lg font-bold text-ok num">{run.score.evidence_count}</div>
                    <div className="text-[10px] text-ink-dim">条证据</div>
                  </div>
                  <div className="bg-surface-2/50 rounded-lg py-2">
                    <div className="text-lg font-bold text-info num">{run.attempts}</div>
                    <div className="text-[10px] text-ink-dim">次尝试</div>
                  </div>
                  <div className="bg-surface-2/50 rounded-lg py-2">
                    <div className={`text-lg font-bold num ${run.score.exec_passed ? "text-ok" : "text-bad"}`}>
                      {run.score.exec_passed ? "通过" : "未过"}
                    </div>
                    <div className="text-[10px] text-ink-dim">真实执行</div>
                  </div>
                  <div className="bg-surface-2/50 rounded-lg py-2">
                    <div className="text-lg font-bold text-vio num" title={run.scenario ?? undefined}>
                      {scenLabel(run.scenario)}
                    </div>
                    <div className="text-[10px] text-ink-dim">执行场景</div>
                  </div>
                </div>
                {/* Q7：四维雷达（fresume 式质量可视化：达成/证据/执行/反思） */}
                {run.score.radar && (
                  <div className="px-4 py-2.5 border-t border-line-soft flex flex-wrap items-center gap-x-6 gap-y-2">
                    <RadarFour axes={run.score.radar} />
                    <div className="space-y-0.5 text-[10.5px] text-ink-dim leading-4">
                      {[
                        ["达成", run.score.radar.goal_achieved, "text-ok"],
                        ["证据", run.score.radar.evidence_used, "text-info"],
                        ["执行", run.score.radar.exec_pass, "text-vio"],
                        ["反思", run.score.radar.reflection, "text-warn"],
                      ].map(([l, v, c]) => (
                        <div key={String(l)}>
                          <span className={String(c)}>●</span> {l} {String(v)}
                          <span className="text-ink-faint">/100</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {/* 看动画：把本次真实执行的场景送进 FaultLab 演示（资产化动画，非额定设置） */}
                {run.scenario && (
                  <div className="px-4 py-2 border-t border-line-soft flex items-center gap-2 flex-wrap">
                    <span className="text-[11px] text-ink-faint">
                      场景「{scenLabel(run.scenario)}」{run.scenario && scenName[run.scenario] ? <code className="kbd-mono">{run.scenario}</code> : null} 已真实执行完成
                    </span>
                    <a
                      className="btn-ghost btn-sm ml-auto shrink-0"
                      href={faultlabHref(run.scenario, "agent-exec")}
                      title="跳转 FaultLab，用动画回放这个场景的故障注入 → 检测 → 处置 → 恢复"
                    >
                      ▶ 看动画
                    </a>
                  </div>
                )}
                {/* 证据链（RAG 检索到哪些知识 → 供 Agent 决策） */}
                {run.evidence && run.evidence.length > 0 && (
                  <div className="px-4 py-2.5 border-t border-line-soft">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[11px] text-ink-faint font-medium uppercase tracking-wide">检索证据 · GraphRAG</span>
                      <span className="text-[10px] text-ink-faint num">{run.evidence.length} 条命中</span>
                    </div>
                    <div className="space-y-1">
                      {run.evidence.slice(0, 3).map((h, i) => (
                        <div key={i} className="flex items-start gap-2 text-[11.5px] leading-4 bg-surface-2/40 rounded-lg px-2.5 py-1.5">
                          <code className="kbd-mono shrink-0">{h.doc_id}</code>
                          <span className="text-ink-dim min-w-0 flex-1 line-clamp-1" title={h.text}>{h.text}</span>
                          <span className="text-ink-faint num shrink-0">{(h.score * 100).toFixed(0)}%</span>
                        </div>
                      ))}
                      {run.evidence.length > 3 && (
                        <div className="text-[10px] text-ink-faint pl-1">+{run.evidence.length - 3} 条…</div>
                      )}
                    </div>
                  </div>
                )}
                {/* 评审（KB 锚定规则评审, evaluator-optimizer） */}
                {run.review && (
                  <div className="px-4 pt-2 pb-3 border-t border-line-soft">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[11px] text-ink-faint font-medium uppercase tracking-wide">KB 锚定评审</span>
                      {run.review.passed ? <Tag tone="ok">通过</Tag> : <Tag tone="bad">未过</Tag>}
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(run.review.dimensions).map(([dim, st]) => (
                        <span
                          key={dim}
                          className={`tag ${
                            st === "pass"
                              ? "text-ok border-ok/30 bg-ok/5"
                              : st === "warn"
                                ? "text-warn border-warn/30 bg-warn/10"
                                : "text-bad border-bad/30 bg-bad/10"
                          }`}
                          title={DIM_LABELS[dim] ?? dim}
                        >
                          {st === "pass" ? "✓" : st === "warn" ? "△" : "✗"} {DIM_LABELS[dim] ?? dim}
                        </span>
                      ))}
                    </div>
                    {run.review.issues.length > 0 && (
                      <ul className="mt-1.5 space-y-0.5">
                        {run.review.issues.map((iss, k) => (
                          <li key={k} className="text-[11px] text-warn leading-4">· {iss}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </Panel>
            );
          })}
        </div>
      )}

      {phase === "idle" && !(sys && !sys.engine.ok) && (
        <Panel>
          <EmptyState
            icon="✦"
            title="Agent 会像测试工程师一样完成任务"
            desc="选一个任务（如「验证紧急制动执行失败必须触发 emergency_brake」），或直接在上方输入你自己的目标。它会先理解成故障+期望处置，再检索知识、选场景、真实执行，并把每一步做了什么实时列出来。"
          />
        </Panel>
      )}
    </div>
  );
}
