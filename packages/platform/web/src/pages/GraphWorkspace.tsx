import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type KbNode, type KbSearchHit, type KbSubgraph } from "../api";
import { Callout, EmptyState, Explain, KV, Panel, SkeletonRows, Tabs, Tag } from "../components/ui";
import { KIND_META, plainExplain } from "../lib/explanations";
import {
  CAM_DEFAULT as CAM3D_DEFAULT,
  CAM_MAX as CAM3D_MAX,
  CAM_MIN as CAM3D_MIN,
  ROT_DEFAULT,
  easeInOutCubic,
  fibonacciSphere,
  project as project3D,
  type Rot3,
} from "../lib/graph3d";

/** 图谱节点类型 → 主题变量色（亮/暗两套由 CSS 变量给出，画布/图例共用） */
const KIND_VAR: Record<string, string> = {
  message: "var(--kind-message)",
  signal: "var(--kind-signal)",
  device: "var(--kind-device)",
  fault: "var(--kind-fault)",
  scenario: "var(--kind-scenario)",
  requirement: "var(--kind-requirement)",
  function: "var(--kind-function)",
  run: "var(--kind-run)",
  // P6 领域知识节点
  mode: "var(--kind-mode)",
  state: "var(--kind-state)",
  interlock: "var(--kind-interlock)",
  threshold: "var(--kind-threshold)",
  mechanism: "var(--kind-mechanism)",
  standard: "var(--kind-standard)",
  hazard: "var(--kind-hazard)",
  concept: "var(--kind-concept)",
  system: "var(--kind-system)",
};
const kindHex = (kind: string): string => KIND_VAR[kind] ?? "var(--kind-run)";

/** 中文类型名/说明：共享词典（lib/explanations.ts 的 KIND_META）暂未收录的类型，在本页兜底。
 *  词典补齐后这里自动让位，不需要改本页。 */
const KIND_META_FALLBACK: Record<string, { label: string; what: string }> = {
  symptom: {
    label: "异常现象",
    what: "一个能被观察到的异常现象（如仪表盘闪烁），它指向若干可疑故障。",
  },
};
const kindLabel = (kind: string): string =>
  KIND_META[kind]?.label ?? KIND_META_FALLBACK[kind]?.label ?? kind;
const kindWhat = (kind: string): string => KIND_META[kind]?.what ?? KIND_META_FALLBACK[kind]?.what ?? "";

/** 首屏示例问句（点一下即检索）。
 *
 * 纪律：**界面上广告过的句子必须真的能检索到命中**（见 docs/decisions.md ADR-022）。
 * 前两句为本轮新增，已用真实检索接口逐句实测（离线、无 key 也能出结果）；
 * 后两句沿用原输入框占位符里的那两句——Python 侧 test_free_goal_situation.py 的
 * 「ADVERTISED_GOAL_EXAMPLES」清单与它们同步，改这里要一并核对，否则又会出现
 * 「界面给了例子、用户照着输却查不到」的老问题。
 */
const SEARCH_EXAMPLES = [
  "车门故障不能发车",
  "仪表盘闪烁但无故障码",
  "车门故障了还能发车吗",
  "紧急制动失败会怎样",
];

/** 检索三通道的中文名（与 /api/kb/search 响应里的 channels 字段一一对应） */
const CHANNEL_ZH: Record<string, string> = {
  vector: "向量语义",
  lexical: "词法精确",
  graph: "图谱扩展",
};

/** 可直接浏览的对象（骨架视图之外的一键入口） */
const OBJECT_SHORTCUTS = ["message:DoorControl", "fault:overspeed", "function:F-EBM", "requirement:SR-01"];

/** 命中里后端**确实返回**、但 api.ts 未声明的字段。
 *  这里只是本页的展示视图类型，不改接口：字段名与后端保持一致（channels / source_ref /
 *  anchor_stats），缺失时按「未提供」渲染，不猜测。 */
type HitEvidence = KbSearchHit & {
  channels?: string[];
  source_ref?: string;
  anchor_stats?: Record<string, number>;
};

const asEvidence = (h: KbSearchHit): HitEvidence => h as HitEvidence;

/** 可点的 chip（用真 button：键盘可达、有可见焦点环） */
function PickChip({
  label,
  title,
  active = false,
  onPick,
}: {
  label: string;
  title: string;
  active?: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onPick}
      className={`tag cursor-pointer transition-colors ${
        active
          ? "text-info border-info/45 bg-info/10"
          : "text-ink-dim border-line bg-surface-2 hover:text-ink hover:border-ink-faint/40"
      }`}
    >
      {label}
    </button>
  );
}

/** 滚轮缩放：React 的 onWheel 挂在根节点上且是**被动监听**（preventDefault 无效），
 *  结果是"在画布上滚轮 = 页面跟着滚 + 图缩放"——两件事一起发生。
 *  这里挂原生非被动监听，让「滚轮在画布上 = 只缩放」成立。 */
function useWheelZoom(
  ref: React.RefObject<SVGSVGElement | null>,
  onWheel: (e: WheelEvent) => void,
) {
  const cb = useRef(onWheel);
  cb.current = onWheel;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handler = (e: WheelEvent) => cb.current(e);
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [ref]);
}

/** 力导向 SVG（保持既有算法，视觉改用 token） */

export function GraphWorkspace() {
  const [params] = useSearchParams();
  const focusParam = params.get("focus");

  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KbSearchHit[] | null>(null);
  const [route, setRoute] = useState<{ domains: string[]; zh: string[]; bounded: boolean; mixed: boolean } | null>(null);
  const [searching, setSearching] = useState(false);
  const [sub, setSub] = useState<KbSubgraph | null>(null);
  const [depth, setDepth] = useState(2);
  const [seedLabel, setSeedLabel] = useState("");
  const [showValue, setShowValue] = useState(false);
  // 本轮新增的两个「本地界面状态」：选中哪条命中（右侧证据面板跟随）、是否已经检索过
  // （决定首屏还显不显示「从哪开始」的引导）。都不影响任何接口调用。
  const [selHit, setSelHit] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [selNode, setSelNode] = useState<{ id: string; kind: string; label: string; props?: Record<string, unknown>; neighbors?: KbNode[] } | null>(null);
  const [err, setErr] = useState("");
  const [activeKind, setActiveKind] = useState<string>("all");
  const [kbStats, setKbStats] = useState<{ graph: { nodes: number; edges: number; by_kind: Record<string, number> }; vector: { docs: number } } | null>(null);
  const didFocus = useRef(false);
  // 选中节点 id（图内高亮环，与详情侧栏联动）；返回栈（上一视图，类“会话可回退”）
  const [selId, setSelId] = useState<string | null>(null);
  const [histLen, setHistLen] = useState(0);
  const histRef = useRef<Array<{ kind: "overview" } | { kind: "seed"; seed: string; depth: number }>>([]);
  const pushHistory = useCallback(() => {
    if (!sub) return;
    const desc = sub.seed === "overview"
      ? { kind: "overview" as const }
      : { kind: "seed" as const, seed: sub.seed, depth };
    const st = histRef.current;
    const last = st[st.length - 1];
    // 相邻重复（连续同视图）不入栈，防返回原地踏步
    if (last && JSON.stringify(last) === JSON.stringify(desc)) return;
    st.push(desc);
    if (st.length > 30) st.shift();
    setHistLen(st.length);
  }, [sub, depth]);

  useEffect(() => {
    api.kbStats().then(setKbStats).catch(() => undefined);
  }, []);

  // 默认视图：未搜索 / 未选种子时先展示「基础关联图谱」骨架（13 系统 + 功能 + 代表故障），
  // 不必等用户搜索后才出现内容。
  const isOverview = sub?.seed === "overview";
  const loadOverview = useCallback(async () => {
    setErr("");
    try {
      const r = await api.kbOverview();
      pushHistory(); // 记录当前视图，供“⬅ 返回”
      setSub(r);
      setHits(null);
      setSeedLabel("基础关联图谱（13 系统域骨架）");
      setSelNode(null);
      setSelId(null);
      setSelHit(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [pushHistory]);

  useEffect(() => {
    if (!focusParam && !didFocus.current) {
      didFocus.current = true;
      void loadOverview();
    }
  }, [focusParam, loadOverview]);

  const focus = useCallback(async (seedId: string, d = depth) => {
    setErr("");
    try {
      const r = await api.kbSubgraph(seedId, d);
      pushHistory(); // 成功后记录上一视图（⬅ 返回用）
      setSub(r);
      setHits(null);
      setSeedLabel(r.nodes.find((n) => n.id === seedId)?.label ?? seedId);
      setSelNode(null);
      setSelId(null);
      setSelHit(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [depth, pushHistory]);

  // 返回上一视图（overview 或上一个种子子图），不重复入栈
  const goBack = async () => {
    const prev = histRef.current.pop();
    setHistLen(histRef.current.length);
    if (!prev) return;
    setErr("");
    try {
      if (prev.kind === "overview") {
        const r = await api.kbOverview();
        setSub(r);
        setSeedLabel("基础关联图谱（13 系统域骨架）");
      } else {
        const r = await api.kbSubgraph(prev.seed, prev.depth);
        setSub(r);
        setSeedLabel(r.nodes.find((n) => n.id === prev.seed)?.label ?? prev.seed);
      }
      setHits(null);
      setSelNode(null);
      setSelId(null);
      setSelHit(null);
    } catch (e) {
      setErr(String(e));
    }
  };

  // 深度切换：以当前 seed 重拉（保持中心不变，扩/缩一圈）；骨架视图无 seed，不适用
  const changeDepth = async (d: number) => {
    if (d === depth || !sub || sub.seed === "overview") return;
    setDepth(d);
    await focus(sub.seed, d);
  };

  // 支持 ?focus= 直达（从总览/资产页跳入）：已带 kind 前缀原样使用，否则补 function:
  useEffect(() => {
    if (focusParam && !didFocus.current) {
      didFocus.current = true;
      const seedId = focusParam.includes(":") ? focusParam : `function:${focusParam}`;
      void focus(seedId);
    }
  }, [focusParam, focus]);

  const search = async (q?: string) => {
    const text = (q ?? query).trim();
    if (!text) return;
    setSearching(true);
    setErr("");
    setSub(null);
    setSearched(true);
    try {
      const r = await api.kbSearch(text, 10);
      setHits(r.hits);
      setRoute({
        domains: r.routed_domains ?? [],
        zh: r.routed_zh ?? [],
        bounded: r.bounded ?? false,
        mixed: r.mixed_fallback ?? false,
      });
      setActiveKind("all");
      setSelNode(null);
      setSelId(null);
      setSelHit(r.hits[0]?.doc_id ?? null); // 默认选中第一条：证据面板一进来就有内容
    } catch (e) {
      setErr(String(e));
    } finally {
      setSearching(false);
    }
  };

  /** 示例问句：点一下即用它检索（输入框同步显示，用户看得见自己"问了什么"） */
  const runExample = (q: string) => {
    setQuery(q);
    void search(q);
  };

  /** 直接浏览某个对象（以它为中心展开） */
  const openObject = (id: string) => {
    setSearched(true);
    void focus(id);
  };

  const openNode = async (id: string) => {
    try {
      const n = await api.kbNode(id);
      setSelNode(n);
      setSelId(id); // 图内高亮环跟随
    } catch (e) {
      setErr(String(e));
    }
  };

  // 结果按类型分类 + 每个 kind 计数
  const grouped = useMemo(() => {
    if (!hits) return null;
    const by: Record<string, KbSearchHit[]> = {};
    for (const h of hits) {
      (by[h.kind] ??= []).push(h);
    }
    return by;
  }, [hits]);

  // 命中在整体结果里的名次（排序分是 RRF 融合分，绝对值不可读，名次才是人能用的信息）
  const rankOf = useMemo(() => {
    const m = new Map<string, number>();
    hits?.forEach((h, i) => m.set(h.doc_id, i + 1));
    return m;
  }, [hits]);

  // 分类：按"该类型里最好的名次"排组，结果从上往下读就是相关度顺序。
  // 类型以**本次真实命中里出现的**为准，不再用固定白名单——原来只列了 8 类，
  // 命中的 symptom / system 等会整组消失（用户看不到它们，却占着排序名次）。
  const kindTabs = grouped
    ? Object.keys(grouped)
        .map((k) => ({
          kind: k,
          n: grouped[k]!.length,
          best: Math.min(...grouped[k]!.map((h) => rankOf.get(h.doc_id) ?? 999)),
        }))
        .sort((a, b) => a.best - b.best)
    : [];

  // 当前子图按 kind 计数（「更进一步拓展」的分布统计行；kind 标签/颜色与图例一致）
  const subKinds = useMemo(() => {
    if (!sub) return null;
    const by: Record<string, number> = {};
    for (const n of sub.nodes) by[n.kind] = (by[n.kind] ?? 0) + 1;
    return by;
  }, [sub]);
  // 图例：展示当前子图实际含有的类型（无子图时退化为 KB 概览 by_kind 全量）
  const legendKinds = useMemo(() => {
    const src = sub ? subKinds : kbStats?.graph.by_kind;
    if (!src) return [];
    const present = Object.keys(src);
    return ["system", "fault", "message", "signal", "function", "requirement", "scenario", "device", "run", "mode", "state", "interlock", "threshold", "mechanism", "standard", "hazard", "concept"].filter(
      (k) => present.includes(k)
    );
  }, [sub, subKinds, kbStats]);

  // 右侧证据面板要展示的那一条（未选中任何命中时为 null）
  const selHitView = useMemo(() => {
    if (!hits || !selHit) return null;
    const idx = hits.findIndex((h) => h.doc_id === selHit);
    if (idx < 0) return null;
    return { hit: asEvidence(hits[idx]), rank: idx + 1, total: hits.length };
  }, [hits, selHit]);

  // 选中节点 → 一键动作的目标场景（scenario 本体，或关联里的第一个复现场景）
  const selScenFile = selNode
    ? selNode.kind === "scenario"
      ? selNode.id.split(":")[1]
      : (selNode.neighbors?.find((nb) => nb.kind === "scenario")?.id.split(":")[1] ?? null)
    : null;

  // 有命中或有子图时才开右栏；否则主区占满宽度（窄屏下两栏自动上下堆叠）
  const showAside = (hits?.length ?? 0) > 0 || Boolean(sub);

  /** 分域英文键 → 中文名：用本次检索的路由结果（routed_domains 与 routed_zh 一一对应），
   *  对不上时如实显示原始键，不硬编码一张可能过期的映射表。 */
  const domainZh = new Map((route?.domains ?? []).map((d, i) => [d, route?.zh[i] ?? d]));
  const domainLabel = (d?: string) => (!d ? "" : `域 · ${domainZh.get(d) ?? d}`);

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4">
      {/* ============ 第一屏的主角：检索 ============
          用户来这一页只有一个动作——问一句话。所以输入框占满宽度放在最上面，
          示例问句就在手边（点一下即检索），环境事实（图谱规模）贴在同一屏里。 */}
      <Panel bodyClass="p-4">
        <div className="flex flex-col gap-2.5">
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1 min-w-0">
              <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint text-[15px]">🔍</span>
              <input
                className="input h-11 pl-10 text-[14px]"
                placeholder="用大白话问，如：车门故障了还能发车吗 / 紧急制动失败会怎样（回车即检索）"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void search()}
                aria-label="知识检索"
              />
            </div>
            <button
              className="btn h-11 px-6 justify-center whitespace-nowrap"
              onClick={() => void search()}
              disabled={searching || !query.trim()}
            >
              {searching ? "检索中…" : "检索"}
            </button>
          </div>

          {/* 可点的示例问句：点一下直接检索（这些句子都实测能出命中，见上方 SEARCH_EXAMPLES 注释） */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-ink-faint shrink-0">试试：</span>
            {SEARCH_EXAMPLES.map((ex) => (
              <PickChip
                key={ex}
                label={ex}
                active={query.trim() === ex}
                title={`用这句话检索：${ex}`}
                onPick={() => runExample(ex)}
              />
            ))}
          </div>

          {/* 环境事实（机器自证）：图谱规模 + 向量索引 */}
          {kbStats && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-faint">
              <span className="num">
                图谱 <b className="text-ink-dim font-medium">{kbStats.graph.nodes}</b> 节点 /{" "}
                <b className="text-ink-dim font-medium">{kbStats.graph.edges}</b> 边
              </span>
              <span className="num">
                向量索引 <b className="text-ink-dim font-medium">{kbStats.vector.docs}</b> 条文档
              </span>
              <span>实体类型 {Object.keys(kbStats.graph.by_kind).length} 种</span>
            </div>
          )}

          {/* 原理收进可展开层：明面只留一句人话 */}
          <Callout
            tone="dim"
            icon="⌕"
            title="三通道融合检索："
            details={
              <div className="space-y-1">
                <div>· 向量语义：说法不同、意思相近也能召回（如「门没关就发车」）。</div>
                <div>· 词法精确：字面必须命中，防止语义跑偏。</div>
                <div>· 图谱扩展：沿真实关系边补召回另外两路都漏掉的相邻资产。</div>
                <div>· 每条命中都会摊开：资产 ID、排序分（RRF 融合分，仅用于排序）、命中通道、资产出处与关联实体。</div>
              </div>
            }
          >
            向量语义 + 词法精确 + 图谱扩展三路召回，每条命中都带得出证据。
          </Callout>
        </div>
      </Panel>

      {err && <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ {err}</div>}

      {/* 检索中 */}
      {searching && (
        <Panel title="正在检索领域知识…" bodyClass="py-1">
          <SkeletonRows rows={3} cols={3} />
        </Panel>
      )}

      {/* 宽屏两栏工作台：左边是「图 / 命中列表」主舞台，右边是「证据 / 详情」；
          窄屏（< xl）自动上下堆叠，画布与面板都不会被压变形。 */}
      <div className={`grid gap-4 ${showAside ? "xl:grid-cols-[minmax(0,1fr)_344px] xl:items-start" : ""}`}>
        <div className="space-y-4 min-w-0">
          {/* 起步引导（还没检索过时显示）：紧凑 + 预告结构 + 一键对象，不占半屏 */}
          {!searched && (
            <Panel bodyClass="p-3">
              <EmptyState
                compact
                icon="◈"
                title="问一句话就行，不用背术语"
                desc="上面已放好示例问句，点一下即检索。检索结果会长这样："
                steps={[
                  { icon: "1", title: "命中卡", desc: "哪种资产 · 资产 ID · 排序名次 · 资产出处" },
                  { icon: "2", title: "点一条看证据", desc: "右侧摊开排序分、命中通道、出处与关联实体" },
                  { icon: "3", title: "展开关系图谱", desc: "以它为中心，看谁影响谁，可单击/双击漫游" },
                ]}
                action={
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-ink-faint">或者直接看一个对象：</span>
                    {OBJECT_SHORTCUTS.map((id) => (
                      <PickChip
                        key={id}
                        label={id}
                        title={`以 ${id} 为中心展开关系图谱（${KIND_META[id.split(":")[0]]?.what ?? ""}）`}
                        onPick={() => openObject(id)}
                      />
                    ))}
                  </div>
                }
              />
            </Panel>
          )}

          {/* 结果（分类展示） */}
          {!searching && hits && (
            <>
              {hits.length === 0 ? (
                <Panel bodyClass="p-3">
                  <EmptyState
                    compact
                    icon="?"
                    title="没找到直接匹配"
                    desc="试试更口语化的问法，例如「心跳丢失」「门没关就发车」「超速」；也可以点上面的示例问句。"
                  />
                </Panel>
              ) : (
                <>
                  {/* 检索走向（有界分层：先图谱路由到域，再域内 topk） */}
                  {route && (route.domains.length > 0 || route.zh.length > 0) && (
                    <div className="flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-ink-faint">
                      <span>路由到分域：</span>
                      {route.zh.map((z) => (
                        <Tag key={z} tone="info">
                          {z}
                        </Tag>
                      ))}
                      {route.bounded && <span>· 域内有界检索（不整库迷失）</span>}
                      {route.mixed && <span>· 域内不足已全局补召回</span>}
                    </div>
                  )}

                  {/* 分类切换：真 Tab（带计数），不再用一排并列按钮让用户猜哪个是当前态 */}
                  <Tabs
                    items={[
                      { value: "all", label: "全部", hint: "显示全部命中", count: hits.length },
                      ...kindTabs.map(({ kind, n }) => ({
                        value: kind,
                        label: kindLabel(kind),
                        hint: kindWhat(kind),
                        count: n,
                      })),
                    ]}
                    value={activeKind}
                    onChange={setActiveKind}
                  />

                  {/* 结果组 */}
                  <div className="space-y-4">
                    {kindTabs
                      .filter(({ kind }) => activeKind === "all" || activeKind === kind)
                      .map(({ kind, n }) => (
                        <div key={kind}>
                          <div className="px-1 mb-1.5 flex items-baseline gap-2 min-w-0">
                            <span className="text-[12px] font-semibold text-ink whitespace-nowrap">
                              {kindLabel(kind)}{" "}
                              <span className="text-ink-faint font-normal tabular-nums">({n})</span>
                            </span>
                            <span className="text-[11px] text-ink-faint truncate min-w-0">
                              {kindWhat(kind)}
                            </span>
                          </div>
                          <div className="space-y-1.5">
                            {grouped![kind].map((h) => {
                              const ev = asEvidence(h);
                              const on = selHit === h.doc_id;
                              return (
                                <div
                                  key={h.doc_id}
                                  className={`panel p-3 transition-colors ${
                                    on ? "border-info/50 bg-info/[0.04]" : "panel-hover"
                                  }`}
                                >
                                  {/* 卡体本身是按钮：点它=选中（右侧看证据），不会一不留神把结果列表换掉 */}
                                  <button
                                    type="button"
                                    aria-pressed={on}
                                    onClick={() => setSelHit(h.doc_id)}
                                    className="block w-full text-left cursor-pointer"
                                    title="点一下：在右侧摊开它的证据"
                                  >
                                    <div className="flex items-center gap-2 min-w-0">
                                      <Tag tone={KIND_META[kind]?.color}>
                                        {kindLabel(kind)}
                                      </Tag>
                                      <code className="kbd-mono truncate min-w-0 flex-1" title={h.doc_id}>
                                        {h.doc_id}
                                      </code>
                                      <span className="text-[10.5px] text-ink-faint tabular-nums shrink-0">
                                        #{rankOf.get(h.doc_id) ?? "-"}
                                      </span>
                                      {on && <Tag tone="info">已选中</Tag>}
                                    </div>
                                    <p className="mt-1.5 text-[13px] text-ink leading-5 line-clamp-2">
                                      {h.text.slice(0, 160)}
                                      {h.text.length > 160 ? "…" : ""}
                                    </p>
                                  </button>
                                  {on && <Explain text={plainExplain(h.doc_id, h.text)} />}
                                  <div className="mt-2 flex items-center gap-2 min-w-0">
                                    <span
                                      className="text-[10.5px] text-ink-faint truncate min-w-0"
                                      title={`资产出处：${ev.source_ref ?? h.doc_id}`}
                                    >
                                      出处 {ev.source_ref ?? h.doc_id}
                                    </span>
                                    {ev.channels && ev.channels.length > 0 && (
                                      <span className="hidden sm:inline text-[10.5px] text-ink-faint truncate">
                                        通道 {ev.channels.map((c) => CHANNEL_ZH[c] ?? c).join(" / ")}
                                      </span>
                                    )}
                                    <button
                                      type="button"
                                      className="btn-ghost btn-sm ml-auto shrink-0 whitespace-nowrap"
                                      onClick={() => void focus(h.doc_id)}
                                      title="以它为中心展开关系图谱"
                                    >
                                      关系图谱 →
                                    </button>
                                  </div>
                                  {on && h.graph_neighbors.length > 0 && (
                                    <div className="mt-2 flex flex-wrap gap-1">
                                      {h.graph_neighbors.slice(0, 6).map((nb) => (
                                        <Tag
                                          key={nb.id}
                                          tone="dim"
                                          title={`${nb.via} → ${nb.label}（点击以它为中心展开）`}
                                          onClick={() => void focus(nb.id)}
                                        >
                                          {kindLabel(nb.kind)} · {nb.label}
                                        </Tag>
                                      ))}
                                      {h.graph_neighbors.length > 6 && (
                                        <span className="text-[11px] text-ink-faint self-center">
                                          +{h.graph_neighbors.length - 6}
                                        </span>
                                      )}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                  </div>
                </>
              )}
            </>
          )}

          {/* 图谱 */}
          {sub && (
            <>
              <Panel
                title={
                  <>
                    关系图谱 · <span className="text-ink">{seedLabel}</span>
                  </>
                }
                right={
                  <div className="flex items-center gap-2">
                    <Tag tone="dim">{sub.node_count} 节点 / {sub.edges.length} 边</Tag>
                    {histLen > 0 && (
                      <button className="btn-ghost btn-sm" onClick={() => void goBack()} title="返回上一视图（浏览历史可回退）">
                        ⬅ 返回
                      </button>
                    )}
                    {!isOverview && (
                      <button className="btn-ghost btn-sm" onClick={() => void loadOverview()} title="回到 13 系统域基础关联图谱">
                        ↺ 骨架
                      </button>
                    )}
                  </div>
                }
                bodyClass="p-0"
              >
                {/* 视图控件放在画布之外的工具条上（原先是浮在画布左上角，会压住节点标签） */}
                <GraphCanvas
                  sub={sub}
                  selId={selId}
                  onNodeClick={openNode}
                  onJump={focus}
                  depth={depth}
                  onDepthChange={changeDepth}
                  isOverview={isOverview}
                />
                {/* 读图说明（原来是一段贴在图下的灰字）：收进可展开层，想看再看 */}
                <details className="border-t border-line-soft px-3 py-2">
                  <summary className="text-[11px] text-ink-faint cursor-pointer select-none hover:text-ink-dim">
                    怎么读这张图？
                  </summary>
                  <div className="mt-1 text-[11.5px] text-ink-dim leading-5">
                    中心是「{seedLabel}」，连线上的词是关系（如「发送方→」「触发」）；色点代表实体类型，数字是该类型在本子图里的个数。
                    单击节点=选中并看详情（图上出现高亮环），双击节点=以它为中心跳转；「深度」扩/缩关联范围，⬅ 返回回上一视图。
                  </div>
                </details>
              </Panel>
            </>
          )}
        </div>

        {showAside && (
          <aside className="space-y-4 min-w-0">
            {/* 命中证据：doc_id / 排序分 / 命中通道 / 资产出处 一律排成可核对的键值行 */}
            {hits && hits.length > 0 && (
              <Panel
                title="命中证据"
                sub={selHitView ? `${selHitView.rank} / ${selHitView.total}` : "机器可核对"}
                right={
                  selHitView ? (
                    <Tag tone={KIND_META[selHitView.hit.kind]?.color}>
                      {kindLabel(selHitView.hit.kind)}
                    </Tag>
                  ) : undefined
                }
                bodyClass="p-3"
              >
                {!selHitView ? (
                  <EmptyState
                    compact
                    icon="☞"
                    title="点左边任一条命中"
                    desc="这里会摊开它的资产 ID、排序分、命中通道、资产出处与关联实体。"
                  />
                ) : (
                  <div className="space-y-3">
                    <div className="min-w-0">
                      <code className="kbd-mono block truncate" title={selHitView.hit.doc_id}>
                        {selHitView.hit.doc_id}
                      </code>
                      <div className="mt-1">
                        <Explain text={plainExplain(selHitView.hit.doc_id, selHitView.hit.text)} />
                      </div>
                    </div>
                    <div className="space-y-1 border-t border-line-soft pt-2">
                      <KV k="排序分" v={selHitView.hit.score.toFixed(4)} mono />
                      <KV
                        k="命中通道"
                        v={
                          (selHitView.hit.channels ?? []).map((c) => CHANNEL_ZH[c] ?? c).join(" / ") || "未标注"
                        }
                      />
                      <KV k="资产出处" v={selHitView.hit.source_ref ?? selHitView.hit.doc_id} mono />
                      <KV
                        k="所属分域"
                        v={selHitView.hit.domain ? domainLabel(selHitView.hit.domain) : "未分区（全局检索）"}
                      />
                      <KV k="直接关系" v={`${selHitView.hit.graph_neighbors.length} 条`} />
                      {selHitView.hit.anchor_stats && Object.keys(selHitView.hit.anchor_stats).length > 0 && (
                        <KV
                          k="邻接构成"
                          v={Object.entries(selHitView.hit.anchor_stats)
                            .map(([k, n]) => `${kindLabel(k)} ×${n}`)
                            .join(" · ")}
                        />
                      )}
                    </div>
                    <div className="text-[10.5px] text-ink-faint leading-4">
                      排序分是三通道排名融合（RRF）的结果，只用于排序，不是匹配概率。
                    </div>
                    {selHitView.hit.graph_neighbors.length > 0 && (
                      <div>
                        <div className="text-[11px] text-ink-faint mb-1.5">
                          关联实体（{selHitView.hit.graph_neighbors.length}）· 点击以其为中心展开
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {selHitView.hit.graph_neighbors.map((nb) => (
                            <Tag
                              key={nb.id}
                              tone={KIND_META[nb.kind]?.color}
                              title={`${nb.via} → ${nb.label}`}
                              onClick={() => void focus(nb.id)}
                            >
                              {nb.label}
                            </Tag>
                          ))}
                        </div>
                      </div>
                    )}
                    <button
                      className="btn btn-sm w-full justify-center"
                      onClick={() => void focus(selHitView.hit.doc_id)}
                      title="以这条命中为中心拉出关系子图"
                    >
                      ⤢ 以它为中心展开图谱
                    </button>
                  </div>
                )}
              </Panel>
            )}

            {/* 选中节点详情（图上单击节点后出现；原先是页面底部一张通栏面板） */}
            {sub && (
              <Panel
                title="选中节点"
                right={
                  selNode ? (
                    <button className="btn-ghost btn-sm" onClick={() => setSelNode(null)}>
                      关闭
                    </button>
                  ) : undefined
                }
                bodyClass="p-3"
              >
                {!selNode ? (
                  <EmptyState
                    compact
                    icon="◉"
                    title="单击图上节点看详情"
                    desc="双击节点=以它为中心重新展开；色点颜色代表实体类型（见下方图例）。"
                  />
                ) : (
                  <div className="space-y-2.5">
                    <div className="flex items-start gap-2 min-w-0">
                      <Tag tone={KIND_META[selNode.kind]?.color}>
                        {kindLabel(selNode.kind)}
                      </Tag>
                      <span className="text-[13px] font-medium text-ink min-w-0 break-words">{selNode.label}</span>
                    </div>
                    <code className="kbd-mono block truncate" title={selNode.id}>
                      {selNode.id}
                    </code>
                    {KIND_META[selNode.kind] && <Explain text={KIND_META[selNode.kind].what} />}
                    {/* 图谱 → 动作（不绕回其它页） */}
                    <div className="flex flex-wrap gap-1.5">
                      <button
                        className="btn btn-sm"
                        onClick={() => {
                          setSelNode(null);
                          void focus(selNode.id);
                        }}
                        title="以该节点为中心重新拉子图（与图上双击同效）"
                      >
                        ⤢ 以它为中心扩展
                      </button>
                      {selScenFile && (
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            window.location.href = `/faultlab?scenario=${encodeURIComponent(selScenFile)}&from=kb`;
                          }}
                          title="跳到 FaultLab 播放该场景的故障动画"
                        >
                          ▶ 去 FaultLab 演示
                        </button>
                      )}
                    </div>
                    {selNode.props && Object.keys(selNode.props).length > 0 && (
                      <div className="space-y-1 border-t border-line-soft pt-2">
                        {Object.entries(selNode.props).map(([k, v]) => (
                          <KV key={k} k={k} v={String(v)} />
                        ))}
                      </div>
                    )}
                    {selNode.neighbors && selNode.neighbors.length > 0 && (
                      <div>
                        <div className="text-[11px] text-ink-faint mb-1.5">
                          直接关联（{selNode.neighbors.length}）· 点击跳转
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {selNode.neighbors.map((nb) => (
                            <Tag
                              key={nb.id}
                              tone={KIND_META[nb.kind]?.color}
                              onClick={() => void focus(nb.id)}
                              title="点击跳转到该节点"
                            >
                              {nb.label}
                            </Tag>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </Panel>
            )}

            {/* 图例与构成：当前子图实际含有的类型（色点与画布一致） */}
            {sub && (
              <Panel title="图例与构成" sub={`${sub.node_count} 节点 / ${sub.edges.length} 边`} dense>
                <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                  {legendKinds.map((k) => (
                    <span key={k} className="inline-flex items-center gap-1.5 text-[11px] text-ink-dim">
                      <span className="h-2 w-2 rounded-full shrink-0" style={{ background: kindHex(k) }} />
                      {kindLabel(k)}
                      <span className="text-ink-faint num">×{subKinds?.[k] ?? 0}</span>
                    </span>
                  ))}
                </div>
              </Panel>
            )}
          </aside>
        )}
      </div>

      {/* 收尾：这张图谱给谁用（教育性内容，移到页面最后，默认收起，不挡检索） */}
      <div className="panel overflow-hidden">
        <button
          className="w-full flex items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-surface-2/40"
          onClick={() => setShowValue((v) => !v)}
          aria-expanded={showValue}
        >
          <span className={`inline-block transition-transform ${showValue ? "rotate-90" : ""} text-ink-faint text-[11px]`}>▶</span>
          <span className="text-[13px] font-semibold text-ink">这张知识图谱，是给谁用的？</span>
          <span className="ml-auto text-[11px] text-ink-faint">{showValue ? "收起" : "人用 / AI 用，两种读法"}</span>
        </button>
        {showValue && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 px-4 pb-4 pt-1">
            {/* 给人 */}
            <div className="bg-surface-2/40 rounded-[var(--radius-md)] p-3.5 border border-line-soft">
              <div className="flex items-center gap-2 mb-2">
                <Tag tone="info">给人</Tag>
                <span className="text-[12px] text-ink-dim">查证 / 理解 · 用大白话问，不用懂报文</span>
              </div>
              <ul className="space-y-1.5 text-[12px] leading-5 text-ink-dim">
                <li>· <span className="text-ink">大白话提问</span>：如「车门故障了还能发车吗」，返回带证据链的答案，不是一堆报文字段。</li>
                <li>· <span className="text-ink">点实体漫游</span>：从一条报文点进它关联的故障、功能、安全需求，摸清「谁影响谁」。</li>
                <li>· <span className="text-ink">每个命中都解释</span>：它是哪种资产、意味着什么、和谁相连，零术语也能读。</li>
              </ul>
            </div>
            {/* 给 AI */}
            <div className="bg-surface-2/40 rounded-[var(--radius-md)] p-3.5 border border-line-soft">
              <div className="flex items-center gap-2 mb-2">
                <Tag tone="vio">给 AI</Tag>
                <span className="text-[12px] text-ink-dim">检索 / 追溯 / 沉淀 · 是 Agent 的「领域记忆」</span>
              </div>
              <ul className="space-y-1.5 text-[12px] leading-5 text-ink-dim">
                <li>· <span className="text-ink">Agent 规划时检索证据</span>：混合检索（向量/词法 + 图谱邻接）给 Agent 决策喂真实知识，评审按需求/联锁/阈值追溯。</li>
                <li>· <span className="text-ink">评审核对需求追溯</span>：每次任务达成与否，都要在图谱里找到对应的安全需求与联锁规则作依据。</li>
                <li>· <span className="text-ink">run 记录沉淀为组织记忆</span>：真实执行结果写成 run 节点连回场景/故障，越用越厚的知识底座。</li>
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** ================= 图谱画布：2D 缩放平移 + 3D 轨道俯瞰 =================
 *  - 2D：滚轮缩放（以光标为中心）、拖拽平移、双击/按钮一键适配、可点节点
 *  - 3D：力导向布局投影到球面，可拖拽旋转 + 自动缓转，体感更直观
 *  - 取色走主题变量 / 语义色（KIND_HEX），亮暗主题下都可读
 */

const GRAPH_W = 960;
const GRAPH_H = 600;

type ViewMode = "2d" | "3d";
type LabelMode = "auto" | "all" | "off";
interface ViewState {
  scale: number;
  tx: number;
  ty: number;
}

type Layout = Map<string, { x: number; y: number }>;

/** 力导向布局（保留原算法，抽成纯函数，2D 与适配共用） */
function computeLayout(sub: KbSubgraph): Layout {
  const W = GRAPH_W;
  const H = GRAPH_H;
  const K = 150;
  const nodes = sub.nodes;
  const links = sub.edges;
  const pos = new Map<string, { x: number; y: number }>();
  const cx = W / 2;
  const cy = H / 2;
  nodes.forEach((n, i) => {
    if (n.id === sub.seed) {
      pos.set(n.id, { x: cx, y: cy });
      return;
    }
    const ang = (i / Math.max(nodes.length - 1, 1)) * Math.PI * 2;
    const r = 120 + (i % 3) * 55;
    pos.set(n.id, { x: cx + Math.cos(ang) * r, y: cy + Math.sin(ang) * r });
  });
  const rep = 9000;
  const attr = 0.06;
  const fmap = new Map<string, { fx: number; fy: number }>();
  for (let iter = 0; iter < 200; iter++) {
    nodes.forEach((n) => fmap.set(n.id, { fx: 0, fy: 0 }));
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos.get(nodes[i].id)!;
        const b = pos.get(nodes[j].id)!;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) {
          dx = (Math.random() - 0.5) * 2;
          dy = (Math.random() - 0.5) * 2;
          d2 = dx * dx + dy * dy;
        }
        const d = Math.sqrt(d2);
        const f = rep / d2;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        fmap.get(nodes[i].id)!.fx += fx;
        fmap.get(nodes[i].id)!.fy += fy;
        fmap.get(nodes[j].id)!.fx -= fx;
        fmap.get(nodes[j].id)!.fy -= fy;
      }
    }
    links.forEach((l) => {
      const a = pos.get(l.src);
      const b = pos.get(l.dst);
      if (!a || !b) return;
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (dist - K) * attr;
      const fx = (dx / dist) * f;
      const fy = (dy / dist) * f;
      fmap.get(l.src)!.fx += fx;
      fmap.get(l.src)!.fy += fy;
      fmap.get(l.dst)!.fx -= fx;
      fmap.get(l.dst)!.fy -= fy;
    });
    nodes.forEach((n) => {
      const p = pos.get(n.id)!;
      p.x += (W / 2 - p.x) * 0.01;
      p.y += (H / 2 - p.y) * 0.01;
      p.x += fmap.get(n.id)!.fx;
      p.y += fmap.get(n.id)!.fy;
      p.x = Math.max(40, Math.min(W - 40, p.x));
      p.y = Math.max(35, Math.min(H - 35, p.y));
    });
  }
  return pos;
}

const shortLabel = (s: string) => (s.length > 15 ? s.slice(0, 14) + "…" : s);
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** 自动模式下"常显标签"的预算：**随节点数缩放**，而不是一个死数。
 *
 * 为什么不用固定值：固定 12 在两端都不对——
 *  · 检索出来的小子图（5–20 个节点）本身就是"答案集"，只标 12 个等于让用户猜其余的；
 *  · 大图（几百节点）12 个又太稀，看不出结构在哪。
 * 规则：≤20 个节点全标；否则取 22% 并在 [10, 20] 内夹住。
 * 56 节点的默认骨架视图算出正好 12——与原先看图定下的值一致，那个视图观感不变。 */
const labelBudget = (n: number) => (n <= 20 ? n : clamp(Math.round(n * 0.22), 10, 20));

/** 只服务"标签何时显示"的索引：度数排行 + 邻接表。
 *  不参与布局/物理——力导向算法一个字没动。 */
function graphIndex(sub: KbSubgraph): { topLabels: Set<string>; neighbors: Map<string, Set<string>> } {
  const deg = new Map<string, number>();
  const neighbors = new Map<string, Set<string>>();
  for (const e of sub.edges) {
    deg.set(e.src, (deg.get(e.src) ?? 0) + 1);
    deg.set(e.dst, (deg.get(e.dst) ?? 0) + 1);
    if (!neighbors.has(e.src)) neighbors.set(e.src, new Set());
    if (!neighbors.has(e.dst)) neighbors.set(e.dst, new Set());
    neighbors.get(e.src)!.add(e.dst);
    neighbors.get(e.dst)!.add(e.src);
  }
  const topLabels = new Set(
    [...sub.nodes]
      .sort((a, b) => (deg.get(b.id) ?? 0) - (deg.get(a.id) ?? 0))
      .slice(0, labelBudget(sub.nodes.length))
      .map((n) => n.id),
  );
  return { topLabels, neighbors };
}

/** 2D 力导向 + 缩放平移画布 */
function GraphCanvas2D({
  sub,
  selId,
  onNodeClick,
  onJump,
  fitSignal,
  labelMode,
}: {
  sub: KbSubgraph;
  selId: string | null;
  onNodeClick: (id: string) => void;
  onJump?: (id: string) => void;
  fitSignal: number;
  labelMode: LabelMode;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [view, setView] = useState<ViewState>({ scale: 1, tx: 0, ty: 0 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);

  const layout = useMemo(() => computeLayout(sub), [sub]);
  const index = useMemo(() => graphIndex(sub), [sub]);

  const fit = useCallback(() => {
    const pts = [...layout.values()];
    if (!pts.length) return;
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const pad = 70;
    const scale = clamp(Math.min((GRAPH_W - pad * 2) / Math.max(maxX - minX, 1), (GRAPH_H - pad * 2) / Math.max(maxY - minY, 1)), 0.15, 2.5);
    const tx = GRAPH_W / 2 - ((minX + maxX) / 2) * scale;
    const ty = GRAPH_H / 2 - ((minY + maxY) / 2) * scale;
    setView({ scale, tx, ty });
  }, [layout]);

  // 首次挂载 + fitSignal 递增（父级“适配”按钮）都触发适配
  useEffect(() => {
    fit();
  }, [fit, fitSignal]);

  /** 屏幕坐标 → viewBox 坐标。
   *  SVG 用 preserveAspectRatio=meet 撑满容器时会留边（letterbox），
   *  直接按容器宽高做比例换算会让"以光标为中心缩放/拖拽"在窄屏上偏掉，
   *  所以这里按真实缩放系数 k 与留白偏移换算。 */
  const viewScale = (rect: DOMRect) => {
    if (!rect.width || !rect.height) return 1;
    return Math.min(rect.width / GRAPH_W, rect.height / GRAPH_H);
  };

  const toLocal = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const r = svg.getBoundingClientRect();
    const k = viewScale(r);
    const offX = (r.width - GRAPH_W * k) / 2;
    const offY = (r.height - GRAPH_H * k) / 2;
    return { x: (clientX - r.left - offX) / k, y: (clientY - r.top - offY) / k };
  };

  const zoomAt = (mx: number, my: number, factor: number) => {
    setView((v) => {
      const scale = clamp(v.scale * factor, 0.15, 4);
      const k = scale / v.scale;
      return { scale, tx: mx - (mx - v.tx) * k, ty: my - (my - v.ty) * k };
    });
  };

  // 滚轮 = 只缩放（不带着页面一起滚）
  useWheelZoom(svgRef, (e) => {
    e.preventDefault();
    const { x, y } = toLocal(e.clientX, e.clientY);
    zoomAt(x, y, e.deltaY < 0 ? 1.18 : 1 / 1.18);
  });

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false };
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const rect = svgRef.current?.getBoundingClientRect();
    const k = rect ? viewScale(rect) : 1;
    const dx = (e.clientX - d.x) / k;
    const dy = (e.clientY - d.y) / k;
    if (Math.abs(dx) + Math.abs(dy) > 1) d.moved = true;
    setView((v) => ({ ...v, tx: d.tx + dx, ty: d.ty + dy }));
  };
  const endDrag = () => {
    drag.current = null;
  };

  const onDoubleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    e.preventDefault(); // 阻止空白区双击触发浏览器文本选择
    fit();
  };
  const showLabels = view.scale >= 0.42;
  const showEdgeText = view.scale >= 0.85;

  // 标签降噪（只影响"何时显示"，不碰布局）：选中优先、其次悬停，作为"关注点"
  const focusId = selId ?? hover;
  const near = focusId ? index.neighbors.get(focusId) : undefined;
  const labelOn = (id: string): boolean => {
    if (labelMode === "off" || !showLabels) return false;
    if (labelMode === "all") return true;
    // 自动：只常显关键节点（度数 top N）+ 中心节点；其余在悬停/选中/与关注点相邻时出现
    return id === sub.seed || index.topLabels.has(id) || id === focusId || Boolean(near?.has(id));
  };
  const edgeTextOn = (src: string, dst: string): boolean => {
    if (labelMode === "off" || !showEdgeText) return false;
    if (labelMode === "all") return true;
    if (!focusId) return false; // 自动：只标出与关注点相连的那几条边
    return src === focusId || dst === focusId;
  };

  return (
    <svg
      ref={svgRef}
      width="100%"
      height="100%"
      viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`}
      preserveAspectRatio="xMidYMid meet"
      className="chart-bg block h-full w-full"
      style={{ touchAction: "none", cursor: drag.current ? "grabbing" : "grab", userSelect: "none", WebkitUserSelect: "none" }}
      role="img"
      aria-label="资产关系图谱（2D：滚轮缩放，拖拽平移，双击适配）"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerLeave={endDrag}
      onDoubleClick={onDoubleClick}
    >
      <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
        {sub.edges.map((e, i) => {
          const a = layout.get(e.src);
          const b = layout.get(e.dst);
          if (!a || !b) return null;
          const onFocus = focusId === e.src || focusId === e.dst;
          return (
            <g key={i}>
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={onFocus ? "var(--ink-faint)" : "var(--line)"}
                strokeWidth={(onFocus ? 1.6 : 1.1) / view.scale}
              />
              {edgeTextOn(e.src, e.dst) && (
                <text
                  x={(a.x + b.x) / 2}
                  y={(a.y + b.y) / 2 - 5}
                  fill="var(--ink-dim)"
                  fontSize={9 / view.scale}
                  textAnchor="middle"
                  stroke="var(--input)"
                  strokeWidth={2.5 / view.scale}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                  style={{ pointerEvents: "none" }}
                >
                  {e.kind}
                </text>
              )}
            </g>
          );
        })}
        {sub.nodes.map((n) => {
          const p = layout.get(n.id);
          if (!p) return null;
          const isSeed = n.id === sub.seed;
          const r = isSeed ? 13 : 8;
          const dr = r / Math.sqrt(view.scale); // 视觉半径
          const hitR = dr + 7 / view.scale; // 命中区：比视觉半径多 ~7 屏幕像素
          return (
            <g
              key={n.id}
              data-nid={n.id}
              transform={`translate(${p.x},${p.y})`}
              style={{ cursor: "pointer" }}
              onPointerEnter={() => setHover(n.id)}
              onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
              onPointerDown={(ev) => ev.stopPropagation()} /* 节点上按下不进平移捕获，保住 click/dblclick */
              onClick={(ev) => {
                ev.stopPropagation();
                onNodeClick(n.id);
              }}
              onDoubleClick={(ev) => {
                ev.preventDefault(); // 防浏览器文本选中（“蓝色选中复制”）
                ev.stopPropagation();
                if (onJump) onJump(n.id);
              }}
            >
              <title>{`${shortLabel(n.label)}${isSeed ? "（当前中心）" : ""} · 单击看详情 · 双击以它为中心跳转`}</title>
              {isSeed && <circle r={r + 7} fill="none" stroke={kindHex(n.kind)} strokeWidth={1.1 / view.scale} opacity={0.55} className="pulse-glow" style={{ transformBox: "fill-box", transformOrigin: "center" }} />}
              <circle r={hitR} fill="transparent" /> {/* 隐形命中区放大，点空白边缘也好点 */}
              <circle r={dr} fill={kindHex(n.kind)} opacity={isSeed ? 1 : 0.92} stroke="var(--bg)" strokeWidth={2 / Math.sqrt(view.scale)} />
              {selId === n.id && (
                <circle r={dr + 5 / view.scale} fill="none" stroke="var(--info)" strokeWidth={2.2 / view.scale} opacity={0.95} />
              )}
              {labelOn(n.id) && (
                <text
                  y={(isSeed ? 30 : 23) / view.scale}
                  fill="var(--ink-dim)"
                  fontSize={(isSeed ? 11.5 : 10) / view.scale}
                  textAnchor="middle"
                  /* 文字底色描边（paint-order: stroke）：压在连线/其它标签上时也读得出字 */
                  stroke="var(--input)"
                  strokeWidth={3 / view.scale}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                  style={{ pointerEvents: "none", fontWeight: isSeed ? 600 : 400 }}
                >
                  {shortLabel(n.label)}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

/** 3D 轨道俯瞰：球面散布 + 透视投影（几何/缓动纯函数见 lib/graph3d.ts），可拖拽旋转 + 自转 + 滚轮缩放 */
function GraphCanvas3D({
  sub,
  selId,
  onNodeClick,
  onJump,
  auto,
  onAutoChange,
  fitSignal,
  labelMode,
}: {
  sub: KbSubgraph;
  selId: string | null;
  onNodeClick: (id: string) => void;
  onJump?: (id: string) => void;
  auto: boolean;
  onAutoChange: (v: boolean) => void;
  fitSignal: number;
  labelMode: LabelMode;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [rot, setRot] = useState<Rot3>({ ...ROT_DEFAULT });
  const [cam, setCam] = useState(CAM3D_DEFAULT);
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; rx: number; ry: number } | null>(null);
  const raf = useRef<number | null>(null);
  const rotRef = useRef(rot);
  const camRef = useRef(cam);
  rotRef.current = rot;
  camRef.current = cam;

  // 「⤢ 适配」：平滑过渡到默认视角（整球入画 + 初始姿态，easeInOutCubic ~420ms）
  useEffect(() => {
    const fromRot = { ...rotRef.current };
    const fromCam = camRef.current;
    const near = Math.abs(fromCam - CAM3D_DEFAULT) < 0.01 && Math.abs(fromRot.x - ROT_DEFAULT.x) < 0.01 && Math.abs(fromRot.y - ROT_DEFAULT.y) < 0.01;
    if (near) return;
    const DUR = 420;
    const start = performance.now();
    let rafId = 0;
    const step = (now: number) => {
      const t = easeInOutCubic((now - start) / DUR);
      setCam(fromCam + (CAM3D_DEFAULT - fromCam) * t);
      setRot({
        x: fromRot.x + (ROT_DEFAULT.x - fromRot.x) * t,
        y: fromRot.y + (ROT_DEFAULT.y - fromRot.y) * t,
      });
      if (t < 1) rafId = requestAnimationFrame(step);
    };
    rafId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafId);
  }, [fitSignal]);

  const R = useMemo(() => clamp(130 + sub.nodes.length * 11, 150, 300), [sub.nodes.length]);
  const index = useMemo(() => graphIndex(sub), [sub]);

  // 3D 的标签降噪规则与 2D 同构：先用远近（depth）筛掉球背面，再按"关键节点 / 关注点及其邻居"
  const focusId = selId ?? hover;
  const near = focusId ? index.neighbors.get(focusId) : undefined;
  const labelOn3d = (s: { n: { id: string }; depth: number }): boolean => {
    if (labelMode === "off") return false;
    if (s.depth <= 0.48) return false; // 球背面/边缘的标签一律不画，否则叠成一团
    if (labelMode === "all") return true;
    return s.n.id === sub.seed || index.topLabels.has(s.n.id) || s.n.id === focusId || Boolean(near?.has(s.n.id));
  };

  const proj = useMemo(() => {
    const pts = fibonacciSphere(sub.nodes.length, R);
    const out = new Map<string, { x: number; y: number; z: number }>();
    sub.nodes.forEach((node, i) => {
      out.set(node.id, pts[i] ?? { x: 0, y: 0, z: R });
    });
    return out;
  }, [sub.nodes, R]);

  useEffect(() => {
    if (!auto) return;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      setRot((r) => ({ ...r, y: r.y + dt * 0.25 }));
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [auto]);

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    onAutoChange(false);
    drag.current = { x: e.clientX, y: e.clientY, rx: rot.x, ry: rot.y };
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    setRot({ x: clamp(d.rx + (e.clientY - d.y) * 0.005, -1.3, 1.3), y: d.ry + (e.clientX - d.x) * 0.006 });
  };
  const endDrag = () => {
    drag.current = null;
  };

  // 滚轮缩放（cam 越大=拉得越远；限制在可视区间）——同样只缩放，不带着页面滚
  useWheelZoom(svgRef, (e) => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 0.9 : 1.1;
    setCam((c) => clamp(c * f, CAM3D_MIN, CAM3D_MAX));
  });

  const spots: { n: { id: string; kind: string; label: string }; sx: number; sy: number; depth: number; seed: boolean }[] = [];

  sub.nodes.forEach((n) => {
    const v = proj.get(n.id);
    if (!v) return;
    const pv = project3D(v, rot, cam, R, GRAPH_W, GRAPH_H);
    spots.push({ n, sx: pv.sx, sy: pv.sy, depth: pv.depth, seed: n.id === sub.seed });
  });
  spots.sort((a, b) => a.depth - b.depth);

  const edgeSpots = sub.edges
    .map((e) => {
      const a = spots.find((s) => s.n.id === e.src);
      const b = spots.find((s) => s.n.id === e.dst);
      return a && b ? { a, b } : null;
    })
    .filter((x): x is { a: (typeof spots)[number]; b: (typeof spots)[number] } => x !== null)
    .sort((p, q) => Math.min(q.a.depth, q.b.depth) - Math.min(p.a.depth, p.b.depth));

  return (
    <svg
      ref={svgRef}
      width="100%"
      height="100%"
      viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`}
      preserveAspectRatio="xMidYMid meet"
      className="chart-bg block h-full w-full"
      style={{ touchAction: "none", cursor: drag.current ? "grabbing" : "grab", userSelect: "none", WebkitUserSelect: "none" }}
      role="img"
      aria-label="资产关系图谱 3D 俯瞰（拖拽旋转 · 节点可点）"
      onPointerDownCapture={() => onAutoChange(false)} /* 捕获阶段即停自转：节点上按下也生效 */
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerLeave={endDrag}
    >
      {edgeSpots.map(({ a, b }, i) => (
        <line key={i} x1={a.sx} y1={a.sy} x2={b.sx} y2={b.sy} stroke="var(--line)" strokeWidth={0.5 + a.depth * b.depth} opacity={0.2 + a.depth * b.depth * 0.4} />
      ))}
      {spots.map((s) => {
        const r = (s.seed ? 11 : 6.5) * (0.55 + 0.5 * s.depth);
        const fill = kindHex(s.n.kind);
        return (
          <g
            key={s.n.id}
            data-nid={s.n.id}
            transform={`translate(${s.sx},${s.sy})`}
            style={{ cursor: "pointer", opacity: 0.3 + 0.7 * s.depth }}
            onPointerEnter={() => setHover(s.n.id)}
            onPointerLeave={() => setHover((h) => (h === s.n.id ? null : h))}
            onPointerDown={(ev) => ev.stopPropagation()} /* 节点上按下不进旋转捕获，保住 click/dblclick */
            onClick={(ev) => {
              ev.stopPropagation();
              onNodeClick(s.n.id);
            }}
            onDoubleClick={(ev) => {
              ev.preventDefault(); // 防浏览器文本选中（“蓝色选中复制”）
              ev.stopPropagation();
              if (onJump) onJump(s.n.id);
            }}
          >
            <title>{`${shortLabel(s.n.label)}${s.seed ? "（当前中心）" : ""} · 单击看详情 · 双击以它为中心跳转`}</title>
            <circle r={r + 5} fill="transparent" /> {/* 隐形命中区放大（3D 小球更好点） */}
            {s.seed && <circle r={r + 6} fill="none" stroke={fill} strokeWidth={1.2} opacity={0.6} className="pulse-glow" style={{ transformBox: "fill-box", transformOrigin: "center" }} />}
            <circle r={r} fill={fill} stroke="var(--bg)" strokeWidth={1.5} />
            {selId === s.n.id && (
              <circle r={r + 3.5} fill="none" stroke="var(--info)" strokeWidth={2} opacity={0.95} />
            )}
            {labelOn3d(s) && (
              <text
                y={r + 13}
                fill="var(--ink-dim)"
                fontSize={s.seed ? 10.5 : 8.5}
                textAnchor="middle"
                stroke="var(--input)"
                strokeWidth={2.4}
                strokeLinejoin="round"
                paintOrder="stroke"
                style={{ pointerEvents: "none" }}
              >
                {shortLabel(s.n.label)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** 图谱视图：2D（缩放/平移/适配）与 3D（轨道/自转/缩放）切换 + 视图工具条
 *
 *  为什么控件从画布上"搬出来"：原来 2D/3D、适配、自转浮在画布左上角，
 *  恰好压在节点标签最密的地方；操作提示浮在右下角同理。现在统一放在画布上方的
 *  工具条里，画布区域完全留给图本身。 */
function GraphCanvas({
  sub,
  selId,
  onNodeClick,
  onJump,
  depth,
  onDepthChange,
  isOverview,
}: {
  sub: KbSubgraph;
  selId: string | null;
  onNodeClick: (id: string) => void;
  onJump?: (id: string) => void;
  depth: number;
  onDepthChange: (d: number) => void;
  isOverview: boolean;
}) {
  const [mode, setMode] = useState<ViewMode>("2d");
  const [fitSignal, setFitSignal] = useState(0);
  const [auto, setAuto] = useState(true);
  /** 标签显示策略：自动（只常显关键节点，悬停/选中/相邻时再出） / 全部 / 关闭 */
  const [labelMode, setLabelMode] = useState<LabelMode>("auto");

  // 3D 停转后 3.2s 无操作自动恢复待机自转（更好的“待机”体验）
  useEffect(() => {
    if (auto || mode !== "3d") return;
    const t = window.setTimeout(() => setAuto(true), 3200);
    return () => window.clearTimeout(t);
  }, [auto, mode]);

  return (
    <div>
      {/* 视觉语义：分段控件（Tabs）= 切状态；带描边的 .btn-ghost = 执行一次动作 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2 border-b border-line-soft">
        <Tabs
          items={[
            { value: "2d" as ViewMode, label: "◫ 2D", hint: "平面图：滚轮缩放、空白拖拽平移" },
            { value: "3d" as ViewMode, label: "◍ 3D", hint: "球面俯瞰：拖拽旋转、可自动缓转" },
          ]}
          value={mode}
          onChange={setMode}
        />
        <button
          className="btn-ghost btn-sm"
          onClick={() => setFitSignal((s) => s + 1)}
          title={mode === "2d" ? "把全部节点适配到可视区域（或双击画布空白）" : "3D 视角重置：整球入画并回到初始姿态"}
        >
          ⤢ 适配
        </button>
        {mode === "3d" && (
          <button
            className={`btn-ghost btn-sm ${auto ? "!text-info" : ""}`}
            onClick={() => setAuto((a) => !a)}
            title={auto ? "停止自动旋转" : "开始自动旋转（停转后无操作 3 秒自动恢复）"}
          >
            {auto ? "⏸ 停转" : "▶ 自转"}
          </button>
        )}
        {!isOverview && (
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-ink-faint">深度</span>
            <Tabs
              items={[
                { value: "1", label: "1", hint: "只看直接相邻的一圈" },
                { value: "2", label: "2", hint: "再多展开一跳" },
                { value: "3", label: "3", hint: "展开到三跳（节点最多）" },
              ]}
              value={String(depth)}
              onChange={(v) => onDepthChange(Number(v))}
            />
          </div>
        )}
        {/* 标签降噪：默认只常显关键节点，密集区才读得出字；想看全可切"全部" */}
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-ink-faint">标签</span>
          <Tabs
            items={[
              { value: "auto" as LabelMode, label: "自动", hint: "只常显关键节点；悬停 / 选中 / 相邻时补显（推荐）" },
              { value: "all" as LabelMode, label: "全部", hint: "所有节点都带名字，密集时可能互相压字" },
              { value: "off" as LabelMode, label: "关闭", hint: "只看点与线，鼠标悬停仍可看单个名字" },
            ]}
            value={labelMode}
            onChange={setLabelMode}
          />
        </div>
        <span className="ml-auto hidden xl:inline text-[10.5px] text-ink-faint whitespace-nowrap">
          {mode === "2d"
            ? "滚轮缩放 · 空白拖拽平移 · 悬停/单击节点看名字 · 双击节点换中心"
            : "拖拽旋转 · 滚轮缩放 · 悬停/单击节点看名字 · 双击节点换中心"}
        </span>
      </div>
      {/* 画布容器：高度显式给足，窄屏也不塌；SVG 撑满容器（不再按 600px 固定高度裁切） */}
      <div className="h-[360px] sm:h-[440px] lg:h-[540px] xl:h-[600px]">
        {mode === "2d" ? (
          <GraphCanvas2D
            sub={sub}
            selId={selId}
            onNodeClick={onNodeClick}
            onJump={onJump}
            fitSignal={fitSignal}
            labelMode={labelMode}
          />
        ) : (
          <GraphCanvas3D
            sub={sub}
            selId={selId}
            onNodeClick={onNodeClick}
            onJump={onJump}
            auto={auto}
            onAutoChange={setAuto}
            fitSignal={fitSignal}
            labelMode={labelMode}
          />
        )}
      </div>
    </div>
  );
}
