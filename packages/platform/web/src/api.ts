/** 后端 API 客户端（/api 代理到 FastAPI）。 */

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

export interface Stats {
  version: string;
  source_upstream: string;
  messages: number;
  signals: number;
  devices: number;
  faults: number;
  scenarios: number;
  requirements: number;
  req_ids: number;
  functions: number;
}

/** /api/health：双段版本（平台自身 / 资产模型 / 上游引擎） */
export interface HealthResp {
  status: string;
  version: string;
  asset_version: string;
  engine_version: string | null;
}

export interface MessageInfo {
  name: string;
  frame_id: string;
  node: string;
  cycle_ms: number | null;
  send_type: string;
  signals: string[];
}

export interface SignalInfo {
  name: string;
  message: string;
  unit: string;
  choices: { value: number; label: string }[];
}

export interface FaultInfo {
  fid: string;
  key: string;
  name: string;
  subsystem: string;
  layer: string;
  level: string;
  action: string;
  sil: string;
  desc?: string;
  detect?: string;
  inject?: string;
  recovery?: string;
  /** 处置条件化说明（action_note：单一默认处置背后的"取决于原因"语义，可为空） */
  action_note?: string;
}

export interface ScenarioInfo {
  file: string;
  name: string;
  steps: number;
  fault_keys: string[];
  nodes: string[];
  desc?: string; // 场景简短释义（YAML desc，可选）
}

export interface FunctionInfo {
  fid: string;
  name: string;
  description: string;
  messages: string[];
  signals: string[];
  fault_keys: string[];
  requirements: string[];
}

export interface RequirementRow {
  req_id: string;
  rows: { module: string; test_file: string; verifies: string }[];
}

export interface KbNode {
  id: string;
  kind: string;
  label: string;
  props?: Record<string, unknown>;
}

export interface KbSearchHit {
  doc_id: string;
  kind: string;
  text: string;
  score: number;
  domain?: string; // Q4 分区标签（如 network/door）
  graph_neighbors: { id: string; kind: string; label: string; via: string }[];
}

export interface KbSearchResp {
  query: string;
  routed_domains?: string[]; // Q4：图谱路由到的分区域（有界检索）
  routed_zh?: string[];
  bounded?: boolean; // true = 域内检索；false = 全局回退
  mixed_fallback?: boolean; // true = 域内不足已回退补召回
  hits: KbSearchHit[];
}

export interface KbSubgraph {
  seed: string;
  depth: number;
  nodes: { id: string; kind: string; label: string }[];
  edges: { src: string; dst: string; kind: string }[];
  node_count: number;
}

export interface RunScenarioResult {
  scenario: string;
  steps: number;
  assertions: { fault: string; ts: number; expected: string; actual: string; passed: boolean }[];
  passed: number;
  failed: number;
  all_passed: boolean;
  engine_version: string;
  run_id?: string;
}

export interface FaultLabEvent {
  t: number;
  kind: "inject" | "detect" | "action" | "recover" | "note";
  fault: string;
  label: string;
  detail: string;
  level: string;
  action: string;
  derived: boolean;
  /** 引擎观察窗：结构化数据来源（引擎断言 / 故障字典 / 场景 YAML / 示意物理），
   *  由 faultlab 各事件 source{kind,ref,desc} 提供（t2 engine-observer 落地）。 */
  source?: { kind: string; ref: string; desc: string };
  /** 高亮提示增强（哪里异常 + 什么异常）：真实故障字典派生，事件到哪都能自解释 */
  fault_name?: string;
  subsystem?: string; // 位置：故障字典子系统（照明/辅助电源/网络…）
  domain_zh?: string; // 位置：13 系统域中文标签（照明/辅助供电/网络列车控制…）
}

export interface FaultLabCurvePoint {
  t: number;
  speed_kmh: number;
  brake_kpa: number;
  eb: number;
  doors_open: number;
  door_fault_count: number;
  heartbeat_ok: boolean;
  bus_ok: boolean;
  pantograph_ok: boolean;
  soc: number;
  action: string;
  alarms: string[];
}

export interface FaultLabResp {
  demo: {
    scenario: string;
    scenario_name: string;
    faults: string[];
    steps: number;
    duration: number;
    sample_s: number;
    events: FaultLabEvent[];
    params: { limit_kmh: number; derate_speed: number; cruise_kmh: number; eb_kpa: number };
    honesty: string;
    /** 引擎真实执行的黑盒开窗（透明数据管线）：真实断言证据 + 结构/示意说明。
     *  引擎可用且真实执行时 asserted=true、version="x.y.z"；未接入引擎时
     *  asserted=false、version=null（后端 faultlab_demo 已附 engine_version）。 */
    engine?: {
      asserted: boolean;
      version: string | null;
      trace: unknown[];
      assertions: { fault: string; ts: number; expected: string; actual: string; passed: boolean }[];
      notes: string[];
    };
    /** 数据管线透明展示：demo 怎么从真实资产/引擎构建出来的可读步骤 + 真实/示意常量表。 */
    pipeline?: {
      title: string;
      steps: { name: string; desc: string; kind: string }[];
      constants?: { real?: ConstRow[]; schematic?: ConstRow[] };
    };
  };
  curve: FaultLabCurvePoint[];
  engine_asserted: boolean;
  honesty_note: string;
}

/** 数据管线常量行（pipeline.constants 条目：真实阈值/枚举 vs 示意规则）。 */
export interface ConstRow {
  name: string;
  value: string | number;
  unit: string;
  source: string;
  desc: string;
}

export const api = {
  stats: () => req<Stats>("/stats"),
  health: () => req<HealthResp>("/health"),
  systemStatus: () =>
    req<{
      engine: { ok: boolean; version?: string; reason?: string };
      llm_key: boolean;
      asset_mode: string;
      capabilities: Record<string, boolean>;
      fix_hints: { engine: string[]; llm: string[] };
    }>("/system/status"),
  messages: () => req<MessageInfo[]>("/messages"),
  signals: () => req<SignalInfo[]>("/signals"),
  faults: () => req<FaultInfo[]>("/faults"),
  fault: (key: string) => req<FaultInfo>(`/faults/${key}`),
  scenarios: () => req<ScenarioInfo[]>("/scenarios"),
  functions: () => req<FunctionInfo[]>("/functions"),
  requirements: () => req<RequirementRow[]>("/requirements"),
  kbStats: () => req<{ graph: { nodes: number; edges: number; by_kind: Record<string, number> }; vector: { docs: number; by_kind: Record<string, number> } }>("/kb/stats"),
  kbSearch: (query: string, k = 5) =>
    req<KbSearchResp>("/kb/search", { method: "POST", body: JSON.stringify({ query, k }) }),
  kbSubgraph: (seed: string, depth = 2) =>
    req<KbSubgraph>("/kb/subgraph", { method: "POST", body: JSON.stringify({ seed, depth }) }),
  /** 默认“基础关联图谱”骨架（未搜索/未选种子时展示）：13 系统 + 11 功能 + 每功能代表故障。 */
  kbOverview: (limit = 3) => req<KbSubgraph>(`/kb/overview?limit=${limit}`),
  kbNodes: (kind?: string, q?: string) => {
    const p = new URLSearchParams();
    if (kind) p.set("kind", kind);
    if (q) p.set("q", q);
    const qs = p.toString();
    return req<KbNode[]>(`/kb/nodes${qs ? `?${qs}` : ""}`);
  },
  kbNode: (id: string) =>
    req<{ id: string; kind: string; label: string; props: Record<string, unknown>; neighbors: KbNode[] }>(
      `/kb/node/${encodeURIComponent(id)}`
    ),
  runScenario: (scenario: string) =>
    req<RunScenarioResult>("/run/scenario", { method: "POST", body: JSON.stringify({ scenario }) }),
  faultlabScenarios: () =>
    req<{ file: string; name: string; desc?: string; steps: number; fault_keys: string[]; duration_hint: number }[]>("/faultlab/scenarios"),
  faultlabDemo: (scenario: string) =>
    req<FaultLabResp>("/faultlab/demo", { method: "POST", body: JSON.stringify({ scenario }) }),
  faultlabDemoSteps: (body: DemoFromStepsRequest) =>
    req<FaultLabResp>("/faultlab/demo-steps", { method: "POST", body: JSON.stringify(body) }),
  agentTasks: () =>
    req<{ task_id: string; title: string; goal: string; target_fault: string; expected_action: string }[]>("/agent/tasks"),
  agentRun: (taskId?: string) =>
    req<AgentRunResp>("/agent/run", { method: "POST", body: JSON.stringify({ task_id: taskId ?? null }) }),
  agentFree: (goal: string) =>
    req<AgentFreeResp>("/agent/free", { method: "POST", body: JSON.stringify({ goal }) }),
  agentCompose: (message: string) =>
    req<AgentComposeResp>("/agent/compose", { method: "POST", body: JSON.stringify({ message }) }),
  /** 时序连锁原子化：先A后B随后C最后D → 逐原子故障错峰注入 + 真实执行。
   *  picks：已点选并入的未锚定子句 [{clause, key}]——message 恒为用户原始句，
   *  每次点选累积传入，其余未锚定子句保留在响应里继续可点。 */
  agentComposeSeq: (message: string, picks: { clause: string; key: string }[] = []) =>
    req<AgentComposeResp>("/agent/compose_seq", { method: "POST", body: JSON.stringify({ message, picks }) }),
  /** 症状多跳诊断（无故障码症状 → 图谱因果链候选 + 诊断建议；derived 显式标注；
   *  use_llm=true 启用 LLM 候选内仲裁——仅重排候选、需已配置 key；
   *  session_id=P1-1 多轮锚点记忆键（追问"刚才/那个部位"可沿用上一轮症状锚点） */
  agentDiagnose: (message: string, use_llm = false, session_id?: string | null) =>
    req<DiagnoseResp>("/agent/diagnose", {
      method: "POST",
      body: JSON.stringify({ message, use_llm, ...(session_id ? { session_id } : {}) }),
    }),
  advisorTurn: (body: AdvisorTurnRequest) =>
    req<AdvisorTurnResp>("/agent/advisor", { method: "POST", body: JSON.stringify(body) }),
  runCustom: (body: CustomScenarioRequest) =>
    req<RunScenarioResult & { custom: boolean }>("/run/custom", { method: "POST", body: JSON.stringify(body) }),
  settingsGet: () => req<SettingsView>("/settings"),
  settingsSave: (patch: Record<string, string | boolean>) =>
    req<SettingsView>("/settings", { method: "POST", body: JSON.stringify(patch) }),
  settingsClearApiKey: () => req<SettingsView>("/settings/clear-api-key", { method: "POST", body: JSON.stringify({}) }),
  /** 测试 LLM 连通性并拉取可用模型列表（OpenAI 兼容 GET /models）。
   *  base_url/api_key 可选：显式传入仅本次探测不落库。ok=false 时 error 为中文引导。 */
  llmModels: (body: { base_url?: string; api_key?: string } = {}) =>
    req<{ ok: boolean; models: { id: string; owned_by?: string; created?: number }[]; error: string | null }>(
      "/llm/models",
      { method: "POST", body: JSON.stringify(body) }
    ),
};

/** 症状多跳诊断响应（/api/agent/diagnose） */
export interface DiagnoseCandidate {
  fault: string;
  name: string;
  domain: string;
  domain_zh: string;
  hop: number;
  basis: "real_mechanism" | "derived";
  confidence: number;
  level: string;
  action: string;
  sil: string;
  check: string;
  scenarios: string[];
  notes: string[];
  derived: boolean;
}
export interface DiagnoseResp {
  query: string;
  matched: boolean;
  no_match: boolean;
  symptom: { key: string; name: string; domains: string[]; annotation: string; score: number; description?: string } | null;
  uncertain: boolean;
  reply: string;
  candidates: DiagnoseCandidate[];
  plan: {
    step: number;
    phase: string;
    fault: string;
    name: string;
    domain: string;
    domain_zh: string;
    hop: number;
    basis: string;
    confidence: number;
    description: string;
    scenarios: string[];
    note: string;
    derived: boolean;
  }[];
  evidence: Record<string, unknown>;
  no_fault_code_invented: boolean;
  llm_generated: boolean;
  /** P1-2：候选不可区分时追问需补充的区分性观测（不硬排第一）；无则 null */
  clarification?: {
    needs_more: boolean;
    kind?: string;
    between?: { fault: string; name: string }[];
    distinguishing_observations?: string[];
    hint?: string;
  } | null;
  /** P1-1：本轮是否沿上一轮症状锚点继续（证据引用式多轮记忆） */
  session_anchor_used?: boolean;
  session_id?: string | null;
  /** no_match 时给出的“可能相关资产”（真实 fault/scenario，可点击跳图谱） */
  related_assets?: { doc_id: string; kind: string; text: string }[];
  /** 宽泛问法推理（域词×故障句式 → 定向推荐真实故障/场景） */
  recommendation?: {
    kind?: string;
    domain?: string;
    domain_zh?: string;
    reply?: string;
    faults?: { key: string; name: string; level?: string; action?: string }[];
    scenarios?: { file: string; name: string }[];
    count?: number;
  } | null;
}

export interface AgentRunResp {
  total: number;
  achieved: number;
  success_rate: number;
  review_passed: number;
  runs: {
    task_id: string;
    fault: string;
    expected: string;
    achieved: boolean;
    attempts: number;
    reflected: boolean;
    scenario: string | null;
    duration_ms: number;
    score: { score: number; achieved: boolean; evidence_count: number; exec_passed: boolean; reflected: boolean; radar?: { goal_achieved: number; evidence_used: number; exec_pass: number; reflection: number } };
    review: {
      task_id: string;
      passed: boolean;
      dimensions: Record<string, string>;
      issues: string[];
    };
    evidence: {
      doc_id: string;
      kind: string;
      score: number;
      text: string;
      neighbors: { id: string; kind: string; label: string; via: string }[];
    }[];
    trace: { step: string; detail: string; t: number }[];
  }[];
}

export interface SettingsView {
  dir: string;
  llm: { provider: string; base_url: string; model: string; has_key: boolean };
  asset_dir: string;
  port: number;
  onboarding_done: boolean;
  theme: string; // dark / light / ""（前端偏好，后端透传）
  providers: Record<string, { label: string; base_url: string; model: string }>;
}

// ---- 自由 Agent 目标（/api/agent/free）----

export interface AgentFreeParsed {
  fault: string;
  fault_name: string;
  expected: string;
  expected_zh: string;
  confidence: number;
  resolver: "rule" | "llm";
  matched_on: string;
}

/** /api/agent/free 响应：解析结果 + 与 /api/agent/run 同构的执行报告。
 *  规则未命中真实故障时返回 no_match=true + suggested_faults（RAG 候选，可点选续跑）。 */
export interface AgentFreeResp extends AgentRunResp {
  goal: string;
  parsed?: AgentFreeParsed;
  matched_task_id?: string; // T-FREE-1（自由任务动态生成）
  no_match?: boolean; // true = 未锚定，见 suggested_faults
  detail?: string;
  suggested_faults?: {
    key: string;
    name: string;
    action: string;
    level: string;
    confidence: number;
    matched_on: string;
  }[];
  rag_evidence?: { doc_id: string; kind: string; score: number; text: string }[];
  followup_question?: string;
  /** 无 LLM/未锚定时，对“仅告警/降级但仍可运行”类盘点问题返回的规则枚举回答 */
  kb_answer?: string;
  kb_items?: { kind?: string; count?: number; shown?: { key: string; name: string; level?: string; action?: string }[] };
}

/** /api/agent/compose：一句话 → 原子资产组合 → 真实执行（Q3）。 */
export interface ComposeProvenance {
  fault: string;
  name: string;
  asset: { fid: string; level: string; action: string; sil: string; desc: string; detect: string; inject: string };
  graph_facts: { system: string; scenarios: string[] };
  agent_action: string;
}
export interface AgentComposeResp {
  goal: string;
  composed: boolean;
  intent: string;
  fault_matches?: { key: string; name: string; action: string; level: string }[];
  steps?: CustomStep[];
  provenance?: ComposeProvenance[]; // Q7 三栏溯源
  run?: {
    scenario: string;
    passed: number;
    failed: number;
    all_passed: boolean;
    assertions: { fault: string; ts: number; expected: string; actual: string; passed: boolean }[];
    engine_version: string;
  };
  reply?: string;
  needs_clarification?: boolean;
  followup_question?: string;
  rag_evidence?: unknown[];
  /** compose_seq（时序连锁原子化）新增字段 */
  faults?: string[];
  chain_note?: string;
  final_action?: string;
  /** 联锁联合提示：门/牵引域故障 + EB 收尾 → 处置取决于原因（EB 环线 vs derate） */
  interlock_note?: string;
  interlock_scenarios?: { file: string; name: string; cause_fault: string }[];
  unresolved?: {
    clause: string;
    domain_candidates?: {
      kind?: string;
      domain?: string;
      domain_zh?: string;
      reply?: string;
      faults?: { key: string; name: string; level?: string; action?: string }[];
      scenarios?: { file: string; name: string }[];
    } | null;
  }[];
}

// ---- 自定义场景执行（/api/run/custom）----

/** 自定义场景单步（与内置场景 YAML 步骤同构）。 */
export interface CustomStep {
  at: number;
  action: "inject" | "recover";
  fault?: string | null;
  node?: string | null;
  level?: string | null;
  expect?: string | null;
  impact?: string | null;
}

export interface CustomScenarioRequest {
  name?: string;
  steps: CustomStep[];
}

// ---- FaultLab 任意序列动画（/api/faultlab/demo-steps）----

/** 从任意故障序列（非已存场景文件）生成演示动画的请求。
 *  steps 与 CustomStep 同构：{at, action, fault, node?, level?, expect?, impact?}。 */
export interface DemoFromStepsRequest {
  name?: string; // 自定义序列名（demo.scenario = "custom/<name>"）
  steps: CustomStep[];
}

// ---- 编排顾问对话（/api/agent/advisor）----

/** 顾问单步请求：用户一句话（任何内容都不该 422）+ 可选当前编排草稿/历史。 */
export interface AdvisorTurnRequest {
  message: string;
  draft_steps?: CustomStep[];
  history?: { role: string; content: string }[];
}

/** 顾问输出：reply 中文回复；intent 见注释；fault_matches 候选（可指回真实故障）。 */
export interface AdvisorTurnResp {
  message: string;
  reply: string;
  intent: "match_fault" | "compose_scenario" | "clarify" | "out_of_domain" | "custom_proposal";
  fault_matches: {
    key: string;
    name: string;
    action: string;
    level: string;
    confidence: number;
    matched_on: string;
  }[];
  needs_clarification: boolean;
  llm_generated: boolean; // true = 回复文案由真 LLM 润色；false = 离线规则模板（诚实标注）
  suggested_steps?: CustomStep[]; // compose_scenario：可被 /api/run/custom 消费的草稿
  rag_evidence?: { doc_id: string; kind: string; score: number; text: string }[]; // clarify/custom 证据链
  followup_question?: string; // clarify/custom：引导用户下一步补充
  matched_fault?: string; // match_fault：命中的真实故障键
  scenario_suggestions?: { file: string; name: string; steps: number }[]; // match_fault：覆盖该故障的现成场景
}
