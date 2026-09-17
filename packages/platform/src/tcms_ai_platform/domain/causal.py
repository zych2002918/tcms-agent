"""症状资产 + 可审计因果表（A/B 步：症状多跳诊断的数据层）。

数据文件（随包分发，单一真源）：
    data/symptoms.yaml    症状资产（12 条，hints 引用上游真实故障键）
    data/causal_edges.yaml 因果表（symptom -indicates-> fault|system；
                           fault -causes-> fault；每条带 basis/note）

本模块职责：
1. 装载（load_*）—— 任何调用方可直接读（含测试/校验脚本）；
2. 校验（validate_symptom_assets）—— 无孤儿/漂移纪律的程序化强制：
   - symptom 键不与故障键冲突、hints 2-4 个且全部存在于真实故障字典或 13 系统域；
   - 因果表方向合法（indicates 仅 symptom→fault|system；causes 仅 fault→fault）；
   - basis ∈ real_mechanism/derived；每条 symptom 的 indicates 目标 == hints
     （防 symptoms.yaml 与 causal_edges.yaml 双源漂移）；
   - 返回统计（12 症状 / indicates N / causes M / real R / derived D —— 测试与
     文档数量以该锁定值断言）；
3. 注入（inject_symptoms_causal）—— enrich_graph 调用：symptom 节点 + 向量文档
   + 带依据因果边进图。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..knowledge.graph import KnowledgeGraph
from ..knowledge.vector import DOMAIN_ZH, Doc, VectorStore

# 数据目录（domain/data/，与 domain_*.json 同随包分发）
DATA_DIR = Path(__file__).resolve().parent / "data"
SYMPTOM_CATALOG = DATA_DIR / "symptoms.yaml"
CAUSAL_TABLE = DATA_DIR / "causal_edges.yaml"

_VALID_RELS = {"indicates", "causes"}
_VALID_BASIS = {"real_mechanism", "derived"}


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_symptom_catalog() -> dict:
    """读症状资产（缺文件返回空 dict —— 与 domain_*.json 纪律一致）。"""
    return _load_yaml(SYMPTOM_CATALOG)


def load_causal_table() -> dict:
    """读可审计因果表。"""
    return _load_yaml(CAUSAL_TABLE)


def symptoms() -> list[dict]:
    return list(load_symptom_catalog().get("symptoms", []))


def causal_edges() -> list[dict]:
    return list(load_causal_table().get("edges", []))


# ---------------------------------------------------------------------------
# 校验（无孤儿/漂移纪律，测试强制）
# ---------------------------------------------------------------------------


def validate_symptom_assets(m) -> dict:
    """校验症状资产 + 因果表与真实资产的一致性。

    参数 m：AssetModel（真实故障字典/系统域来源）。
    违规 → 抛 ValueError（逐条列出）；通过 → 返回数量统计 dict（机器自证）。
    """
    errs: list[str] = []
    catalog = load_symptom_catalog()
    table = load_causal_table()

    syms = list(catalog.get("symptoms", []))
    if not syms:
        errs.append("症状资产为空（symptoms.yaml 缺 symptoms 段）")

    fault_keys = set(m.faults_by_key)
    sys_codes = set(m.systems) if hasattr(m, "systems") else set()
    # 13 系统域代码（domain_systems.json 单一真源；兜底已知集）
    if not sys_codes:
        sys_codes = {
            "SYS-" + c
            for c in (
                "TRAIN", "BRAKE", "TRACTION", "DOOR", "PANTO", "BATT",
                "AUX", "HVAC", "PIS", "LIGHT", "FIRE", "BOGIE", "SENSING",
            )
        }

    # —— symptoms.yaml 本体 ——
    seen_keys: set[str] = set()
    for s in syms:
        key = s.get("key", "")
        if not key:
            errs.append("症状条目缺 key")
            continue
        if key in seen_keys:
            errs.append(f"症状 key 重复: {key}")
        seen_keys.add(key)
        if key in fault_keys:
            errs.append(f"症状 key 与故障键冲突: {key}")
        if not s.get("name"):
            errs.append(f"{key}: 缺中文名 name")
        hints = s.get("hints") or []
        if not (2 <= len(hints) <= 4):
            errs.append(f"{key}: hints 应 2-4 个候选（当前 {len(hints)}）")
        for h in hints:
            if h.startswith("system:"):
                if h not in {f"system:{c}" for c in sys_codes}:
                    errs.append(f"{key}: 未知系统域候选 {h}")
            elif h not in fault_keys:
                errs.append(f"{key}: 候选故障键 {h} 不在故障字典（{len(fault_keys)} 条）")
        domains = s.get("domains") or []
        if not domains:
            errs.append(f"{key}: 缺涉及域 domains")
        for d in domains:
            if d not in DOMAIN_ZH or d == "":
                errs.append(f"{key}: 涉及域 {d} 不在 13 域词汇表")
        if s.get("annotation") not in ("real", "mixed", "derived"):
            errs.append(f"{key}: annotation 应为 real/mixed/derived")

    # —— causal_edges.yaml ——
    by_sym_indicates: dict[str, set[str]] = {}
    e_real = e_derived = e_indicates = e_causes = 0
    for e in table.get("edges", []):
        src, rel, dst = e.get("src", ""), e.get("rel", ""), e.get("dst", "")
        basis = e.get("basis", "")
        if rel not in _VALID_RELS:
            errs.append(f"因果边未知 rel: {rel}（{src}→{dst}）")
        if basis not in _VALID_BASIS:
            errs.append(f"因果边 basis 非法: {basis}（{src}→{dst}）应 ∈ real_mechanism/derived")
        if not src.startswith(("symptom:", "fault:", "system:")):
            errs.append(f"因果边起点非法: {src}")
        if rel == "indicates":
            e_indicates += 1
            if not src.startswith("symptom:"):
                errs.append(f"indicates 起点应为 symptom: {src}→{dst}")
            if not dst.startswith(("fault:", "system:")):
                errs.append(f"indicates 终点应为 fault/system: {src}→{dst}")
            skey = src.split(":", 1)[1] if src.startswith("symptom:") else ""
            by_sym_indicates.setdefault(skey, set()).add(dst)
            if dst.startswith("fault:") and dst.split(":", 1)[1] not in fault_keys:
                errs.append(f"indicates 终点故障键不存在: {dst}")
            if dst.startswith("system:") and dst.split(":", 1)[1] not in sys_codes:
                errs.append(f"indicates 终点系统不存在: {dst}")
        elif rel == "causes":
            e_causes += 1
            if not (src.startswith("fault:") and dst.startswith("fault:")):
                errs.append(f"causes 应 fault→fault: {src}→{dst}")
            if src.split(":", 1)[1] not in fault_keys:
                errs.append(f"causes 起点故障键不存在: {src}")
            if dst.split(":", 1)[1] not in fault_keys:
                errs.append(f"causes 终点故障键不存在: {dst}")
        if basis == "real_mechanism":
            e_real += 1
        elif basis == "derived":
            e_derived += 1
    # —— 双源一致：每个症状的 indicates 目标 == hints ——
    for s in syms:
        key = s.get("key", "")
        want = set(s.get("hints") or [])
        # symptoms.yaml 里 hints 写故障键或 "system:SYS-*" → 完整 id 归一
        got = by_sym_indicates.get(key, set())
        want_ids = {h if h.startswith("system:") else f"fault:{h}" for h in want}
        if want_ids != got:
            errs.append(
                f"{key}: hints 与因果表漂移 — symptoms 缺 {sorted(want_ids - got)}; "
                f"表多 {sorted(got - want_ids)}"
            )

    if errs:
        raise ValueError("症状/因果资产校验失败：\n  - " + "\n  - ".join(errs[:40]))

    # —— annotation 与 indicates 依据一致性（real=全真实机制；mixed=含 derived；derived=全示意） ——
    for s in syms:
        key = s.get("key", "")
        edges_here = [
            e
            for e in table.get("edges", [])
            if e.get("src") == f"symptom:{key}" and e.get("rel") == "indicates"
        ]
        bases = {e.get("basis") for e in edges_here}
        has_real = "real_mechanism" in bases
        has_derived = "derived" in bases
        expect = "derived" if (bases and not has_real) else ("mixed" if has_derived else "real")
        if s.get("annotation") != expect:
            errs.append(
                f"{key}: annotation={s.get('annotation')} 与因果依据不符（应为 {expect}）"
            )
    if errs:
        raise ValueError("症状/因果资产校验失败：\n  - " + "\n  - ".join(errs[:40]))

    n_mixed = sum(1 for s in syms if s.get("annotation") == "mixed")
    n_derived_sym = sum(1 for s in syms if s.get("annotation") == "derived")
    # 统计（机器自证口径：测试/文档据此锁定）
    real_syms = sum(1 for s in syms if s.get("annotation") == "real")
    return {
        "symptoms": len(syms),
        "real_symptoms": real_syms,
        "mixed_annotation_symptoms": n_mixed,
        "derived_annotation_symptoms": n_derived_sym,
        "indicates": e_indicates,
        "causes": e_causes,
        "total_edges": e_indicates + e_causes,
        "real_mechanism_edges": e_real,
        "derived_edges": e_derived,
    }


# ---------------------------------------------------------------------------
# 注入（enrich_graph 调用：节点 + 向量文档 + 带依据因果边）
# ---------------------------------------------------------------------------


def _label_base(label: str) -> str:
    """fault 节点 label 形如 "故障名 (F-TCMS-xxx)" → 取主体名（向量文档更干净）。"""
    if label and " (" in label:
        return label.split(" (", 1)[0]
    return label or ""


def inject_symptoms_causal(
    g: KnowledgeGraph,
    store: VectorStore | None = None,
    catalog: dict | None = None,
    table: dict | None = None,
) -> dict:
    """把症状资产与因果表注入图谱/向量（enrich_graph 调用）。

    幂等：同 symptom 节点已存在时跳过（重复 enrich 不翻倍计数）。
    返回注入统计 {symptoms, docs, indicates, causes, real_mechanism, derived}。
    """
    catalog = catalog if catalog is not None else load_symptom_catalog()
    table = table if table is not None else load_causal_table()
    syms = list(catalog.get("symptoms", []))
    edges = list(table.get("edges", []))
    stats = {
        "symptoms": 0,
        "docs": 0,
        "indicates": 0,
        "causes": 0,
        "real_mechanism": 0,
        "derived": 0,
        "total_edges": 0,
    }
    if not syms:
        return stats

    # 节点 + 文档
    for s in syms:
        key = s["key"]
        nid = g.add_node(
            "symptom",
            key,
            s.get("name", key),
            {
                "name": s.get("name", key),
                "description": s.get("description", ""),
                "domains": s.get("domains", []),
                "hints": s.get("hints", []),
                "annotation": s.get("annotation", ""),
                "evidence": s.get("evidence", ""),
            },
        )
        stats["symptoms"] += 1
        if store is not None:
            # 疑似候选中文名（真实故障/系统节点 label 取主体）
            cand_names: list[str] = []
            for h in s.get("hints", []):
                hid = h if h.startswith(("system:", "fault:")) else f"fault:{h}"
                nd = g.nodes.get(hid)
                if nd:
                    cand_names.append(_label_base(nd.label))
            doms = s.get("domains") or []
            zh = "、".join(DOMAIN_ZH.get(d, d) for d in doms)
            cand_txt = "、".join(cand_names) if cand_names else "、".join(s.get("hints", []))
            store.add(
                Doc(
                    doc_id=nid,
                    kind="symptom",
                    text=(
                        f"症状 {s.get('name', key)}。{s.get('description', '')} "
                        f"涉及域：{zh}。疑似候选故障：{cand_txt}。"
                        f"诚实标注：{s.get('annotation', '')}。"
                    ),
                    meta={
                        "key": key,
                        "name": s.get("name", key),
                        "domain": doms[0] if doms and doms[0] in DOMAIN_ZH and doms[0] else "",
                        "annotation": s.get("annotation", ""),
                    },
                )
            )
            stats["docs"] += 1

    # 因果边（带依据）
    for e in edges:
        src, rel, dst = e.get("src", ""), e.get("rel", ""), e.get("dst", "")
        basis = e.get("basis", "")
        if src not in g.nodes or dst not in g.nodes:
            continue  # 端点缺失不注入（validate 已保证；幂等保守）
        g.add_edge_raw(src, dst, rel, basis=basis, note=e.get("note", ""))
        if rel == "indicates":
            stats["indicates"] += 1
        elif rel == "causes":
            stats["causes"] += 1
        stats["total_edges"] += 1
        if basis == "real_mechanism":
            stats["real_mechanism"] += 1
        elif basis == "derived":
            stats["derived"] += 1
    return stats
