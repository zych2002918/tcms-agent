"""P2 知识图谱：从 L1 AssetModel 派生的实体/关系图（GraphRAG 结构通道）。

节点类型（对齐北极星 schema：信号→报文→设备→功能→需求→故障→场景）：
    signal / message / device / function / requirement / fault / scenario

关系（direction-aware，全部从真实资产派生，无手编）：
    signal -in-> message
    message -sent_by-> device
    device -involved_in-> function       (经功能表的报文/信号/故障键)
    function -covers_requirement-> requirement
    fault -detected_by_module-> module   (module 作为 value 节点：detect 文本提取)
    scenario -injects_fault-> fault
    scenario -runs_on-> device/node

查询：neighbors(node) / shortest_path / subgraph(seed, depth)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import AssetModel

# 节点类型全集
NODE_TYPES = {
    "signal",
    "message",
    "device",
    "function",
    "requirement",
    "fault",
    "scenario",
    "system",  # Q2 语义层：列车系统分类框架（S1000D 思想对齐）
    "symptom",  # 症状/无码故障资产（A/B 步：symptoms.yaml 注入；诊断起点）
}

# 因果/诊断关系（B 步：多跳诊断只走这两类有向边；basis 标注依据类型）
CAUSAL_RELS = ("indicates", "causes")

# 关系类型（展示标签）
EDGE_LABELS = {
    ("signal", "message"): "in",
    ("message", "device"): "sent_by",
    ("message", "function"): "used_in",
    ("signal", "function"): "used_in",
    ("function", "requirement"): "covers",
    ("fault", "function"): "triggers",
    ("scenario", "fault"): "injects",
    ("scenario", "device"): "runs_on",
    ("fault", "device"): "affects",
}


@dataclass(frozen=True)
class Node:
    kind: str  # NODE_TYPES
    id: str  # 规范 id（如 signal:Door1State / req:SR-01）
    label: str  # 展示名
    props: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str  # node id
    dst: str  # node id
    kind: str  # EDGE_LABELS value
    # 因果边依据（B 步）：basis ∈ real_mechanism / derived；note 供审计溯源
    basis: str = ""
    note: str = ""


def node_id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


class KnowledgeGraph:
    """内存图：邻接表 + 节点/边集合（P2 阶段足够；后续可换图库）。"""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._adj: dict[str, list[str]] = {}

    def add_node(self, kind: str, key: str, label: str, props: dict | None = None) -> str:
        nid = node_id(kind, key)
        if nid not in self.nodes:
            self.nodes[nid] = Node(kind=kind, id=nid, label=label, props=props or {})
            self._adj.setdefault(nid, [])
        return nid

    def add_edge(self, src_kind: str, src_key: str, dst_kind: str, dst_key: str, kind: str) -> None:
        src = node_id(src_kind, src_key)
        dst = node_id(dst_kind, dst_key)
        if src not in self.nodes:
            raise KeyError(f"边起点不存在: {src}")
        if dst not in self.nodes:
            raise KeyError(f"边终点不存在: {dst}")
        if dst not in self._adj[src]:
            self._adj[src].append(dst)
        if src not in self._adj[dst]:
            self._adj[dst].append(src)
        self.edges.append(Edge(src=src, dst=dst, kind=kind))

    def add_edge_raw(
        self,
        src_id: str,
        dst_id: str,
        kind: str,
        basis: str = "",
        note: str = "",
    ) -> None:
        """按完整 node id 加边（供领域注入用，两端必须已存在）。

        因果边（B 步）：kind ∈ indicates/causes 时可带 basis（real_mechanism/
        derived）与 note（审计溯源）。同 (src,dst,kind) 不重复添加。
        """
        if src_id not in self.nodes:
            return
        if dst_id not in self.nodes:
            return
        for e in self.edges:
            if e.src == src_id and e.dst == dst_id and e.kind == kind:
                return
        if dst_id not in self._adj[src_id]:
            self._adj[src_id].append(dst_id)
        if src_id not in self._adj[dst_id]:
            self._adj[dst_id].append(src_id)
        self.edges.append(Edge(src=src_id, dst=dst_id, kind=kind, basis=basis, note=note))

    # ---- 查询 ----

    def neighbors(self, nid: str) -> list[tuple[str, str]]:
        """返回 [(邻居 node_id, 边 kind)]，双向。"""
        out = []
        for e in self.edges:
            if e.src == nid:
                out.append((e.dst, e.kind))
            elif e.dst == nid:
                out.append((e.src, e.kind))
        return out

    def subgraph(self, seed_id: str, depth: int = 2) -> dict:
        """以 seed 为中心提取子图（BFS），用于可视化/检索上下文。"""
        visited: set[str] = set()
        frontier = [seed_id]
        for _ in range(depth):
            nxt = []
            for nid in frontier:
                if nid in visited:
                    continue
                visited.add(nid)
                for nb, _kind in self.neighbors(nid):
                    if nb not in visited and nb not in nxt:
                        nxt.append(nb)
            frontier = nxt
        # 子图边（两端都在 visited 内）
        sub_edges = []
        seen = set()
        for e in self.edges:
            if e.src in visited and e.dst in visited:
                key = (e.src, e.dst, e.kind)
                if key not in seen:
                    seen.add(key)
                    item = {"src": e.src, "dst": e.dst, "kind": e.kind}
                    if e.basis:
                        item["basis"] = e.basis
                    if e.note:
                        item["note"] = e.note
                    sub_edges.append(item)
        return {
            "seed": seed_id,
            "depth": depth,
            "nodes": [
                {"id": n.id, "kind": n.kind, "label": n.label}
                for n in self.nodes.values()
                if n.id in visited
            ],
            "edges": sub_edges,
            "node_count": len(visited),
        }

    def stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for n in self.nodes.values():
            by_kind[n.kind] = by_kind.get(n.kind, 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges), "by_kind": by_kind}

    # ---- 出处链（P2-1）：节点 → 资产出处 "file:key"，证据可机器追溯 ----

    @staticmethod
    def node_asset_ref(node_id: str) -> str:
        """图节点 id → 权威出处（诊断 evidence/检索证据逐链可点到资产）。

        约定单一冒号：`file:key`；场景资产为目录下文件 `scenarios/<file>.yaml`。
        文件名为真实资产：上游引擎 tcms/faults.yaml、tcms/tcms.dbc、scenarios/*.yaml；
        平台领域 domain/data/symptoms.yaml、domain_systems.json；需求/功能等非单文件
        资产用可追溯语义标注（rtm / engine-functions …），不伪造文件名。
        """
        if ":" not in node_id:
            return node_id
        kind, key = node_id.split(":", 1)
        if kind == "scenario":
            return f"scenarios/{key}"
        _FILE = {
            "symptom": "symptoms.yaml",
            "fault": "faults.yaml",
            "system": "domain_systems.json",
            "message": "tcms.dbc",
            "signal": "tcms.dbc",
            "req": "rtm",
            "requirement": "rtm",
            "function": "engine-functions",
            "hazard": "engine-hazards",
            "concept": "engine-concepts",
            "threshold": "engine-thresholds",
            "interlock": "engine-interlocks",
            "mechanism": "engine-mechanisms",
            "run": "runtime",
        }
        file = _FILE.get(kind)
        return f"{file}:{key}" if file else node_id

    # ---- 因果/症状诊断遍历（B/C 步：深度优先候选链） ----

    def causal_hops(self, nid: str) -> list[dict]:
        """节点的一次因果步进：返回下一步候选 [{to, rel, basis, note, via}]。

        语义（B 步，方向即“解释方向”）：
        - symptom 节点   → 沿 indicates 边外扩（症状指向怀疑故障/系统）；
        - fault 节点     → 沿 causes 边反向（A -causes-> B ⇒ A 是 B 的疑似原因，
                          从被解释的 B 出发找 A，实现“症状→嫌疑→根因”多跳）；
        - 其它节点       → 不再外扩（叶子）。
        """
        node = self.nodes.get(nid)
        if node is None:
            return []
        out: list[dict] = []
        if node.kind == "symptom":
            for e in self.edges:
                if e.src == nid and e.kind == "indicates":
                    out.append(
                        {"to": e.dst, "rel": "indicates", "basis": e.basis, "note": e.note, "via": e.kind}
                    )
        elif node.kind in ("fault", "system"):
            for e in self.edges:
                if e.dst == nid and e.kind == "causes":
                    out.append(
                        {"to": e.src, "rel": "caused_by", "basis": e.basis, "note": e.note, "via": e.kind}
                    )
        return out

    def causal_chain(self, seed_id: str, depth: int = 3, max_chains: int = 24) -> dict:
        """从症状（或故障）节点出发做有向多跳因果遍历。

        返回 {seed, depth, chains, node_count}；每条 chain 为路径记录：
            [{from, to, kind, rel, basis, note}]   （hops[0] 起点 = seed）
        - 从 symptom 沿 indicates 到 fault/system（嫌疑），再从 fault 沿
          causes 反向找疑似根因（深度 ≤ depth）；
        - 只走 CAUSAL_RELS（indicates/causes）有向边，其它关系不参与诊断链；
        - 节点不重复（防环），不同路径共享前缀时仍分别保留（便于审计）。
        """
        seed_node = self.nodes.get(seed_id)
        if seed_node is None:
            return {"seed": seed_id, "depth": depth, "chains": [], "node_count": 0}
        chains: list[list[dict]] = []
        visited_nodes: set[str] = {seed_id}

        def _walk(node_id: str, hops: list[dict]) -> None:
            # 叶子判定：症状下一步必须是指示边；fault 下一步必须是 causes 反查；
            # 均无候选或到达深度上限 → 结束本条链
            if len(hops) >= depth:
                return
            nxts = self.causal_hops(node_id)
            if not nxts:
                return
            for nx in nxts:
                to_id = nx["to"]
                if to_id in visited_nodes:
                    continue
                hop = {
                    "from": node_id,
                    "to": to_id,
                    "kind": nx["via"],
                    "rel": nx["rel"],
                    "basis": nx.get("basis", ""),
                    "note": nx.get("note", ""),
                }
                new_hops = hops + [hop]
                chains.append(new_hops)
                if len(chains) >= max_chains:
                    return
                visited_nodes.add(to_id)
                _walk(to_id, new_hops)
                visited_nodes.discard(to_id)
                if len(chains) >= max_chains:
                    return

        _walk(seed_id, [])
        visited = {seed_id}
        for c in chains:
            for h in c:
                visited.add(h["from"])
                visited.add(h["to"])
        return {
            "seed": seed_id,
            "depth": depth,
            "chains": chains,
            "node_count": len(visited),
        }

    def shortest_path(
        self,
        src_id: str,
        dst_id: str,
        max_depth: int = 8,
        kinds: frozenset[str] | None = None,
    ) -> list[dict] | None:
        """两节点间最短路（BFS，边数最少；可选只走指定 kind）。

        返回逐边记录 {src, dst, kind, basis, note}（证据可溯源）；无路径/超深 → None。
        用于“证据图可查询”：症状 → 故障 → 根因、故障 → 系统/需求等任意可达性。
        """
        if src_id not in self.nodes or dst_id not in self.nodes:
            return None
        prev: dict[str, tuple[str, Edge]] = {}
        visited: set[str] = {src_id}
        frontier = [src_id]
        depth = 0
        found = False
        while frontier and depth < max_depth:
            nxt: list[str] = []
            for nid in frontier:
                if nid == dst_id:
                    found = True
                    break
                for e in self.edges:
                    other = None
                    if e.src == nid:
                        other = e.dst
                    elif e.dst == nid:
                        other = e.src
                    if other is None or other in visited:
                        continue
                    if kinds is not None and e.kind not in kinds:
                        continue
                    visited.add(other)
                    prev[other] = (nid, e)
                    nxt.append(other)
                if found:
                    break
            if found:
                break
            frontier = nxt
            depth += 1
        if not found:
            return None
        # 回溯还原路径（按实际行走方向定向端点：parent → cur；边依据原样保留）
        path: list[dict] = []
        cur = dst_id
        while cur != src_id:
            parent, edge = prev[cur]
            path.append(
                {
                    "src": parent,
                    "dst": cur,
                    "kind": edge.kind,
                    "basis": edge.basis,
                    "note": edge.note,
                }
            )
            cur = parent
        path.reverse()
        return path


# ---------------------------------------------------------------------------
# 构建器：从 AssetModel 派生
# ---------------------------------------------------------------------------


def build_knowledge_graph(m: AssetModel) -> KnowledgeGraph:
    """从 L1 AssetModel 构建完整图谱（全部关系机器派生）。"""
    g = KnowledgeGraph()

    # 设备
    for dev in m.devices.values():
        g.add_node("device", dev.name, dev.name, {"role": dev.role})

    # 报文 / 信号
    for msg in m.messages.values():
        g.add_node("message", msg.name, msg.name, {"cycle_ms": msg.cycle_ms})
        if msg.node in m.devices:
            g.add_edge("message", msg.name, "device", msg.node, "sent_by")
    for sig in m.signals.values():
        g.add_node("signal", sig.name, sig.name, {"unit": sig.unit})
        if sig.message in m.messages:
            g.add_edge("signal", sig.name, "message", sig.message, "in")

    # 故障
    for f in m.faults_by_key.values():
        g.add_node(
            "fault",
            f.key,
            f"{f.name} ({f.fid})",
            {"level": f.level, "action": f.action, "sil": f.sil, "subsystem": f.subsystem},
        )

    # 场景
    for s in m.scenarios.values():
        g.add_node("scenario", s.file, s.name, {"steps": len(s.steps)})
        for fk in s.fault_keys:
            if fk in m.faults_by_key:
                g.add_edge("scenario", s.file, "fault", fk, "injects")
        for n in s.nodes:
            dev = n.upper() if n != "bus" else "TCMS"
            if dev in m.devices:
                g.add_edge("scenario", s.file, "device", dev, "runs_on")

    # 需求
    for req_id, reqs in m.requirements.items():
        g.add_node(
            "requirement",
            req_id,
            req_id,
            {"verifies": " | ".join(r.verifies for r in reqs)},
        )

    # 被测功能（聚合层，锚定真实）
    for fn in m.functions.values():
        g.add_node("function", fn.fid, fn.name, {"description": fn.description})
        for msg in fn.messages:
            if msg in m.messages:
                g.add_edge("function", fn.fid, "message", msg, "used_in")
        for sig in fn.signals:
            if sig in m.signals:
                g.add_edge("function", fn.fid, "signal", sig, "used_in")
        for fk in fn.fault_keys:
            if fk in m.faults_by_key:
                g.add_edge("fault", fk, "function", fn.fid, "triggers")
        for req in fn.requirements:
            if req in m.requirements:
                g.add_edge("function", fn.fid, "requirement", req, "covers")

    # 故障 → 设备（经子系统近邻）
    _SUB_DEV = {
        "VCU": "VCU",
        "车门": "BOGIE",
        "能源": "BMS",
        "牵引": "VCU",
        "受电弓": "BCU",
        "制动": "BCU",
        "网络": "TCMS",
    }
    for f in m.faults_by_key.values():
        dev = _SUB_DEV.get(f.subsystem)
        if dev and dev in m.devices:
            g.add_edge("fault", f.key, "device", dev, "affects")

    return g
