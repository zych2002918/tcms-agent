import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type FaultInfo, type RunScenarioResult, type ScenarioInfo } from "../api";
import { Panel, Tag, EmptyState, SkeletonRows } from "../components/ui";

/**
 * 手动编排（自定义故障场景）—— 契约已由队长确认（be-contracts t1，POST /api/run/custom）：
 *   runCustom({ name?, steps: [{ at, action, fault?, node?, level?, expect?, impact? }] }) → RunScenarioResult
 *   字段均可为 string|null；inject 步骤 fault 必填且须在故障字典，后端 422 校验。
 *
 * TODO(t1): api.ts 尚未加入 runCustom 类型/方法（t1 接线中）。
 * 这里用「可选访问 + 运行时检测」调用 api.runCustom——t1 把方法挂上后本页自动生效，
 * 不阻塞本页开发、也不与 t1 编辑 api.ts 冲突。若后端契约字段有出入，以 t1 类型为准。
 */
type CustomStepPayload = {
  at: number;
  action: "inject" | "recover";
  fault?: string | null;
  node?: string | null;
  level?: string | null;
  expect?: string | null;
  impact?: string | null;
};
type CustomScenarioPayload = { name?: string; steps: CustomStepPayload[] };
type RunCustomFn = (body: CustomScenarioPayload) => Promise<RunScenarioResult>;

/** 手动编排步骤行（字符串态，提交时才 parse/校验） */
type Row = {
  id: number;
  at: string;
  action: "inject" | "recover";
  fault: string;
  node: string;
  level: string;
  expect: string;
  impact: string;
  err: string;
};

const LEVELS = ["major", "minor", "critical", "info"];
const EXPECTS = ["emergency_brake", "derate", "shutdown", "warning", "none"];
const NODES = ["vcu", "bcu", "bms"];

/* ===== 跳 FaultLab（t5 跳转链）：把「这次真实执行」变成 FaultLab 动画 =====
 * 内置场景 → /faultlab?scenario=<file>&from=scenario-exec（fe-faultlab t4 读 ?scenario 直达加载）；
 * 自定义 runCustom → 步骤序列写 sessionStorage 通道 tcms.faultlab.draft（与 t4 键名一致），导航到纯 /faultlab
 * （t4 onMount 一次性消费并 removeItem → POST /api/faultlab/demo-steps；不带 query，按 t4 读取端约定）。
 * 通道契约对齐 t4：{ name?, from?: "scenario-exec"|"agent-exec", steps: FaultLabStepPayload[] }。 */
const FAULTLAB_DRAFT_KEY = "tcms.faultlab.draft";

function jumpToFaultLab(opts: { file?: string; name?: string; steps?: CustomStepPayload[] }) {
  if (opts.file) {
    const p = new URLSearchParams({ scenario: opts.file, from: "scenario-exec" });
    window.location.href = `/faultlab?${p.toString()}`;
    return;
  }
  if (opts.steps && opts.steps.length > 0) {
    try {
      sessionStorage.setItem(FAULTLAB_DRAFT_KEY, JSON.stringify({ name: opts.name || "自定义场景", from: "scenario-exec", steps: opts.steps }));
    } catch {
      /* sessionStorage 不可用（隐私模式等）→ 退化为纯导航，FaultLab 展示空态 */
    }
    window.location.href = `/faultlab`;
  }
}

/* ===== AI 编排顾问 —— POST /api/agent/advisor（be-core t2 已落盘，契约权威在 api.ts AdvisorTurnResp）=====
 * 请求：{ message, draft_steps?, history? }；响应永远 200（不 422）：
 *   reply + intent(match_fault|compose_scenario|clarify|out_of_domain|custom_proposal) +
 *   fault_matches（{key,name,action,level,confidence,matched_on} 候选，供一键填入步骤行）+
 *   suggested_steps（建议编排，供一键采纳）+ needs_clarification/followup_question（语义澄清）+
 *   rag_evidence（证据链透明）+ llm_generated（true=LLM 润色 / false=离线规则模板，诚实标注）+
 *   matched_fault + scenario_suggestions（覆盖该故障的现成场景，可一键运行）。
 * 本页先 UI 后接线：api.advisorTurn 挂上后自动走契约；未挂上 → 本地 fetch POST /api/agent/advisor；
 * 端点也 405/404/服务异常 → 本地启发式回复（绝不向用户抛“无法匹配/接口错误”）。 */
type AdvisorFaultMatch = {
  key?: string; // be-core 契约主字段（{key,name,...}）
  fault?: string; // 兼容本地兜底旧字段
  name?: string;
  level?: string;
  action?: string;
  confidence?: number;
  matched_on?: string;
};
type AdvisorStep = {
  at: number;
  action: "inject" | "recover";
  fault?: string | null;
  node?: string | null;
  level?: string | null;
  expect?: string | null;
  impact?: string | null;
};
type AdvisorHistoryItem = { role: "user" | "assistant"; content: string };
type AdvisorTurnRequest = { message: string; draft_steps?: AdvisorStep[]; history?: AdvisorHistoryItem[] };
type AdvisorRagEvidence = string | { doc_id?: string; kind?: string; text?: string; score?: number };
type AdvisorIntent = "match_fault" | "compose_scenario" | "clarify" | "out_of_domain" | "custom_proposal";
type AdvisorScenarioSuggestion = { file?: string; name?: string; steps?: number };
type AdvisorTurnResponse = {
  reply?: string;
  intent?: AdvisorIntent;
  fault_matches?: (AdvisorFaultMatch | string)[];
  suggested_steps?: AdvisorStep[];
  needs_clarification?: boolean;
  followup_question?: string | null;
  rag_evidence?: AdvisorRagEvidence[];
  llm_generated?: boolean; // true=LLM 润色文案；false=离线规则模板（诚实标注）
  matched_fault?: string;
  scenario_suggestions?: AdvisorScenarioSuggestion[];
};
type AdvisorFn = (body: AdvisorTurnRequest) => Promise<AdvisorTurnResponse>;

/** 对话消息（UI 态：把后端结构化字段摊平，便于渲染 chips / 采纳按钮） */
type ChatMsg = {
  id: number;
  role: "user" | "ai";
  text: string;
  matches?: AdvisorFaultMatch[];
  suggested?: AdvisorStep[];
  clarify?: boolean;
  followup?: string | null;
  evidence?: AdvisorRagEvidence[];
  offline?: boolean; // 本地兜底（顾问服务/LLM 未接线时）
  llmGenerated?: boolean; // 后端诚实标注：true=LLM 润色 / false=离线规则模板
  intent?: AdvisorIntent;
  scenarios?: AdvisorScenarioSuggestion[];
};

let _rid = 0;
const nextId = () => ++_rid;

/** 归一化 fault_matches：容忍 string[] 或 {key|fault,name,action,level,confidence,matched_on}[]。
 *  be-core 契约元素是 {key,name,action,level,confidence,matched_on}；key 是主键。 */
const normMatches = (m: unknown): AdvisorFaultMatch[] | undefined => {
  if (!Array.isArray(m) || !m.length) return undefined;
  const out: AdvisorFaultMatch[] = [];
  for (const x of m) {
    if (typeof x === "string") {
      if (x) out.push({ key: x, fault: x });
      continue;
    }
    if (x && typeof x === "object") {
      const o = x as Record<string, unknown>;
      const key = String(o.key ?? o.fault ?? "");
      if (key)
        out.push({
          key,
          fault: key,
          name: o.name != null ? String(o.name) : undefined,
          level: o.level != null ? String(o.level) : undefined,
          action: o.action != null ? String(o.action) : undefined,
          confidence: typeof o.confidence === "number" ? o.confidence : undefined,
          matched_on: o.matched_on != null ? String(o.matched_on) : undefined,
        });
    }
  }
  return out.length ? out : undefined;
};

/** 候选故障的主键（key ?? fault） */
const matchKey = (f: AdvisorFaultMatch): string => f.key ?? f.fault ?? "";

/** 归一化 suggested_steps：只收合法行，字段同 CustomStepPayload */
const normSteps = (m: unknown): AdvisorStep[] | undefined => {
  if (!Array.isArray(m) || !m.length) return undefined;
  const out: AdvisorStep[] = [];
  for (const x of m) {
    if (!x || typeof x !== "object") continue;
    const o = x as Record<string, unknown>;
    if (typeof o.at !== "number" || (o.action !== "inject" && o.action !== "recover")) continue;
    const s: AdvisorStep = { at: o.at, action: o.action };
    if (o.fault != null) s.fault = String(o.fault);
    if (o.node != null) s.node = String(o.node);
    if (o.level != null) s.level = String(o.level);
    if (o.expect != null) s.expect = String(o.expect);
    if (o.impact != null) s.impact = String(o.impact);
    out.push(s);
  }
  return out.length ? out : undefined;
};

/** 手动编排步骤行 → 提交/建议 payload（供 draft_steps 与 runCustom 共用） */
const rowsToSteps = (rows: Row[]): AdvisorStep[] =>
  rows.map((r) => {
    const step: AdvisorStep = {
      at: Number.parseFloat(r.at) || 0,
      action: r.action,
      fault: r.fault || null,
      node: r.node || null,
      level: r.level || null,
      expect: r.expect || null,
      impact: r.impact.trim() || null,
    };
    return step;
  });

/* ===== 本地兜底编排顾问（fetch 兜底也 404 时）=====
 * 目的：无论后端/LLM 是否就绪，任何输入都有友好回复（「不 422」语义）。
 * 用故障字典做轻量模糊匹配：key/name 子串 + 中文双字命中，给出候选 chips
 * 与澄清问题，绝不报「无法匹配」。后端就绪后此路径不再触发。 */

const zhBigrams = (s: string): string[] => {
  const out: string[] = [];
  for (let i = 0; i + 1 < s.length; i++) {
    if (/[\u4e00-\u9fff]/.test(s[i]) && /[\u4e00-\u9fff]/.test(s[i + 1])) out.push(s.slice(i, i + 2));
  }
  return out;
};
const faultScore = (f: FaultInfo, msg: string): number => {
  const m = msg.toLowerCase();
  let s = 0;
  if (f.key && m.includes(f.key.toLowerCase())) s += 20;
  const nm = (f.name || "").toLowerCase();
  if (nm && m.includes(nm)) s += 20;
  const big = zhBigrams(f.name || "");
  if (big.length) {
    const mb = zhBigrams(msg);
    for (const b of big) if (mb.includes(b)) s += 4;
  }
  return s;
};

const turnLocalAdvisor = (
  message: string,
  faults: FaultInfo[],
  draft?: Row[]
): AdvisorTurnResponse => {
  // 域外闲聊（天气/问候/与故障编排无关）→ 友好引导，不做故障匹配、不假装理解
  if (/^(今天|明天|天气|你好|hi|hello|谢谢|感谢|再见|拜拜|在吗|你会|你是谁|吃|饿|累|困)/i.test(message.trim()) || /天气|你好|吃饭|笑话|唱歌/.test(message)) {
    return {
      reply:
        "这个话题与故障编排无关——编排只处理 TCMS 注入/恢复步骤。可描述想验证的故障或行为，比如「车门故障不能发车」「超速时会不会降级」「恢复后状态是否复原」。",
      intent: "out_of_domain",
      needs_clarification: false,
      rag_evidence: [{ kind: "local-dict", text: "域外闲聊 → 本地兜底引导回故障编排域（顾问服务未接线）" }],
    };
  }
  const scored = faults
    .map((f) => ({ f, s: faultScore(f, message) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, 4);
  const lastAt = draft?.length ? Math.max(...draft.map((d) => Number.parseFloat(d.at) || 0)) : 0;

  if (scored.length) {
    const top = scored[0];
    const matches: AdvisorFaultMatch[] = scored.map(({ f, s }) => ({
      key: f.key,
      fault: f.key,
      name: f.name,
      level: f.level,
      action: f.action,
      confidence: Math.min(0.99, Math.round((s / 30) * 100) / 100),
    }));
    const uncertain = top.s < 8;
    const wantArrange = /排|时序|顺序|组合|帮我|建议/.test(message);
    const suggested: AdvisorStep[] = wantArrange
      ? [
          { at: Math.round(lastAt + 10), action: "inject", fault: top.f.key, level: top.f.level, expect: top.f.action },
          ...(draft?.some((d) => d.fault === top.f.key && d.action === "inject")
            ? [{ at: Math.round(lastAt + 20), action: "recover" as const, fault: top.f.key }]
            : []),
        ]
      : [];
    return {
      reply: uncertain
        ? `按故障字典做了模糊匹配，下面几个可能相关（${matches.map((x) => x.name ?? x.key).join("、")}）。点一个 chip 就能把它作为新步骤加进编排；或再描述下观察到的现象/想验证的行为。`
        : `已对上——「${top.f.name}（${top.f.key}）」在字典里：注入后期望处置 ${top.f.action ?? "—"}。点 chip 直接把它加为新步骤（会预填等级/期望），也可以继续说意图。`,
      intent: uncertain ? "clarify" : "match_fault",
      fault_matches: matches,
      suggested_steps: suggested.length ? suggested : undefined,
      rag_evidence: [{ kind: "local-dict", text: "故障字典模糊匹配（顾问服务未接线时的本地兜底，非 RAG 证据）" }],
    };
  }
  const hot = faults.slice(0, 5).map((f) => f.name).join("、");
  return {
    reply: `内存故障字典里没有直接对应这段描述——为排出能真实执行的步骤，需补充：① 想验证什么故障或行为（如「车门故障」「超速」）？② 大概在哪个设备、什么时候注入？也可以直接说意图，下面这些热门故障可供参考：${hot}。`,
    fault_matches: undefined,
    intent: "custom_proposal",
    needs_clarification: true,
    followup_question: "想验证的是哪类故障或行为？大概在什么设备/时间？",
    rag_evidence: [{ kind: "local-dict", text: "无匹配 → 本地兜底转向澄清（顾问服务未接线）" }],
  };
};

const defaultRows = (): Row[] => [
  { id: nextId(), at: "10", action: "inject", fault: "overspeed", node: "vcu", level: "major", expect: "derate", impact: "速度超过限速，期望降级", err: "" },
  { id: nextId(), at: "20", action: "recover", fault: "overspeed", node: "vcu", level: "", expect: "", impact: "", err: "" },
];

export function ScenariosPage() {
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [faults, setFaults] = useState<FaultInfo[]>([]);
  const [sel, setSel] = useState("");
  const [sys, setSys] = useState<{ engine: { ok: boolean; version?: string } } | null>(null);
  const [phase, setPhase] = useState<"idle" | "running" | "done" | "error">("idle");
  const [result, setResult] = useState<RunScenarioResult | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<"builtin" | "custom">("builtin");
  // 手动编排状态
  const [sceneName, setSceneName] = useState("");
  const [rows, setRows] = useState<Row[]>(defaultRows);
  // 编排指引（默认展开；用户收起后保持收起——状态跨模式保留）
  const [guideOpen, setGuideOpen] = useState(true);
  // AI 编排顾问对话状态
  const [chat, setChat] = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatPending, setChatPending] = useState(false);
  const chatBoxRef = useRef<HTMLDivElement | null>(null);
  // t5 跳转链：记住最近一次「真实执行的场景」（内置=文件；自定义=步骤序列），供结果区「看动画」跳 FaultLab
  const [lastRun, setLastRun] = useState<{ kind: "builtin" | "custom"; file?: string; name?: string; steps?: CustomStepPayload[] } | null>(null);
  // 行选故障的懒加载详情（检测/处置/恢复——列表接口不含，单条接口才有）
  const [faultDetail, setFaultDetail] = useState<Record<string, FaultInfo>>({});
  // t5 跳转链：AssetsPage「执行」可带 ?file=<场景文件> 直达并预选该场景
  const [searchParams] = useSearchParams();
  const selByFile = searchParams.get("file");
  const prevQueryFile = useRef<string | null>(null);

  // 读取 AssetsPage 跳转的 ?file=：预选该场景（不自动运行，留给人点「运行此场景」）
  useEffect(() => {
    if (!selByFile) return;
    if (prevQueryFile.current === selByFile) return; // 同一 file 只消费一次
    if (scenarios.some((s) => s.file === selByFile)) {
      prevQueryFile.current = selByFile;
      setSel(selByFile);
    }
    // scenarios 未就绪时本 effect 随 scenarios 更新重跑，直到命中
  }, [selByFile, scenarios]);

  // 首次进入自定义模式时给一条顾问引导语（含示例提问）
  useEffect(() => {
    if (mode !== "custom" || chat.length) return;
    setChat([
      {
        id: nextId(),
        role: "ai",
        text: "编排顾问就绪：把「想验证的情形」写下来，将按故障知识库（RAG）翻译成可执行的注入/恢复步骤。例如：",
        offline: false,
      },
    ]);
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api
      .scenarios()
      .then((s) => {
        setScenarios(s);
        if (s.length) setSel(s[0].file);
      })
      .catch((e) => setErr(String(e)));
    api.faults().then(setFaults).catch(() => undefined);
    api.systemStatus().then(setSys).catch(() => undefined);
  }, []);

  const current = scenarios.find((s) => s.file === sel);
  const engineOk = sys?.engine.ok ?? true;
  const faultOpts = useMemo(() => [...faults].sort((a, b) => a.key.localeCompare(b.key)), [faults]);

  /** 每行字段更新 */
  const patchRow = (id: number, p: Partial<Row>) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p, err: "" } : r)));

  const actionOf = (id: number) => rows.find((r) => r.id === id)?.action ?? "inject";

  /** 选故障后：inject 行若等级/期望仍空，用故障字典真实值预填（机器自证，可再改） */
  const pickFault = (id: number, key: string) => {
    const f = faults.find((x) => x.key === key);
    const r = rows.find((x) => x.id === id);
    const patch: Partial<Row> = { fault: key };
    if (r?.action === "inject" && f) {
      if (!r.level && LEVELS.includes(f.level)) patch.level = f.level;
      if (!r.expect && EXPECTS.includes(f.action)) patch.expect = f.action;
    }
    patchRow(id, patch);
  };

  /** 步骤行级校验（不合法 → 行内红字，阻止提交）。同步计算 next 行再 setState，保证返回值可靠。 */
  const validateRows = (): boolean => {
    const next = rows.map((r) => {
      const at = Number.parseFloat(r.at);
      const msgs: string[] = [];
      if (!Number.isFinite(at) || at <= 0) msgs.push("时间需大于 0 秒");
      if (!r.fault) msgs.push(r.action === "inject" ? "inject 行需选故障" : "需选要恢复的故障");
      return { ...r, err: msgs.join("；") };
    });
    setRows(next);
    return !next.some((r) => r.err);
  };

  const runBuiltin = async () => {
    if (!sel || phase === "running") return;
    if (!engineOk) {
      setErr("engine_missing");
      return;
    }
    setPhase("running");
    setResult(null);
    setErr("");
    setLastRun({ kind: "builtin", file: sel }); // t5：记住本次执行的场景文件，完成后可跳 FaultLab
    try {
      const r = await api.runScenario(sel);
      setResult(r);
      setPhase("done");
    } catch (e) {
      setErr(String(e));
      setPhase("error");
    }
  };

  /** 提交自定义场景：校验 → api.runCustom（t1 接线后自动可用） */
  const runCustom = async () => {
    if (phase === "running") return;
    if (!engineOk) {
      setErr("engine_missing");
      return;
    }
    const fn = (api as unknown as { runCustom?: RunCustomFn }).runCustom;
    if (!fn) {
      setErr("自定义场景执行接口尚未就绪（api.runCustom 由后端契约 t1 接线中，完成后本页自动可用）");
      return;
    }
    if (!validateRows()) return; // 行内红字提示
    const steps: CustomStepPayload[] = rows.map((r) => {
      const step: CustomStepPayload = {
        at: Number.parseFloat(r.at),
        action: r.action,
        fault: r.fault || null, // inject 必填（后端 422 校验）；recover 指定要恢复的故障
        node: r.node || null,
        level: r.level || null,
        expect: r.expect || null,
        impact: r.impact.trim() || null,
      };
      return step;
    });
    const body: CustomScenarioPayload = { steps };
    if (sceneName.trim()) body.name = sceneName.trim();
    setPhase("running");
    setResult(null);
    setErr("");
    setLastRun({ kind: "custom", name: sceneName.trim() || undefined, steps }); // t5：记住步骤序列，完成后可跳 FaultLab 按资产组合演示
    try {
      const r = await fn(body);
      setResult(r);
      setPhase("done");
    } catch (e) {
      setErr(String(e));
      setPhase("error");
    }
  };

  /* ===== AI 编排顾问 ===== */

  const chatPush = (m: ChatMsg) => setChat((c) => [...c, m]);

  /** 发送用户消息 → api.advisorTurn → 渲染回复（永不因“无匹配”拒绝） */
  const sendChat = async () => {
    const message = chatInput.trim();
    if (!message || chatPending) return;
    setChatInput("");
    chatPush({ id: nextId(), role: "user", text: message });
    setChatPending(true);
    const lastTurn = (() => {
      // 取最后一条「完整回合」：跳过最近一条可能还在兜底的 AI 消息
      const out: AdvisorHistoryItem[] = [];
      const msgs = [...chat];
      if (msgs.length && msgs[msgs.length - 1].role === "ai") msgs.pop();
      for (const m of msgs.slice(-6)) out.push({ role: m.role === "user" ? "user" : "assistant", content: m.text });
      return out;
    })();
    const req: AdvisorTurnRequest = { message, draft_steps: rowsToSteps(rows), history: lastTurn };
    let resp: AdvisorTurnResponse | null = null;
    let offline = false;

    // 1) 契约优先：api.advisorTurn（be-core t2 挂上后自动走这条）
    const fn = (api as unknown as { advisorTurn?: AdvisorFn }).advisorTurn;
    if (fn) {
      try {
        resp = await fn(req);
      } catch {
        resp = null; // 掉到 fetch 兜底
      }
    }
    // 2) 本地 fetch 兜底（后端已实现但 t2 未同步 api.ts）
    if (!resp) {
      let httpNote = "";
      try {
        const r = await fetch("/api/agent/advisor", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req),
        });
        if (r.ok) {
          resp = (await r.json()) as AdvisorTurnResponse;
        } else if (r.status === 404 || r.status === 405) {
          httpNote = `顾问服务尚未接线（HTTP ${r.status}）——先用本地故障字典兜底`;
          resp = null; // 端点尚未实现 → 本地启发式
        } else {
          const detail = await r.text();
          httpNote = `顾问服务暂时没接上（${r.status} ${detail.slice(0, 120)}）——先用本地故障字典兜底`;
          resp = null; // 服务异常 → 也走本地启发式，绝不空手
        }
      } catch (e) {
        httpNote = `顾问服务未就绪（${String(e).slice(0, 100)}）——先用本地故障字典兜底`;
        resp = null;
      }
      // 3) 本地启发式兜底（端点未实现 / 服务异常 / 网络失败——一律接管，保证有可用回复）
      if (!resp) {
        offline = true;
        resp = turnLocalAdvisor(message, faults, rows);
        if (httpNote) resp.reply = `${httpNote}：\n${resp.reply ?? ""}`;
      }
    }

    chatPush({
      id: nextId(),
      role: "ai",
      text: resp.reply ?? "收到，继续处理。",
      matches: normMatches(resp.fault_matches),
      suggested: normSteps(resp.suggested_steps),
      clarify: !!resp.needs_clarification,
      followup: resp.followup_question ?? null,
      evidence: resp.rag_evidence,
      offline,
      llmGenerated: resp.llm_generated,
      intent: resp.intent,
      scenarios: resp.scenario_suggestions,
    });
    setChatPending(false);
  };

  /** 点候选故障 chip：优先填选中行的 fault；否则新增一步 */
  const adoptFault = (f: AdvisorFaultMatch) => {
    const fk = matchKey(f);
    if (!fk) return;
    const row = rows.find((r) => r.fault === "" || r.fault === fk) ?? rows.find((r) => r.action === "inject" && !r.fault);
    if (row) {
      const patch: Partial<Row> = { fault: fk };
      if (f.level && LEVELS.includes(f.level)) patch.level = f.level;
      if (f.action && EXPECTS.includes(f.action)) patch.expect = f.action;
      patchRow(row.id, patch);
      return;
    }
    setRows((rs) => [
      ...rs,
      {
        id: nextId(),
        at: String(Math.max(0, ...rs.map((r) => Number.parseFloat(r.at) || 0)) + 10),
        action: "inject",
        fault: fk,
        node: "vcu",
        level: f.level && LEVELS.includes(f.level) ? f.level : "",
        expect: f.action && EXPECTS.includes(f.action) ? f.action : "",
        impact: f.name ? `${f.name}——期望 ${f.action ?? "处置"}（顾问推荐）` : "",
        err: "",
      },
    ]);
  };

  /** 采纳顾问建议步骤：时间偏移防冲突后追加 */
  const adoptSteps = (steps: AdvisorStep[]) => {
    const next: Row[] = [...rows];
    let base = rows.length ? Math.max(...rows.map((r) => Number.parseFloat(r.at) || 0)) : 0;
    for (const s of steps) {
      const at = Math.max(0.1, s.at > base ? s.at : base + 10); // 与已有行时间错开
      next.push({
        id: nextId(),
        at: String(Math.round(at * 10) / 10),
        action: s.action,
        fault: s.fault ?? "",
        node: s.node ?? "vcu",
        level: s.level ?? "",
        expect: s.expect ?? "",
        impact: s.impact ?? "",
        err: "",
      });
      base = at;
    }
    setRows(next);
  };

  /** 问句一键填充到输入框 */
  const askFollowup = () => {
    if (!chatInput.trim() && chat.length) {
      const last = chat[chat.length - 1];
      if (last.role === "ai" && last.followup) setChatInput(last.followup);
    }
  };

  // 新 AI 消息后自动滚到底
  useEffect(() => {
    if (chatBoxRef.current) chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight;
  }, [chat, chatPending]);

  // 懒加载行故障详情（检测/注入/恢复——列表接口不含详情，避免逐行请求）
  useEffect(() => {
    if (mode !== "custom") return;
    let alive = true;
    for (const r of rows) {
      if (!r.fault || faultDetail[r.fault]) continue;
      api
        .fault(r.fault)
        .then((f) => {
          if (alive) setFaultDetail((d) => ({ ...d, [f.key]: f }));
        })
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, [rows, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const modeTab = (m: "builtin" | "custom", label: string) => (
    <button
      onClick={() => setMode(m)}
      className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
        mode === m ? "text-ink border-info/50 bg-info/10" : "text-ink-dim border-line hover:text-ink"
      }`}
    >
      {label}
    </button>
  );

  /** 编排指引开合 */
  const toggleGuide = () => setGuideOpen((o) => !o);

  /** 渲染单条 AI 顾问消息（文本 + 候选 chips + 建议步骤 + 澄清 + 证据折叠） */
  const renderAiMsg = (m: ChatMsg) => (
    <div className="space-y-2">
      {m.text && <p className="text-[13px] text-ink leading-5 whitespace-pre-wrap">{m.text}</p>}
      {m.intent === "out_of_domain" && (
        <p className="text-[10px] text-warn/80">（这句话偏离了 TCMS 故障编排域——顾问已给出引导；说故障/现象/意图才能排步骤）</p>
      )}
      {m.intent === "custom_proposal" && (
        <p className="text-[10px] text-vio/80">（字典未收录 → 自定义新故障引导流程：补齐注入方式/影响/期望处置，顾问组装草稿）</p>
      )}
      {m.offline && (
        <p className="text-[10px] text-ink-faint">（顾问服务未接线 · 本地故障字典兜底回复）</p>
      )}
      {m.llmGenerated === false && !m.offline && (
        <p className="text-[10px] text-ink-faint">（离线规则模板回复 · 未接 LLM，建议真实、可解释）</p>
      )}
      {m.llmGenerated === true && (
        <p className="text-[10px] text-ok/70">（回复文案由 LLM 润色 · 决策仍锚定真实故障字典）</p>
      )}
      {m.matches && m.matches.length > 0 && (
        <div className="pt-0.5">
          <div className="text-[10px] text-ink-faint mb-1">候选故障（点一下填入步骤）：</div>
          <div className="flex flex-wrap gap-1.5">
            {m.matches.map((f, i) => {
              const fk = matchKey(f);
              const meta = f.name
                ? `${f.name} · ${fk}`
                : `${faults.find((x) => x.key === fk)?.name ?? ""} ${fk}`.trim();
              return (
                <button
                  key={`${fk}-${i}`}
                  type="button"
                  onClick={() => adoptFault(f)}
                  className="tag text-info border-info/40 bg-info/10 hover:bg-info/20 cursor-pointer transition-colors text-left"
                  title={f.level && f.action ? `等级 ${f.level} · 期望处置 ${f.action}` : "点击填入步骤"}
                >
                  + {meta}
                  {f.confidence != null && <span className="opacity-60 num">{Math.round(f.confidence * 100)}%</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {m.suggested && m.suggested.length > 0 && (
        <div className="panel bg-surface-2/50 p-2 rounded-lg">
          <div className="text-[10px] text-ink-faint mb-1">建议编排（已按你的现有步骤避让时间）：</div>
          <ol className="space-y-0.5">
            {m.suggested.map((s, i) => (
              <li key={i} className="text-[11px] font-mono text-ink-dim">
                {s.at}s · {s.action === "inject" ? "注入" : "恢复"} {s.fault ?? "—"}
                {s.node ? ` @${s.node}` : ""}
                {s.expect ? ` → 期望 ${s.expect}` : ""}
              </li>
            ))}
          </ol>
          <button type="button" className="btn-ghost btn-sm mt-1.5" onClick={() => adoptSteps(m.suggested!)}>
            ＋ 采纳建议步骤（追加到下方编排表）
          </button>
        </div>
      )}
      {m.clarify && (
        <div className="panel border-warn/30 bg-warn/5 px-2.5 py-2 rounded-lg">
          <div className="text-[11px] text-warn flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-warn pulse-dot" />
            顾问需要澄清一下，才能给出能真实执行的步骤
          </div>
          {m.followup && (
            <div className="mt-1.5 flex items-center gap-2">
              <span className="text-[12px] text-ink-dim">{m.followup}</span>
              <button type="button" className="btn-ghost btn-sm !py-1 shrink-0" onClick={askFollowup}>
                用这句问我 →
              </button>
            </div>
          )}
        </div>
      )}
      {m.evidence && m.evidence.length > 0 && (
        <details className="text-[11px]">
          <summary className="text-ink-faint cursor-pointer select-none hover:text-ink-dim">查看检索证据（透明）</summary>
          <ul className="mt-1.5 space-y-1 pl-1 border-l-2 border-line-soft">
            {m.evidence.map((e, i) => {
              const line =
                typeof e === "string"
                  ? e
                  : [e?.kind, e?.doc_id, e?.text, e?.score != null ? `score ${e.score}` : ""].filter(Boolean).join(" · ");
              return (
                <li key={i} className="text-ink-faint leading-4.5 pl-2">
                  {line}
                </li>
              );
            })}
          </ul>
        </details>
      )}
      {m.intent === "match_fault" && m.scenarios && m.scenarios.length > 0 && (
        <div className="panel bg-surface-2/50 p-2 rounded-lg">
          <div className="text-[10px] text-ink-faint mb-1">现成场景可直接验证（点一下真实执行）：</div>
          <div className="flex flex-wrap gap-1.5">
            {m.scenarios.map((s, i) => {
              const file = s.file ?? "";
              return (
                <button
                  key={`${file}-${i}`}
                  type="button"
                  className="tag text-ok border-ok/40 bg-ok/10 hover:bg-ok/20 cursor-pointer transition-colors"
                  onClick={() => {
                    if (file) {
                      setSel(file);
                      setMode("builtin");
                    }
                  }}
                  title={`${file} · ${s.steps ?? "?"} 步编排`}
                >
                  ▶ {s.name ?? file}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );

  /** 单步行控件（带小标签） */
  const field = (label: string, children: React.ReactNode, extraCls = "") => (
    <label className={`flex flex-col gap-0.5 text-[10px] text-ink-faint ${extraCls}`}>
      {label}
      {children}
    </label>
  );
  const ctrl = "input !py-1.5 !px-2 text-[12px]";

  return (
    <div className="mx-auto w-full max-w-[1720px] space-y-4">
      {/* 顶部：数量来源说明 + 模式切换 + 执行区 */}
      <Panel title="运行故障场景" bodyClass="p-3">
        {/* 数量来源说明（机器自证：N = 实际拉到的场景数） */}
        <p className="text-[11px] text-ink-faint leading-4.5 mb-2.5">
          ℹ 这里的 <span className="num">{scenarios.length}</span> 个场景来自当前资产源
          （真实引擎目录 <code className="kbd-mono">scenarios/</code> 或内置快照）—— 每个文件 = 一个场景；
          往里加 <code className="kbd-mono">scenarios/*.yaml</code> 即可自动出现在这里（无需改代码）。
        </p>

        {/* 内置 vs 手动编排 切换 */}
        <div className="flex items-center gap-1.5 mb-2.5">
          {modeTab("builtin", "▤ 内置场景")}
          {modeTab("custom", "✎ 手动编排")}
          {mode === "custom" && <span className="ml-auto text-[11px] text-ink-faint">自己编排故障注入 / 恢复步骤，真实执行</span>}
        </div>

        {mode === "builtin" ? (
          <>
            <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
              <select className="select flex-1" value={sel} onChange={(e) => setSel(e.target.value)} aria-label="选择场景">
                {scenarios.map((s) => (
                  <option key={s.file} value={s.file}>
                    {s.name} — {s.file}
                  </option>
                ))}
              </select>
              <button className="btn justify-center" onClick={runBuiltin} disabled={phase === "running" || !sel || !engineOk}>
                {phase === "running" ? "运行中…" : "▶ 运行此场景"}
              </button>
            </div>
            {current && (
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs">
                <span className="text-ink-faint">注入故障：</span>
                {current.fault_keys.map((f) => (
                  <Tag key={f} tone="warn">
                    {f}
                  </Tag>
                ))}
                <span className="text-ink-faint ml-2">涉及节点：</span>
                {current.nodes.map((n) => (
                  <Tag key={n} tone="dim">
                    {n}
                  </Tag>
                ))}
                <span className="ml-auto text-ink-faint">{current.steps} 步编排</span>
              </div>
            )}
          </>
        ) : (
          /* ---------- 手动编排编辑器 ---------- */
          <div className="space-y-2">
            {/* 编排指引（场景名 = 叙事标签；步骤 = 注入脚本） */}
            <div className="panel border-info/25 bg-info/5 overflow-hidden">
              <button
                type="button"
                onClick={toggleGuide}
                className="w-full flex items-center gap-2 px-3 py-2 text-left text-[12px] font-medium text-ink hover:bg-info/5"
                aria-expanded={guideOpen}
              >
                <span className={`inline-block transition-transform ${guideOpen ? "rotate-90" : ""}`}>▸</span>
                <span>编排指引：场景名称与故障步骤是什么关系？</span>
                <span className="ml-auto text-[10px] text-ink-faint font-normal">{guideOpen ? "收起" : "展开"}</span>
              </button>
              {guideOpen && (
                <div className="px-3.5 pb-3 space-y-2.5 text-[12px] leading-5">
                  <div className="grid sm:grid-cols-2 gap-2">
                    <div className="panel bg-surface/70 p-2.5 rounded-lg">
                      <div className="text-info text-[11px] font-semibold mb-1">场景名称 = 叙事标签</div>
                      <p className="text-ink-dim text-[12px] leading-4.5">
                        给人看的：这段测试在验证什么情形。像一幕剧的<b>剧名</b>——方便沉淀、汇报与复用，
                        引擎不执行它。例：<span className="text-ink">「车门故障后超速级联」</span>。
                      </p>
                    </div>
                    <div className="panel bg-surface/70 p-2.5 rounded-lg">
                      <div className="text-info text-[11px] font-semibold mb-1">故障步骤 = 注入脚本</div>
                      <p className="text-ink-dim text-[12px] leading-4.5">
                        给引擎执行的：<b>何时(at)</b> 往哪个设备(node) 注入/恢复(recover) 哪个故障(fault)，
                        注入时断言期望处置(expect)。像剧的<b>台词与走位</b>——决定真实结果。
                      </p>
                    </div>
                  </div>
                  <div className="panel bg-surface/70 p-2.5 rounded-lg">
                    <div className="text-[11px] font-semibold text-ink-dim mb-1.5">微型示例：名称一句话 + 步骤三行（索引 vs 脚本）</div>
                    <div className="text-[11px] text-ink-dim mb-1">
                      <span className="kbd-mono text-ink">场景名称</span>：车门故障级联（我这次想验证：车门故障时超速也会被正确处置，且故障恢复后解除）
                    </div>
                    <div className="space-y-0.5">
                      {[
                        ["10s", "inject", "door_fault", "bcu", "→ 期望 derate", "车门打不开故障：请求降级"],
                        ["20s", "inject", "overspeed", "vcu", "→ 期望 derate", "叠加超速：仍应降级而非崩溃"],
                        ["30s", "recover", "door_fault", "bcu", "", "恢复车门故障：确认解除"],
                      ].map((c, i) => (
                        <div key={i} className="font-mono text-[11px] flex flex-wrap gap-x-2 text-ink-dim">
                          <span className="num text-ink">{c[0]}</span>
                          <span className={c[1] === "inject" ? "text-warn" : "text-ok"}>{c[1]}</span>
                          <span className="text-ink">{c[2]}</span>
                          <span className="text-ink-faint">@{c[3]}</span>
                          {c[4] && <span className="text-info">{c[4]}</span>}
                          <span className="text-ink-faint">{c[5]}</span>
                        </div>
                      ))}
                    </div>
                    <p className="text-[10px] text-ink-faint mt-1">
                      名：我要验证什么情形（沉淀/汇报用）；步骤：引擎按时间轴注入/恢复什么故障（决定执行结果）。名与步骤不必一一对应，一个好名字能概括一组步骤的意图。
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-ink-dim">
                    <span><span className="num text-ok font-semibold">1</span>　起个场景名称（可选，但建议）——想验证什么情形，一句话说清</span>
                    <span><span className="num text-ok font-semibold">2</span>　加步骤：每步 = 何时(at) + 对哪个设备(node) + 注入/恢复(recover) 哪个故障(fault)，注入可写期望处置(expect)</span>
                    <span><span className="num text-ok font-semibold">3</span>　点「执行这个自定义场景」真实跑一遍</span>
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              <label className="text-[11px] text-ink-faint">
                场景名称（可选）
                <input
                  className="input mt-0.5 font-mono text-[12px]"
                  placeholder="如：自定义超速降级演练"
                  value={sceneName}
                  onChange={(e) => setSceneName(e.target.value)}
                />
              </label>
            </div>

            {rows.length === 0 ? (
              <div className="panel bg-surface-2/40 px-3 py-4 text-center text-xs text-ink-faint">
                还没有步骤——点「+ 添加一步」开始编排。
              </div>
            ) : (
              <div className="space-y-1.5">
                {rows.map((r) => (
                  <div key={r.id} className="panel bg-surface-2/40 p-2.5">
                    <div className="flex flex-wrap items-end gap-1.5">
                      {field("时间 at（秒）", (
                        <input
                          className={`${ctrl} w-20`}
                          type="number"
                          min="0.1"
                          step="0.1"
                          placeholder="10"
                          value={r.at}
                          onChange={(e) => patchRow(r.id, { at: e.target.value })}
                        />
                      ))}
                      {field("动作", (
                        <select
                          className={`select !py-1.5 !px-2 text-[12px] w-24`}
                          value={r.action}
                          onChange={(e) =>
                            patchRow(r.id, {
                              action: e.target.value as Row["action"],
                              level: e.target.value === "recover" ? "" : r.level,
                              expect: e.target.value === "recover" ? "" : r.expect,
                            })
                          }
                        >
                          <option value="inject">inject（注入）</option>
                          <option value="recover">recover（恢复）</option>
                        </select>
                      ))}
                      {field("故障", (
                        <select
                          className={`select !py-1.5 !px-2 text-[12px] w-56`}
                          value={r.fault}
                          onChange={(e) => pickFault(r.id, e.target.value)}
                        >
                          <option value="">（选故障）</option>
                          {faultOpts.map((f) => (
                            <option key={f.key} value={f.key}>
                              {f.name} {f.key}
                            </option>
                          ))}
                          {r.fault && !faults.some((f) => f.key === r.fault) && <option value={r.fault}>{r.fault}</option>}
                        </select>
                      ))}
                      {actionOf(r.id) === "inject" && (
                        <>
                          {field("节点", (
                            <select
                              className="select !py-1.5 !px-2 text-[12px] w-20"
                              value={r.node}
                              onChange={(e) => patchRow(r.id, { node: e.target.value })}
                            >
                              {NODES.map((n) => (
                                <option key={n} value={n}>
                                  {n}
                                </option>
                              ))}
                            </select>
                          ))}
                          {field("等级（可选）", (
                            <select
                              className="select !py-1.5 !px-2 text-[12px] w-24"
                              value={r.level}
                              onChange={(e) => patchRow(r.id, { level: e.target.value })}
                            >
                              <option value="">（选填）</option>
                              {LEVELS.map((l) => (
                                <option key={l} value={l}>
                                  {l}
                                </option>
                              ))}
                            </select>
                          ))}
                          {field("期望处置（可选）", (
                            <select
                              className="select !py-1.5 !px-2 text-[12px] w-32"
                              value={r.expect}
                              onChange={(e) => patchRow(r.id, { expect: e.target.value })}
                            >
                              <option value="">（选填）</option>
                              {EXPECTS.map((x) => (
                                <option key={x} value={x}>
                                  {x}
                                </option>
                              ))}
                            </select>
                          ))}
                          {field("影响说明（可选）", (
                            <input
                              className={`${ctrl} min-w-40 flex-1`}
                              placeholder="中文描述该故障的影响…"
                              value={r.impact}
                              onChange={(e) => patchRow(r.id, { impact: e.target.value })}
                            />
                          ))}
                        </>
                      )}
                      <button
                        className="btn-ghost btn-sm !px-2 shrink-0"
                        title="删除此步"
                        onClick={() => setRows((rs) => rs.filter((x) => x.id !== r.id))}
                      >
                        ✕
                      </button>
                    </div>
                    {r.err && <div className="text-[11px] text-bad mt-1">⚠ {r.err}</div>}
                    {/* 懒加载的故障详情小字：帮用户理解这一步引擎会怎么检测/处置/恢复（不喧宾夺主） */}
                    {actionOf(r.id) === "inject" && r.fault && faultDetail[r.fault] && (
                      <div className="text-[10px] text-ink-faint leading-4 mt-1.5 border-t border-line-soft/60 pt-1.5 flex flex-wrap gap-x-4 gap-y-0.5">
                        {faultDetail[r.fault].detect && <span>检测：{faultDetail[r.fault].detect}</span>}
                        {faultDetail[r.fault].inject && <span>注入：{faultDetail[r.fault].inject}</span>}
                        {faultDetail[r.fault].recovery && <span>恢复：{faultDetail[r.fault].recovery}</span>}
                        {faultDetail[r.fault].desc && <span className="w-full">{faultDetail[r.fault].desc}</span>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2 pt-0.5">
              <button
                className="btn-ghost btn-sm"
                onClick={() => setRows((rs) => [...rs, { id: nextId(), at: "", action: "inject", fault: "", node: "vcu", level: "", expect: "", impact: "", err: "" }])}
              >
                + 添加一步
              </button>
              <button className="btn btn-sm justify-center" onClick={runCustom} disabled={phase === "running" || !engineOk}>
                {phase === "running" ? "执行中…" : "▶ 执行这个自定义场景"}
              </button>
              {!engineOk && <span className="text-[11px] text-warn">需要先启用 TCMS 引擎（见下方说明）</span>}
            </div>
            <p className="text-[10px] text-ink-faint">
              步骤按时间先后执行：inject 注入故障并断言期望处置，recover 恢复故障。恢复（recover）步骤需要指定要恢复的故障。
            </p>

            {/* AI 编排顾问 —— 多轮对话；任何输入都有回复，不因无法匹配而拒绝 */}
            <div className="panel border-vio/25 overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-2 border-b border-line-soft bg-surface-2/30">
                <span className="text-vio text-[12px]">◇</span>
                <span className="text-[12px] font-semibold text-ink">AI 编排顾问</span>
                <span className="text-[10px] text-ink-faint">描述「想验证的情形」 → 翻译成可执行步骤（RAG 检索 + 规则编排）</span>
              </div>
              <div ref={chatBoxRef} className="px-3 py-2.5 space-y-2.5 max-h-80 overflow-y-auto">
                {chat.map((m) => (
                  <div key={m.id} className={m.role === "user" ? "flex justify-end" : ""}>
                    <div
                      className={
                        m.role === "user"
                          ? "max-w-[85%] bg-info/15 border border-info/30 rounded-xl rounded-tr-sm px-3 py-2 text-[13px] text-ink whitespace-pre-wrap"
                          : "max-w-[92%] bg-surface-2/50 border border-line/70 rounded-xl rounded-tl-sm px-3 py-2"
                      }
                    >
                      {m.role === "ai" ? renderAiMsg(m) : m.text}
                    </div>
                  </div>
                ))}
                {chatPending && (
                  <div className="flex items-center gap-2 text-[11px] text-ink-faint">
                    <span className="h-1.5 w-1.5 rounded-full bg-vio pulse-dot" />
                    顾问思考中：检索故障知识 → 匹配意图…
                  </div>
                )}
                {chat.length === 0 && (
                  <p className="text-[11px] text-ink-faint">
                    示例提问：「验证车门故障时不能发车」「门好像有问题」「我想测紧急停车时空调会不会关」。
                  </p>
                )}
              </div>
              <div className="flex items-end gap-2 border-t border-line-soft px-3 py-2.5 bg-surface-2/20">
                <input
                  className="input !py-2 text-[13px]"
                  placeholder="说你的意图 / 故障现象 / 编排求助…（回车发送）"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void sendChat();
                    }
                  }}
                  disabled={chatPending}
                />
                <button
                  type="button"
                  className="btn btn-sm justify-center shrink-0"
                  onClick={() => void sendChat()}
                  disabled={chatPending || !chatInput.trim()}
                >
                  {chatPending ? "…" : "发送"}
                </button>
              </div>
            </div>
          </div>
        )}
      </Panel>

      {/* 引擎缺失引导 */}
      {sys && !engineOk && (
        <div className="panel border-warn/30 bg-warn/5 p-4">
          <div className="text-sm font-medium text-warn flex items-center gap-2">⚠ 运行场景需要 TCMS 引擎</div>
          <p className="text-[12px] text-ink-dim mt-1 leading-5">
            场景由真实 TCMS 引擎执行（资产浏览与知识图谱不需要它）。启用方法：
          </p>
          <div className="mt-2 text-[12px] text-ink mono space-y-0.5 bg-surface px-3 py-2 rounded-lg">
            <div>· pip install -e ".[upstream]"  （从 GitHub 安装 tcms-can-test 引擎）</div>
            <div>· 或设置环境变量 TCMS_UPSTREAM_DIR 指向 tcms-can-test 目录后重启服务</div>
          </div>
        </div>
      )}

      {/* 运行中：真实引擎在工作（诚实加载态） */}
      {phase === "running" && (
        <Panel
          title="TCMS 引擎执行中…"
          right={
            <span className="flex items-center gap-1.5 text-[11px] text-ink-dim">
              <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" /> 仿真 + 断言中
            </span>
          }
          bodyClass="py-3"
        >
          <SkeletonRows rows={3} cols={4} />
          <p className="text-[11px] text-ink-faint mt-2">
            引擎正在：装载场景 → 初始化虚拟时钟 → 注入故障推进时间线 → 逐条断言期望处置
          </p>
        </Panel>
      )}

      {phase === "error" && (
        <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">
          {err === "engine_missing" ? "⚠ 需要先启用 TCMS 引擎（见上方说明）" : `⚠ 执行失败：${err}`}
        </div>
      )}

      {phase === "done" && result && (
        <div className="step-in space-y-4">
          <Panel
            title={
              (() => {
                // 运行结果标题：优先显示场景中文名（file→name 映射），文件保留作次要标识
                const nm = scenarios.find((s) => s.file === result.scenario || s.name === result.scenario)?.name;
                return (
                  <>
                    <span className="text-ink">运行完成</span> ·{" "}
                    {nm ? <span className="text-ink font-medium">{nm}</span> : <code className="kbd-mono">{result.scenario}</code>}
                    {nm && nm !== result.scenario && <code className="kbd-mono ml-1">{result.scenario}</code>}
                    {result.run_id && (
                      <span className="text-ink-faint text-xs font-normal"> · run {result.run_id}</span>
                    )}
                  </>
                );
              })()
            }
            right={
              result.all_passed ? (
                <Tag tone="ok">PASS</Tag>
              ) : (
                <Tag tone="bad">FAIL</Tag>
              )
            }
            bodyClass="p-3"
          >
            {/* t5 跳转链：把「这次执行」送进 FaultLab 用动画重放（资产化动画，不是额定设置） */}
            {(() => {
              const canJump = lastRun?.kind === "builtin" && lastRun.file
                ? { href: `/faultlab?scenario=${encodeURIComponent(lastRun.file)}&from=scenario-exec` }
                : lastRun?.kind === "custom" && lastRun.steps && lastRun.steps.length > 0
                  ? { draft: true, name: lastRun.name }
                  : null;
              return canJump ? (
                <div className="flex items-center gap-2 mb-3 flex-wrap">
                  <a
                    className="btn btn-sm justify-center"
                    href={canJump.draft ? "/faultlab" : (canJump as { href: string }).href}
                    onClick={
                      canJump.draft
                        ? (e) => {
                            e.preventDefault();
                            jumpToFaultLab({ name: canJump.name, steps: lastRun?.steps });
                          }
                        : undefined
                    }
                    title="跳转 FaultLab，用动画回放本次执行的故障注入 → 检测 → 处置 → 恢复"
                  >
                    ▶ 用动画看这次执行
                  </a>
                  <span className="text-[11px] text-ink-faint">
                    {lastRun?.kind === "custom"
                      ? `把刚才手动编排的 ${lastRun.steps?.length ?? 0} 步序列作为资产组合送进 FaultLab`
                      : `场景 ${lastRun?.file} 按真实资产时间线回放`}
                  </span>
                </div>
              ) : null;
            })()}
            <div className="grid grid-cols-3 gap-3 max-w-sm">
              <div className="panel bg-surface-2/50 px-3 py-2">
                <div className="stat-num text-ok num">{result.passed}</div>
                <div className="text-[11px] text-ink-dim">断言通过</div>
              </div>
              <div className="panel bg-surface-2/50 px-3 py-2">
                <div className={`stat-num num ${result.failed ? "text-bad" : "text-ink-faint"}`}>{result.failed}</div>
                <div className="text-[11px] text-ink-dim">失败</div>
              </div>
              <div className="panel bg-surface-2/50 px-3 py-2">
                <div className="stat-num text-ink-dim num text-[20px] mt-1.5">v{result.engine_version}</div>
                <div className="text-[11px] text-ink-dim">TCMS 引擎</div>
              </div>
            </div>
            <div className="table-scroll mt-3">
              <table>
                <thead>
                  <tr>
                    <th className="th">时间</th>
                    <th className="th">故障</th>
                    <th className="th">期望处置</th>
                    <th className="th">实际处置</th>
                    <th className="th">结果</th>
                  </tr>
                </thead>
                <tbody>
                  {result.assertions.map((a, i) => (
                    <tr key={i} className="tr-hover">
                      <td className="td num">{a.ts}s</td>
                      <td className="td">
                        <code className="kbd-mono">{a.fault}</code>
                      </td>
                      <td className="td kbd-mono">{a.expected}</td>
                      <td className="td kbd-mono">{a.actual}</td>
                      <td className="td">
                        {a.passed ? (
                          <Tag tone="ok">✓ 通过</Tag>
                        ) : (
                          <Tag tone="bad">✗ 未通过</Tag>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      )}

      {phase === "idle" && engineOk && (
        <Panel>
          <EmptyState
            icon="▶"
            title="选好场景后点「运行此场景」"
            desc="引擎会真实执行：注入故障 → 推进时间线 → 断言期望处置。结果沉淀到知识库（run 记录）。"
          />
        </Panel>
      )}
    </div>
  );
}
