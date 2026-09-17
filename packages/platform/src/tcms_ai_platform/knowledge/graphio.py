"""图谱可移植导出（P1-2：证据图“可查询/可迁移”的第一步）。

当前图谱是内存对象（离线、测试友好）；当规模/查询复杂度上升时可迁移到图引擎
（Kuzu/Neo4j/pyoxigraph）。本模块给出**无损可移植导出**：
- JSON 全量导出：节点(props) + 边(kind + basis/note 依据字段 + props)，外加统计；
- 迁入外部图库时按此结构建 schema（节点 kind 属性 + 有向边 label/basis/note），
  即“一张可审计证据表”的机器视图，供导入/对拍/审计脚本直接消费。

诚实口径：导出内容完全派生自内存图（graph.stats() 与导出节点/边计数必须一致，
测试断言锁定），不额外“美化”任何关系。
"""

from __future__ import annotations

from .graph import KnowledgeGraph


def export_graph_json(g: KnowledgeGraph) -> dict:
    """全量导出（含节点属性与边依据）。计数 = graph.stats() 派生（机器自证）。"""
    nodes = [
        {"id": n.id, "kind": n.kind, "label": n.label, "props": dict(n.props)}
        for n in sorted(g.nodes.values(), key=lambda x: x.id)
    ]
    edges = []
    seen: set[tuple[str, str, str]] = set()
    for e in sorted(g.edges, key=lambda x: (x.src, x.dst, x.kind)):
        key = (e.src, e.dst, e.kind)
        if key in seen:
            continue
        seen.add(key)
        item = {"src": e.src, "dst": e.dst, "kind": e.kind}
        if e.basis:
            item["basis"] = e.basis
        if e.note:
            item["note"] = e.note
        edges.append(item)
    stats = g.stats()
    return {
        "schema_version": 1,
        "generator": "tcms_ai_platform.knowledge.graphio.export_graph_json",
        "stats": stats,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }
