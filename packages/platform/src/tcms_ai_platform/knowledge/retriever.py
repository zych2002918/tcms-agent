"""P2 混合检索（GraphRAG 风格）+ 有界分层路由 + 沉淀闭环。

`HybridRetriever.retrieve(query)`（Q4 升级：图谱路由 → 域内向量 topk，不迷失）：
1. **路由**：从查询里提取域线索（子系统词/域别名/图谱 fault 邻接），映射到 1~2 个分区；
2. **有界域内检索**：只在路由到的分区做向量 topk（候选池有界，不再全库线性扫）；
   路由失败则回退全库 topk（诚实降级，保召回）；向量默认字符级哈希，真语义
   近义为可选通道（ApiEmbedder，无 key/失败自动降级哈希）；
3. **证据富化**：对每个命中取其图谱 neighbors 作证据路径（结构化邻接，带资产出处
   ref），并给 2 跳弱路径样例 weak_links + 邻接资产计数 anchor_stats（P1-4：命中与
   资产无直接边时仍给出"经谁可达"的可溯源弱关联，不发明）；
4. 返回 {query, routed_domains, bounded, hits} —— 路由信息供 UI 展示「检索走向」。

`GraphSink.record_run(result)`：执行结果 → run 节点 + 关联（组织记忆，L2 运行层）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .graph import KnowledgeGraph
from .lexical import BM25Index, LexDoc, rrf
from .vector import DOMAIN_ZH, Doc, VectorStore, system_domain

# 域路由词表：查询里的中文/英文域信号 → 分区（13 域与 domain_systems.json 同口径）
_DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "network": ("网络", "心跳", "VCU", "总线", "报文", "CRC", "看门狗", "重启", "网关", "司控", "主控", "network", "bus"),
    "brake": ("制动", "brake", "刹车", "缸压", "防滑", "闸瓦", "紧急制动", "EB", "制动缸"),
    "traction": ("牵引", "traction", "超速", "限速", "逆变", "变流器", "母线", "手柄", "overspeed", "ATP"),
    "door": ("车门", "door", "门控", "联锁门", "站台门", "门气源", "门开", "后车门"),
    "pantograph": ("受电弓", "弓", "网压", "高压", "拉弧", "升弓", "pantograph"),
    "battery": ("电池", "SOC", "储能", "绝缘", "单体", "充放电", "bms", "battery", "均衡"),
    "aux": ("辅助", "aux", "低压母线", "辅助变流", "接触器", "过载", "辅逆"),
    "hvac": ("空调", "暖通", "压缩机", "客室", "制冷", "制热", "新风", "滤网", "加热器", "舒适度", "hvac"),
    "pis": ("PIS", "乘客信息", "显示屏", "对讲", "播报", "到站", "pis", "display"),
    "light": ("照明", "应急灯", "灯光", "light", "lamp"),
    "fire": ("烟火", "烟雾", "灭火", "探测器", "火警", "疏散", "fire", "smoke"),
    "bogie": ("走行部", "轴温", "轴箱", "轴承", "振动", "转向架", "bogie"),
    "signal": ("信号", "传感器", "漂移", "卡死", "表决", "超范围", "signal", "sensor"),
}


def _route_domains(query: str) -> list[str]:
    """从查询文本提取域信号 → 命中的分区域（按命中词数降序，最多 2 个）。"""
    q = (query or "").lower()
    scored: list[tuple[int, str]] = []
    for dom, terms in _DOMAIN_TERMS.items():
        hit = sum(1 for t in terms if t.lower() in q)
        if hit:
            scored.append((hit, dom))
    scored.sort(reverse=True)
    return [d for _, d in scored[:2]]


def _shared_cjk(query: str, text: str) -> int:
    """查询与文本共享的中文字符数（图谱路由的"词元重合"闸门）。"""
    qs = set(re.findall(r"[\u4e00-\u9fff]", query or ""))
    ts = set(re.findall(r"[\u4e00-\u9fff]", text or ""))
    return len(qs & ts)


@dataclass
class RetrievalHit:
    doc_id: str
    kind: str
    text: str
    score: float
    graph_neighbors: list[dict]  # [{id, kind, label, via}]


class HybridRetriever:
    """图谱路由 + 有界域内语义检索 + 图谱邻接证据（GraphRAG 混合）。"""

    def __init__(self, store: VectorStore, graph: KnowledgeGraph) -> None:
        self.store = store
        self.graph = graph
        self._bm25_index: BM25Index | None = None
        self._docs_map: dict[str, Doc] | None = None

    # ---- 词法通道（懒建；与向量通道同语料，供混合检索/评测） ----

    def _docs_map_of(self) -> dict[str, Doc]:
        if self._docs_map is None:
            self._docs_map = {d.doc_id: d for d in self.store.docs}
        return self._docs_map

    def _bm25(self) -> BM25Index:
        """懒建 BM25 索引（语料 = 当前 store.docs；确定性、可单测）。"""
        if self._bm25_index is None:
            self._bm25_index = BM25Index(
                [LexDoc(doc_id=d.doc_id, text=d.text, domain=str(d.meta.get("domain", ""))) for d in self.store.docs]
            )
        return self._bm25_index

    def _route_via_graph(self, query: str, probe: int = 3) -> list[str]:
        """图谱先定位域：查询无显式域词时，用全局 top-k 命中经 belongs_to 边
        反查所属系统 → 得到 1 个分区域（Q4"先图谱定位系统再域内 topk"兜底）。

        只采信图谱 belongs_to 边（enrich_graph 注入的 system 节点），且查询与
        候选文档须共享 ≥3 个中文字符（词元重合闸门）——防止无域查询被哈希噪声
        文档误路由。未 enrich 的图没有系统边 → 返回 []（走全局检索，诚实降级）。
        """
        if not self.graph.nodes:
            return []
        # 通用/诊断中性字（故障、检测、处置、系统…）：两文本只在"故障/处置"等
        # 词上重合不算语义证据——防止"无码症状"查询（如仪表盘闪烁）被这类词带进错误域。
        _GENERIC_CJK = frozenset(
            "故障检测处置注入恢复系统等级动作期望监控告警报警方法分析流程事件影响信息"
        )
        qchars = set(re.findall(r"[\u4e00-\u9fff]", query or "")) - _GENERIC_CJK
        probe_hits = self.store.search(query, k=probe, domains=None)
        votes: dict[str, int] = {}
        for h in probe_hits:
            nid = h["doc_id"]
            if nid not in self.graph.nodes:
                continue
            dom = ""
            for nb, via in self.graph.neighbors(nid):
                if via == "belongs_to" and nb.startswith("system:"):
                    dom = system_domain(nb.split(":", 1)[1])
                    break
            if not dom:
                continue
            # 词元重合闸门（排除通用字后仍需 ≥3 非通用重合）→ 防哈希噪声/假域证据
            tchars = set(re.findall(r"[\u4e00-\u9fff]", h.get("text") or "")) - _GENERIC_CJK
            shared = len(qchars & tchars)
            if shared >= 3:
                votes[dom] = votes.get(dom, 0) + 1
        if not votes:
            return []
        # 多数一致 → 采纳；唯一命中也可采纳（域内召回不足由 mixed 回退保底）
        best = max(votes.items(), key=lambda kv: kv[1])
        return [best[0]]

    def _route_to(self, query: str) -> tuple[list[str], str]:
        """路由：词表 → 图谱定位 → 全局（返回 routed_domains 与来源标注）。"""
        routed = _route_domains(query)
        if routed:
            return routed, "terms"
        routed = self._route_via_graph(query)
        return (routed, "graph") if routed else ([], "")

    # ---- P1-4 弱证据：资产锚点 + 2 跳路径样例（A/B 无直接边时的可溯源弱关联） ----

    def _two_hop_samples(self, nid: str, max_total: int = 4) -> list[dict]:
        """命中节点经一个中间节点的 2 跳可达样例（最多 max_total 条，防爆量）。

        目的：命中与某资产无直接边时，仍给出"经谁可达"的可溯源弱证据——每条带
        中间节点与目标节点的资产出处 ref（node_asset_ref），路径真实存在于图，
        不发明。若无 2 跳目标或节点不在图 → []。
        """
        if nid not in self.graph.nodes:
            return []
        direct = {nb for nb, _ in self.graph.neighbors(nid)}
        out: list[dict] = []
        for mid, via in self.graph.neighbors(nid)[:3]:
            nb = self.graph.nodes.get(mid)
            if nb is None:
                continue
            for nn, via2 in self.graph.neighbors(mid)[:3]:
                if nn == nid or nn in direct:
                    continue
                nnb = self.graph.nodes.get(nn)
                if nnb is None:
                    continue
                out.append(
                    {
                        "kind": "2hop",
                        "path": [nid, mid, nn],
                        "middle": {
                            "id": mid,
                            "kind": nb.kind,
                            "label": nb.label,
                            "via": via,
                            "ref": KnowledgeGraph.node_asset_ref(mid),
                        },
                        "target": {
                            "id": nn,
                            "kind": nnb.kind,
                            "label": nnb.label,
                            "via": via2,
                            "ref": KnowledgeGraph.node_asset_ref(nn),
                        },
                    }
                )
                if len(out) >= max_total:
                    return out
        return out

    # ---- P2-3：run 记忆参与召回（真实执行过 → 检索/诊断证据可见 run 引用） ----

    def _runs_index(self) -> dict[str, list[dict]]:
        """一次调用内只建一次：executed 边 → {命中节点id: [run 引用]}（最近在前）。

        run 引用含 passed/failed/all_passed + 资产出处（runtime:run_id）——让"该资产
        近期有真实执行记录"成为可机器自证的证据，而非记忆摆设。
        """
        idx: dict[str, list[dict]] = {}
        for e in self.graph.edges:
            if e.kind != "executed":
                continue
            rid, nid = (e.src, e.dst) if e.dst.startswith(("scenario:", "fault:", "run:")) else (e.dst, e.src)
            if not rid.startswith("run:") or not nid.startswith(("scenario:", "fault:")):
                continue
            rn = self.graph.nodes.get(rid)
            if rn is None:
                continue
            props = rn.props or {}
            idx.setdefault(nid, []).append(
                {
                    "run_id": rid.split(":", 1)[1],
                    "node": rid,
                    "scenario": props.get("scenario", ""),
                    "passed": props.get("passed", 0),
                    "failed": props.get("failed", 0),
                    "all_passed": props.get("all_passed"),
                    "ref": KnowledgeGraph.node_asset_ref(rid),
                }
            )
        return {nid: runs[-3:][::-1] for nid, runs in idx.items()}  # 最近 3 条、新在前

    def _pack(
        self,
        query: str,
        ordered: list[dict],
        k: int,
        neighbor_limit: int,
        routed: list[str],
        route_source: str,
        mixed: bool,
    ) -> dict:
        """把有序命中列表组装成统一响应（补图谱邻接证据 + 路由指标）。"""
        enriched = []
        runs_idx = self._runs_index()  # P2-3：一次调用建一次（有真实 run 才非空）
        for h in ordered[: max(k, 8)]:
            nid = h["doc_id"]  # 向量 doc_id 与图节点 id 对齐（fault:x / req:x ...）
            neighbors = []
            anchor_stats: dict[str, int] = {}
            if nid in self.graph.nodes:
                nbs = self.graph.neighbors(nid)
                for nb_id, via in nbs[:neighbor_limit]:
                    nb = self.graph.nodes.get(nb_id)
                    if nb:
                        neighbors.append(
                            {
                                "id": nb.id,
                                "kind": nb.kind,
                                "label": nb.label,
                                "via": via,
                                "ref": KnowledgeGraph.node_asset_ref(nb.id),  # P2-1 出处
                            }
                        )
                for nb_id, _via in nbs:
                    nb = self.graph.nodes.get(nb_id)
                    if nb:
                        anchor_stats[nb.kind] = anchor_stats.get(nb.kind, 0) + 1
            enriched.append(
                {
                    "doc_id": h["doc_id"],
                    "kind": h["kind"],
                    "text": h["text"],
                    "score": h["score"],
                    "domain": (h.get("meta") or {}).get("domain", ""),
                    "graph_neighbors": neighbors,
                    # P1-4：弱证据升级（无直接边时的可溯源关联）
                    "source_ref": KnowledgeGraph.node_asset_ref(nid),
                    "anchor_stats": anchor_stats,  # 邻接资产按类计数（场景/信号/需求…）
                    "weak_links": self._two_hop_samples(nid),
                    # P2-3：该资产近期的真实执行记录（有 run 边才出现）
                    "recent_runs": runs_idx.get(nid, []),
                }
            )
        # 分区路由命中率：最终 top-k 命中里属于路由分区的比例（bounded 时应 =1.0）
        rset = set(routed)
        surfaced = enriched[:k]
        route_precision = (
            round(
                sum(1 for h in surfaced if (h["domain"] or "") in rset) / len(surfaced),
                3,
            )
            if surfaced and rset
            else None
        )
        return {
            "query": query,
            "routed_domains": routed,
            "routed_zh": [DOMAIN_ZH.get(d, d) for d in routed],
            "route_source": route_source,  # terms=词表路由 / graph=图谱定位域 / ''=全局
            "route_precision": route_precision,  # 分区命中率（域内召回质量）
            "bounded": bool(routed),
            "mixed_fallback": mixed,
            "hits": enriched,
        }

    def retrieve(self, query: str, k: int = 5, neighbor_limit: int = 6) -> dict:
        """向量检索（GraphRAG 语义通道；保持原行为，兼容既有调用）。"""
        routed, route_source = self._route_to(query)
        hits = self.store.search(query, k=max(k * 2, 8), domains=routed if routed else None)
        # 域内候选可能不足 k → 诚实回退全库补召回（标注 mixed=true）
        mixed = len(hits) < k and bool(routed)
        if mixed:
            extra = self.store.search(query, k=k, domains=None)
            seen = {h["doc_id"] for h in hits}
            hits.extend(h for h in extra if h["doc_id"] not in seen)
        return self._pack(query, hits, k, neighbor_limit, routed, route_source, mixed)

    def retrieve_hybrid(
        self,
        query: str,
        k: int = 5,
        neighbor_limit: int = 6,
        vector_pool: int = 20,
    ) -> dict:
        """混合检索（向量 + BM25 词法，RRF 融合）：兼顾语义近义与字面精确。

        返回结构与 `retrieve` 完全同构（hits 带图谱邻接证据），前端/Agent 可零改动
        切换通道。score 字段为融合分（RRF，确定性）。
        """
        routed, route_source = self._route_to(query)
        doms = routed if routed else None
        vector = self.store.search(query, k=max(vector_pool, k * 2), domains=doms)
        mixed = len(vector) < k and bool(routed)
        if mixed:
            extra = self.store.search(query, k=k, domains=None)
            seen = {h["doc_id"] for h in vector}
            vector.extend(h for h in extra if h["doc_id"] not in seen)
        # 词法通道（单域路由 → 域内词法；多域/全局 → 全库词法）
        lex_domain = doms[0] if doms and len(doms) == 1 else None
        lex_top = self._bm25().top(query, k=max(vector_pool, k * 2), domain=lex_domain)
        vec_ids = [h["doc_id"] for h in vector]
        lex_ids = [doc_id for doc_id, _ in lex_top]
        fused = rrf([vec_ids, lex_ids], top=max(k * 2, 8))
        ordered: list[dict] = []
        dmap = self._docs_map_of()
        for doc_id, fscore in fused:
            vh = next((h for h in vector if h["doc_id"] == doc_id), None)
            doc = dmap.get(doc_id)
            if vh is None and doc is None:
                continue
            ordered.append(
                {
                    "doc_id": doc_id,
                    "kind": vh["kind"] if vh else (doc.kind if doc else ""),
                    "text": vh["text"] if vh else (doc.text if doc else ""),
                    "meta": vh["meta"] if vh else (doc.meta if doc else {}),
                    "score": round(fscore, 4),
                }
            )
        # 兜底：融合结果为空时退回向量顺序（诚实降级，不空手）
        if not ordered:
            ordered = vector
        return self._pack(query, ordered, k, neighbor_limit, routed, route_source, mixed)

    def subgraph(self, seed_id: str, depth: int = 2) -> dict:
        """图谱子图（供可视化 / 前端图谱工作台）。"""
        if seed_id not in self.graph.nodes:
            return {"seed": seed_id, "nodes": [], "edges": [], "node_count": 0}
        return self.graph.subgraph(seed_id, depth)


class GraphSink:
    """沉淀闭环：执行结果 → 知识库 run 节点（组织记忆雏形）。"""

    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph
        self.runs: list[dict] = []

    def record_run(self, run_id: str, scenario_file: str, result: dict) -> None:
        """记录一次真实执行。result 含 passed/failed/assertions。"""
        passed = result.get("passed", 0)
        failed = result.get("failed", 0)
        node_id = self.graph.add_node(
            "run",
            run_id,
            f"run:{run_id}",
            {
                "scenario": scenario_file,
                "passed": passed,
                "failed": failed,
                "all_passed": result.get("all_passed"),
            },
        )
        # 关联到场景节点
        scen_id = f"scenario:{scenario_file}"
        if scen_id in self.graph.nodes:
            self.graph.add_edge("run", run_id, "scenario", scenario_file, "executed")
        self.runs.append(
            {"run_id": run_id, "node_id": node_id, "scenario": scenario_file, "result": result}
        )
