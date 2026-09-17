import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type KbNode, type KbSearchHit, type KbSubgraph } from "../api";
import { Panel, Tag, SkeletonRows, EmptyState, Explain } from "../components/ui";
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
    } catch (e) {
      setErr(String(e));
    } finally {
      setSearching(false);
    }
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

  const order = ["fault", "message", "signal", "scenario", "requirement", "function", "device", "run"];
  const kindTabs = grouped
    ? order.filter((k) => grouped[k]?.length).map((k) => ({ kind: k, n: grouped[k]!.length }))
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

  return (
    <div className="mx-auto w-full max-w-[1840px] space-y-4">
      {/* KB 索引概览（图谱+向量规模：让人一眼看到知识底座的深度） */}
      {kbStats && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 text-[11px] text-ink-dim">
          <span className="font-medium text-ink">
            知识底座
          </span>
          <span className="num">图谱 <b className="text-info">{kbStats.graph.nodes}</b> 节点 / <b className="text-info">{kbStats.graph.edges}</b> 边</span>
          <span className="num">向量索引 <b className="text-vio">{kbStats.vector.docs}</b> 条文档</span>
          <span className="text-ink-faint">
            实体类型：{Object.keys(kbStats.graph.by_kind).length} 种（资产 + 领域知识：驾驶模式 / 联锁 / 阈值 / 危害…）
          </span>
        </div>
      )}

      {/* 图谱双价值：这一张图，人怎么用 / AI 怎么用（默认收起，想看时展开） */}
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
            <div className="bg-surface-2/40 rounded-xl p-3.5 border border-line-soft">
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
            <div className="bg-surface-2/40 rounded-xl p-3.5 border border-line-soft">
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

      {/* 检索区 */}
      <Panel bodyClass="p-3">
        <div className="flex flex-col sm:flex-row gap-2">
          <div className="relative flex-1">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint text-sm">🔍</span>
            <input
              className="input pl-9"
              placeholder="用大白话问，如：车门故障了还能发车吗 / 紧急制动失败会怎样"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
              aria-label="知识检索"
            />
          </div>
          <button className="btn justify-center sm:w-auto" onClick={() => search()} disabled={searching || !query.trim()}>
            {searching ? "检索中…" : "检索"}
          </button>
        </div>
        <Explain text="怎么读结果：每一条命中都带「它是哪种资产 + 一句话解释 + 它与谁相连」。点任意条目可展开成关系图谱。" />
      </Panel>

      {err && <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ {err}</div>}

      {/* 检索中 */}
      {searching && (
        <Panel title="正在检索领域知识…" bodyClass="py-1">
          <SkeletonRows rows={3} cols={3} />
        </Panel>
      )}

      {/* 结果（分类展示） */}
      {!searching && hits && (
        <>
          {hits.length === 0 ? (
            <Panel>
              <EmptyState
                icon="?"
                title="没找到直接匹配"
                desc="试试更口语化的问法，例如「心跳丢失」「门没关就发车」「超速」。也可点击下方类型直接浏览资产。"
              />
            </Panel>
          ) : (
            <>
              {/* 检索走向（Q4 有界分层：先图谱路由到域，再域内向量 topk） */}
              {route && route.domains.length > 0 && (
                <div className="px-1 -mt-1 mb-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
                  <span>检索已路由到分域：</span>
                  {route.zh.map((z) => (
                    <span key={z} className="tag text-info border-info/30 bg-info/5">
                      {z}
                    </span>
                  ))}
                  {route.bounded && <span>· 有界域内检索（不整库迷失）</span>}
                  {route.mixed && <span>· 域内不足已全局补召回</span>}
                </div>
              )}
              {/* 分类 tab */}
              <div className="flex flex-wrap items-center gap-1.5 px-1">
                <button
                  className={`btn-ghost btn-sm ${activeKind === "all" ? "!text-info !border-info/50" : ""}`}
                  onClick={() => setActiveKind("all")}
                >
                  全部 {hits.length}
                </button>
                {kindTabs.map(({ kind, n }) => (
                  <button
                    key={kind}
                    className={`btn-ghost btn-sm ${activeKind === kind ? "!text-info !border-info/50" : ""}`}
                    onClick={() => setActiveKind(kind)}
                  >
                    {KIND_META[kind]?.label ?? kind} {n}
                  </button>
                ))}
              </div>

              {/* 结果组 */}
              <div className="space-y-4 mt-1">
                {kindTabs
                  .filter(({ kind }) => activeKind === "all" || activeKind === kind)
                  .map(({ kind, n }) => (
                    <div key={kind}>
                      <div className="px-1 mb-1.5 flex items-baseline gap-2">
                        <span className="text-[12px] font-semibold text-ink">
                          {KIND_META[kind]?.label} <span className="text-ink-faint font-normal">({n})</span>
                        </span>
                        <span className="text-[11px] text-ink-faint">{KIND_META[kind]?.what}</span>
                      </div>
                      <div className="space-y-1.5">
                        {grouped![kind].map((h) => (
                          <div
                            key={h.doc_id}
                            className="panel panel-hover p-3 cursor-pointer step-in"
                            onClick={() => focus(h.doc_id)}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => e.key === "Enter" && focus(h.doc_id)}
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <code className="kbd-mono">{h.doc_id}</code>
                              <Tag tone={KIND_META[kind]?.color}>
                                相关度 {(h.score * 100).toFixed(0)}%
                              </Tag>
                              {h.domain && (
                                <span className="text-[10px] text-ink-faint tag !bg-transparent border-line-soft">
                                  域·{h.domain}
                                </span>
                              )}
                              <span className="ml-auto text-[11px] text-ink-faint">点击查看关系图谱 →</span>
                            </div>
                            <div className="mt-1.5 text-[13px] text-ink leading-5 line-clamp-2">
                              {h.text.slice(0, 160)}
                              {h.text.length > 160 ? "…" : ""}
                            </div>
                            <Explain text={plainExplain(h.doc_id, h.text)} />
                            {h.graph_neighbors.length > 0 && (
                              <div className="mt-1.5 flex flex-wrap gap-1">
                                {h.graph_neighbors.slice(0, 5).map((nb) => (
                                  <Tag key={nb.id} tone="dim" title={`${nb.via} → ${nb.label}`}>
                                    {KIND_META[nb.kind]?.label ?? nb.kind} · {nb.label}
                                  </Tag>
                                ))}
                                {h.graph_neighbors.length > 5 && (
                                  <span className="text-[11px] text-ink-faint">+{h.graph_neighbors.length - 5}</span>
                                )}
                              </div>
                            )}
                          </div>
                        ))}
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
                {histLen > 0 && (
                  <button className="btn-ghost btn-sm" onClick={() => void goBack()} title="返回上一视图（浏览历史可回退）">
                    ⬅ 返回
                  </button>
                )}
                {isOverview ? (
                  <Tag tone="info">基础骨架 · 单击看详情 / 双击跳转</Tag>
                ) : (
                  <div className="flex items-center gap-1 text-[11px] text-ink-faint">
                    深度
                    {[1, 2, 3].map((d) => (
                      <button
                        key={d}
                        className={`btn-ghost btn-sm !px-2 !py-0.5 !text-[11px] ${depth === d ? "!text-info !border-info/50" : ""}`}
                        onClick={() => changeDepth(d)}
                        disabled={d === depth}
                      >
                        {d}
                      </button>
                    ))}
                  </div>
                )}
                <Tag tone="dim">{sub.node_count} 节点 / {sub.edges.length} 边</Tag>
                {!isOverview && (
                  <button className="btn-ghost btn-sm" onClick={() => void loadOverview()} title="回到 13 系统域基础关联图谱">
                    ↺ 骨架
                  </button>
                )}
              </div>
            }
            bodyClass="p-0"
          >
            {/* 图例 + 分布（当前子图按类型统计；色点与画布一致） */}
            <div className="px-4 pt-2.5 flex flex-wrap items-center gap-x-1 gap-y-1.5">
              {legendKinds.map((k) => (
                <span key={k} className="inline-flex items-center gap-1.5 text-[11px] text-ink-dim mr-2">
                  <span className="h-2 w-2 rounded-full" style={{ background: kindHex(k) }} />
                  {KIND_META[k]?.label ?? k}
                  {subKinds ? <span className="text-ink-faint num">×{subKinds[k] ?? 0}</span> : null}
                </span>
              ))}
            </div>
            <GraphCanvas sub={sub} selId={selId} onNodeClick={openNode} onJump={focus} />
          </Panel>
          <div className="px-1 -mt-2 text-[11px] text-ink-faint">
            说明：中心是「{seedLabel}」，连线标着关系（如“发送方→”“触发”）；色点代表实体类型，数字是该类型在本子图里的个数。
            单击节点=选中并看详情（图上会出现高亮环），双击节点=以其为中心跳转；点节点旁标签也可跳转；调整「深度」可扩/缩关联范围，⬅ 返回可回上一视图。
          </div>
        </>
      )}

      {/* 节点详情 */}
      {selNode && (() => {
        // 一键动作目标：scenario 本体，或 fault/… 关联的第一个复现场景
        const scenFile = selNode.kind === "scenario"
          ? selNode.id.split(":")[1]
          : (selNode.neighbors?.find((nb) => nb.kind === "scenario")?.id.split(":")[1] ?? null);
        return (
        <Panel
          title={
            <>
              <Tag tone={KIND_META[selNode.kind]?.color}>{KIND_META[selNode.kind]?.label ?? selNode.kind}</Tag>{" "}
              {selNode.label}
            </>
          }
          right={
            <button className="btn-ghost btn-sm" onClick={() => setSelNode(null)}>
              关闭
            </button>
          }
        >
          <div className="kbd-mono mb-2">{selNode.id}</div>
          {KIND_META[selNode.kind] && <Explain text={KIND_META[selNode.kind].what} />}
          {/* 图谱 → 动作（不绕回其它页） */}
          <div className="mt-2 flex flex-wrap gap-1.5">
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
            {scenFile && (
              <button
                className="btn btn-sm"
                onClick={() => {
                  window.location.href = `/faultlab?scenario=${encodeURIComponent(scenFile)}&from=kb`;
                }}
                title="跳到 FaultLab 播放该场景的故障动画"
              >
                ▶ 去 FaultLab 演示
              </button>
            )}
          </div>
          {selNode.props && Object.keys(selNode.props).length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 mt-2">
              {Object.entries(selNode.props).map(([k, v]) => (
                <div key={k} className="text-xs flex gap-2">
                  <span className="text-ink-faint shrink-0">{k}</span>
                  <span className="text-ink truncate" title={String(v)}>
                    {String(v)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {selNode.neighbors && selNode.neighbors.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] text-ink-faint mb-1.5">直接关联（{selNode.neighbors.length}）</div>
              <div className="flex flex-wrap gap-1.5">
                {selNode.neighbors.map((nb) => (
                  <Tag key={nb.id} tone={KIND_META[nb.kind]?.color} onClick={() => focus(nb.id)} title="点击跳转到该节点">
                    {nb.label}
                  </Tag>
                ))}
              </div>
            </div>
          )}
        </Panel>
        );
      })()}

      {/* 无任何操作时：提示浏览实体 */}
      {!hits && !sub && !selNode && !searching && (
        <Panel bodyClass="py-1">
          <EmptyState
            icon="◈"
            title="从这里开始检索"
            desc="上方输入框支持大白话提问。也可以先看几个高频对象找感觉："
          />
          <div className="flex flex-wrap gap-1.5 justify-center pb-3">
            {["message:DoorControl", "fault:overspeed", "function:F-EBM", "requirement:SR-01"].map((id) => (
              <Tag key={id} tone={KIND_META[id.split(":")[0]]?.color} onClick={() => focus(id)}>
                {id}
              </Tag>
            ))}
          </div>
        </Panel>
      )}
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

/** 2D 力导向 + 缩放平移画布 */
function GraphCanvas2D({
  sub,
  selId,
  onNodeClick,
  onJump,
  fitSignal,
}: {
  sub: KbSubgraph;
  selId: string | null;
  onNodeClick: (id: string) => void;
  onJump?: (id: string) => void;
  fitSignal: number;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [view, setView] = useState<ViewState>({ scale: 1, tx: 0, ty: 0 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);

  const layout = useMemo(() => computeLayout(sub), [sub]);

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

  const toLocal = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const r = svg.getBoundingClientRect();
    return { x: ((clientX - r.left) / r.width) * GRAPH_W, y: ((clientY - r.top) / r.height) * GRAPH_H };
  };

  const zoomAt = (mx: number, my: number, factor: number) => {
    setView((v) => {
      const scale = clamp(v.scale * factor, 0.15, 4);
      const k = scale / v.scale;
      return { scale, tx: mx - (mx - v.tx) * k, ty: my - (my - v.ty) * k };
    });
  };

  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    const { x, y } = toLocal(e.clientX, e.clientY);
    zoomAt(x, y, e.deltaY < 0 ? 1.18 : 1 / 1.18);
  };

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false };
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const rect = svgRef.current?.getBoundingClientRect();
    const w = rect?.width ?? GRAPH_W;
    const h = rect?.height ?? GRAPH_H;
    const dx = ((e.clientX - d.x) / w) * GRAPH_W;
    const dy = ((e.clientY - d.y) / h) * GRAPH_H;
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

  return (
    <svg
      ref={svgRef}
      width="100%"
      height={GRAPH_H}
      viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`}
      className="chart-bg"
      style={{ display: "block", touchAction: "none", cursor: drag.current ? "grabbing" : "grab", userSelect: "none", WebkitUserSelect: "none" }}
      role="img"
      aria-label="资产关系图谱（2D：滚轮缩放，拖拽平移，双击适配）"
      onWheel={onWheel}
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
          return (
            <g key={i}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--line)" strokeWidth={1.1 / view.scale} />
              {showEdgeText && (
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 5} fill="var(--ink-faint)" fontSize={9 / view.scale} textAnchor="middle" style={{ pointerEvents: "none" }}>
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
              {showLabels && (
                <text
                  y={(isSeed ? 30 : 23) / view.scale}
                  fill="var(--ink-dim)"
                  fontSize={(isSeed ? 11.5 : 10) / view.scale}
                  textAnchor="middle"
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
}: {
  sub: KbSubgraph;
  selId: string | null;
  onNodeClick: (id: string) => void;
  onJump?: (id: string) => void;
  auto: boolean;
  onAutoChange: (v: boolean) => void;
  fitSignal: number;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [rot, setRot] = useState<Rot3>({ ...ROT_DEFAULT });
  const [cam, setCam] = useState(CAM3D_DEFAULT);
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

  // 滚轮缩放（cam 越大=拉得越远；限制在可视区间）
  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    e.stopPropagation();
    const f = e.deltaY < 0 ? 0.9 : 1.1;
    setCam((c) => clamp(c * f, CAM3D_MIN, CAM3D_MAX));
  };

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
      height={GRAPH_H}
      viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`}
      className="chart-bg"
      style={{ display: "block", touchAction: "none", cursor: drag.current ? "grabbing" : "grab", userSelect: "none", WebkitUserSelect: "none" }}
      role="img"
      aria-label="资产关系图谱 3D 俯瞰（拖拽旋转 · 节点可点）"
      onPointerDownCapture={() => onAutoChange(false)} /* 捕获阶段即停自转：节点上按下也生效 */
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerLeave={endDrag}
      onWheel={onWheel}
    >
      {edgeSpots.map(({ a, b }, i) => (
        <line key={i} x1={a.sx} y1={a.sy} x2={b.sx} y2={b.sy} stroke="var(--line)" strokeWidth={0.5 + a.depth * b.depth} opacity={0.2 + a.depth * b.depth * 0.4} />
      ))}
      {spots.map((s) => {
        const r = (s.seed ? 11 : 6.5) * (0.55 + 0.5 * s.depth);
        const fill = kindHex(s.n.kind);        return (
          <g
            key={s.n.id}
            data-nid={s.n.id}
            transform={`translate(${s.sx},${s.sy})`}
            style={{ cursor: "pointer", opacity: 0.3 + 0.7 * s.depth }}
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
            {s.depth > 0.48 && (
              <text y={r + 13} fill="var(--ink-dim)" fontSize={s.seed ? 10.5 : 8.5} textAnchor="middle" style={{ pointerEvents: "none" }}>
                {shortLabel(s.n.label)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** 图谱视图：2D（缩放/平移/适配）与 3D（轨道/自转/缩放）切换 + 控制条 */
function GraphCanvas({
  sub,
  selId,
  onNodeClick,
  onJump,
}: {
  sub: KbSubgraph;
  selId: string | null;
  onNodeClick: (id: string) => void;
  onJump?: (id: string) => void;
}) {
  const [mode, setMode] = useState<ViewMode>("2d");
  const [fitSignal, setFitSignal] = useState(0);
  const [auto, setAuto] = useState(true);

  // 3D 停转后 3.2s 无操作自动恢复待机自转（更好的“待机”体验）
  useEffect(() => {
    if (auto || mode !== "3d") return;
    const t = window.setTimeout(() => setAuto(true), 3200);
    return () => window.clearTimeout(t);
  }, [auto, mode]);

  return (
    <div className="relative">
      {mode === "2d" ? (
        <GraphCanvas2D sub={sub} selId={selId} onNodeClick={onNodeClick} onJump={onJump} fitSignal={fitSignal} />
      ) : (
        <GraphCanvas3D sub={sub} selId={selId} onNodeClick={onNodeClick} onJump={onJump} auto={auto} onAutoChange={setAuto} fitSignal={fitSignal} />
      )}
      {/* 视图控制条 */}
      <div className="absolute left-2 top-2 z-10 flex items-center gap-1.5">
        <div className="flex items-center rounded-lg border border-line bg-surface/90 p-0.5 shadow-sm">
          {(["2d", "3d"] as ViewMode[]).map((m) => (
            <button
              key={m}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${mode === m ? "bg-info text-[color:var(--on-info)]" : "text-ink-dim hover:text-ink"}`}
              onClick={() => setMode(m)}
            >
              {m === "2d" ? "◫ 2D" : "◍ 3D"}
            </button>
          ))}
        </div>
        {mode === "2d" ? (
          <button className="btn-soft" onClick={() => setFitSignal((s) => s + 1)} title="把全部节点适配到可视区域（或双击画布）">
            ⤢ 适配
          </button>
        ) : (
          <>
            <button className="btn-soft" onClick={() => setFitSignal((s) => s + 1)} title="3D 视角重置：整球入画并回到初始姿态">
              ⤢ 适配
            </button>
            <button className={`btn-soft ${auto ? "!text-info" : ""}`} onClick={() => setAuto((a) => !a)} title={auto ? "停止自动旋转" : "开始自动旋转（停转后无操作 3 秒自动恢复）"}>
              {auto ? "⏸ 停转" : "▶ 自转"}
            </button>
          </>
        )}
      </div>
      {/* 操作提示 */}
      <div className="absolute bottom-2 right-2 z-10 pointer-events-none">
        <span className="rounded-md border border-line-soft bg-surface/70 px-1.5 py-0.5 text-[10px] text-ink-faint">
          {mode === "2d"
            ? "滚轮缩放 · 空白拖拽平移 · 单击节点=选中看详情 · 双击节点=以其为中心跳转 · 双击空白适配"
            : "拖拽空白旋转 · 滚轮缩放 · 单击节点=选中看详情 · 双击节点=以其为中心跳转"}
        </span>
      </div>
    </div>
  );
}
