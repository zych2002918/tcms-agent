"""知识底座上下文：把 platform 的资产模型 / 图谱 / 检索器组装成 Agent 的"世界"。

为什么不让 agent 直接 import platform 的 server：agent 只用**库**，不依赖 Web 层。
这样 agent 可以脱离 FastAPI 单独跑（CLI / 评测 / CI），也让分层保持单向。

上游目录解析复用 platform 的 `resolve_asset_source()`（四级链：用户设置 → 环境变量
→ monorepo 同仓成员 → 内置快照）。**不自己另写一套路径猜测**——原三仓里
`mcp_server.py` 因为硬编码兄弟目录而在 pip 安装场景下直接崩，是前车之鉴。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class KnowledgeContext:
    """Agent 可访问的真实知识底座（全部来自上游引擎资产）。"""

    upstream: Path | None
    model: Any  # tcms_ai_platform.core.models.AssetModel
    graph: Any  # tcms_ai_platform.knowledge.graph.KnowledgeGraph
    retriever: Any  # tcms_ai_platform.knowledge.retriever.HybridRetriever
    asset_mode: str = "unknown"

    def stats(self) -> dict:
        """知识底座规模（自证用：所有数字都从真实对象数出来，禁止手抄）。"""
        return {
            "asset_mode": self.asset_mode,
            "upstream": str(self.upstream) if self.upstream else None,
            "faults": len(self.model.faults_by_key),
            "scenarios": len(self.model.scenarios),
            "graph_nodes": len(self.graph.nodes),
            "docs": len(getattr(self.retriever.store, "docs", []) or []),
        }


def build_knowledge(upstream: Path | None = None) -> KnowledgeContext:
    """构建知识底座上下文（资产模型 + 图谱 + 混合检索）。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.core.sources import ensure_engine_importable, resolve_asset_source
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    if upstream is None:
        src = resolve_asset_source()
        root = src.root
        mode = src.mode
        ensure_engine_importable(src)  # 让 `import tcms` 生效（场景执行需要）
    else:
        root = Path(upstream)
        mode = "explicit"

    model = load_asset_model(root)
    graph = build_knowledge_graph(model)
    store = VectorStore()
    store.add_many(build_docs_from_asset(model))
    enrich_graph(graph, store)  # 领域知识注入（13 系统域 / 症状 / 因果边）
    retriever = HybridRetriever(store, graph)
    return KnowledgeContext(
        upstream=root, model=model, graph=graph, retriever=retriever, asset_mode=mode
    )


__all__ = ["KnowledgeContext", "build_knowledge"]
