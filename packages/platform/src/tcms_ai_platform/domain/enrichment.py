"""领域知识注入：把挖掘出的真实领域知识(源码/标准/概念)注入知识图谱。

数据源（domain/data/domain_*.json，随包分发；由子代理从 tcms-can-test
真实源码与标准文档提取，数值逐字来自源码）：
    domain_ebm.json      — EBM 驾驶模式矩阵/状态机/联锁/表决/旁路/ATP
    domain_network.json  — 网络/总线/NMT/错误状态机/看门狗
    domain_safety.json   — 安全需求/SIL/危害/概念卡/标准

注入后图谱新增节点类型（贴近真实列车，资产文件表达不了的领域知识）：
    mode / state / interlock / threshold / mechanism / standard / hazard / concept

字段约定（对齐 domain_*.json 实际结构，见各 inject 函数）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..knowledge.graph import KnowledgeGraph
from ..knowledge.vector import Doc, VectorStore

# 领域知识 JSON 随包分发(domain/data/)，dev 与 wheel 安装一致可用
DOMAIN_DATA_DIR = Path(__file__).resolve().parent / "data"


def load_domain_json(name: str) -> dict:
    """读 domain/data/domain_*.json（随包分发），缺文件返回空 dict。"""
    p = DOMAIN_DATA_DIR / name
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _add_knowledge(
    g: KnowledgeGraph,
    store: VectorStore | None,
    kind: str,
    stable_id: str,
    label: str,
    props: dict,
    links: list[tuple[str, str]],  # (rel, target_full_node_id)
    doc_text: str | None = None,
) -> None:
    """加一个领域知识节点（含向量文档与到既有实体的边）。"""
    nid = f"{kind}:{stable_id}"
    g.add_node(kind, stable_id, label, props)
    for rel, target_id in links:
        g.add_edge_raw(nid, target_id, rel)
    if store is not None and doc_text:
        store.add(Doc(doc_id=nid, kind=kind, text=doc_text, meta={"label": label}))


def _zh(entry: dict, *keys: str) -> str:
    """按顺序取第一个存在的字段值（兼容 enum/zh 与 code/name 等别名）。"""
    for k in keys:
        if k in entry and entry[k] is not None:
            return entry[k]
    return ""


# ---------------------------------------------------------------------------
# EBM / ATP / 联锁 / 表决 / 旁路
# ---------------------------------------------------------------------------


def inject_ebm(g: KnowledgeGraph, store: VectorStore | None, data: dict) -> int:
    count = 0
    ebm = data.get("ebm", {})
    F_EBM = "function:F-EBM"
    F_ATP = "function:F-ATP"

    # 驾驶模式
    for mode in ebm.get("modes", []):
        code = _zh(mode, "enum", "code")
        zh = _zh(mode, "zh", "name", "desc")
        if not code:
            continue
        _add_knowledge(
            g, store, "mode", code, f"驾驶模式 {code}",
            {"code": code, "zh": zh},
            [("applies_in", F_EBM)],
            f"驾驶模式 {code}（{zh}）。EBM 按『模式×原因』矩阵决定是否制动。",
        )
        count += 1

    # 降级链
    chain = ebm.get("degradation_chain", [])
    if chain:
        _add_knowledge(
            g, store, "mechanism", "degradation-chain", "驾驶模式降级链",
            {"chain": chain, "desc": "故障按 FAM→CM→RM 逐级降级（禁止跳级），恢复逐级升回"},
            [("applies_in", F_EBM)],
            "驾驶模式降级链 " + "→".join(chain) + "：故障强制降级、人工逐级恢复。",
        )
        count += 1

    # EBM 状态机状态
    for st in ebm.get("state_machine", {}).get("states", []):
        code = _zh(st, "enum", "id", "code")
        zh = _zh(st, "zh", "desc")
        if not code:
            continue
        _add_knowledge(
            g, store, "state", f"ebm-{code}", f"EBM 状态 {code}",
            {"code": code, "zh": zh, "machine": "ebm"},
            [("part_of", F_EBM)],
            f"紧急制动管理状态 {code}：{zh}",
        )
        count += 1

    # 触发原因矩阵
    for r in ebm.get("reasons_matrix", []):
        reason = _zh(r, "reason", "id")
        if not reason:
            continue
        desc = _zh(r, "description", "desc") or reason
        sil = r.get("sil", "")
        modes = r.get("applies_modes", [])
        action = r.get("action", "")
        _add_knowledge(
            g, store, "mechanism", f"ebm-reason-{reason}", f"EBM 触发 {reason}",
            {"reason": reason, "applies_modes": modes, "action": action, "sil": sil, "desc": desc},
            [("triggers", F_EBM)],
            f"EBM 触发原因 {reason}（SIL{sil}，适用模式 {modes}）：{desc} → 处置 {action}",
        )
        count += 1

    # 关键常量 → threshold
    consts = ebm.get("key_constants", {})
    kc_map = [
        ("zero-speed", "零速判定阈值", consts.get("zero_speed_threshold_kmh"), "km/h", "缓解/旁路前提"),
        ("self-heal-max", "EBM 自愈复位上限", consts.get("MAX_SELF_HEAL"), "次", "超限转 FAULT 需人工/远程复位"),
        ("release-hold", "司机缓解按钮保持", consts.get("RELEASE_HOLD_S"), "s", "两步缓解序列第二步"),
    ]
    for tid, tname, val, unit, note in kc_map:
        if val is None:
            continue
        _add_knowledge(
            g, store, "threshold", f"ebm-{tid}", f"{tname} = {val} {unit}",
            {"value": val, "unit": unit, "note": note},
            [("governs", F_EBM)],
            f"关键阈值 {tname} = {val} {unit}（{note}）",
        )
        count += 1

    # 联锁规则（含 atp 超速阈值 160 由 protocol 常量支撑）
    for il in data.get("interlocks", []):
        iid = il.get("id", f"ilk-{count}")
        links = [("constrains", F_EBM)]
        name = il.get("name", iid)
        rule = il.get("rule", "")
        cond = il.get("condition", "")
        cons = il.get("consequence", "")
        # 显式 fault 关联（domain_ebm.json 每条 interlock 的 fault_keys——按引擎函数
        # 语义 + 场景 expect 声明，杜绝"关键词泛匹配"造成的误连/漏连，如 door_open_moving）
        for fk in il.get("fault_keys", []) or []:
            if fk and f"fault:{fk}" in g.nodes:
                links.append(("enforces", f"fault:{fk}"))
        _add_knowledge(
            g, store, "interlock", iid, name,
            {"rule": rule, "condition": cond, "consequence": cons},
            links,
            f"联锁规则 {name}：{rule}。条件：{cond}。后果：{cons}",
        )
        count += 1

    # ATP 监督层级
    for lvl in data.get("atp", {}).get("supervision_levels", []):
        code = _zh(lvl, "enum", "id", "level")
        zh = _zh(lvl, "zh", "desc")
        rel = lvl.get("relation", "")
        if not code:
            continue
        _add_knowledge(
            g, store, "state", f"atp-{code}", f"ATP 监督 {code}",
            {"code": code, "zh": zh, "relation": rel, "machine": "atp"},
            [("part_of", F_ATP)],
            f"ATP 超速监督层级 {code}（{zh}）：{rel}",
        )
        count += 1

    # ATP 阈值体系 → threshold
    thr = data.get("atp", {}).get("threshold_constants", {})
    if isinstance(thr, dict):
        for k, v in thr.items():
            if isinstance(v, (int, float)):
                _add_knowledge(
                    g, store, "threshold", f"atp-{k}", f"ATP 阈值 {k} = {v}",
                    {"value": v, "unit": "km/h", "note": "atp.py 源码常量"},
                    [("governs", F_ATP)],
                    f"ATP 速度监督阈值 {k} = {v} km/h",
                )
                count += 1

    # 安全机制（表决/三重证据/旁路）
    voting = data.get("voting", {})
    if voting:
        tol = voting.get("tolerance", {})
        tol_v = tol.get("value") if isinstance(tol, dict) else tol
        _add_knowledge(
            g, store, "mechanism", "voting-2oo3", "速度 2oo3 表决",
            {"desc": voting.get("mechanism", ""), "channels": voting.get("channels", {}), "tolerance": tol_v},
            [("implements", F_ATP)],
            f"安全机制 速度 2oo3 表决：{voting.get('mechanism', '')}（容差 {tol_v}）",
        )
        count += 1
    execf = data.get("exec_feedback", {})
    if execf:
        _add_knowledge(
            g, store, "mechanism", "exec-feedback-3ev", "制动执行三重证据",
            {"desc": execf.get("mechanism", execf.get("module_purpose", ""))},
            [("implements", F_EBM)],
            f"安全机制 制动执行三重证据：{execf.get('module_purpose', '')}",
        )
        count += 1
    bypass = data.get("bypass", {})
    if bypass:
        _add_knowledge(
            g, store, "mechanism", "bypass-degrade", "旁路强制降级",
            {"desc": bypass.get("module_purpose", "")},
            [("implements", F_EBM)],
            f"安全机制 旁路与强制降级：{bypass.get('module_purpose', '')}",
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# 网络/总线/NMT/错误状态机/看门狗
# ---------------------------------------------------------------------------


def inject_network(g: KnowledgeGraph, store: VectorStore | None, data: dict) -> int:
    count = 0
    F_NET = "function:F-NET"
    for mod in data.get("modules", []):
        mname = mod.get("module", "")
        for k in mod.get("knowledge", []):
            kid = k.get("id", f"net-{count}")
            name = k.get("name", kid)
            desc = k.get("desc", "")
            kv = k.get("key_values") or {}
            has_num = any(isinstance(v, (int, float)) for v in kv.values())
            kind = "threshold" if has_num else "mechanism"
            links = [("knowledge_of", F_NET)]
            blob = (name + desc).lower()
            for fk, kw in [("heartbeat_loss_vcu", "心跳"), ("crc_error_frame", "crc"), ("bus_short", "bus-off"), ("node_restart_storm", "节点")]:
                if kw.lower() in blob and f"fault:{fk}" in g.nodes:
                    links.append(("governs", f"fault:{fk}"))
            _add_knowledge(
                g, store, kind, kid, name,
                {"desc": desc, "key_values": kv, "source_module": k.get("source_module", mname)},
                links,
                f"{name}：{desc}" + (f"（关键值: {kv}）" if kv else ""),
            )
            count += 1
    return count


# ---------------------------------------------------------------------------
# 安全需求 / 标准 / 危害 / 概念卡
# ---------------------------------------------------------------------------


def inject_safety(g: KnowledgeGraph, store: VectorStore | None, data: dict) -> int:
    count = 0

    # 标准
    for st in data.get("standards", []):
        code = st.get("code", f"std-{count}")
        _add_knowledge(
            g, store, "standard", code, code,
            {"name": st.get("name", ""), "category": st.get("category", ""), "role": st.get("role", "")},
            [],
            f"标准 {code} {st.get('name','')}（{st.get('category','')}）：{st.get('role','')}",
        )
        count += 1

    # 危害
    for hz in data.get("hazards", []):
        hid = hz.get("id", f"hazard-{count}")
        links = []
        for sr in hz.get("related_sr", []):
            if f"requirement:{sr}" in g.nodes:
                links.append(("mitigates", f"requirement:{sr}"))
        blob = hz.get("title", "") + hz.get("description", "")
        for fn, kw in [("F-EBM", "制动"), ("F-ATP", "超速"), ("F-NET", "网络"), ("F-DOOR", "门")]:
            if kw in blob and f"function:{fn}" in g.nodes:
                links.append(("touches", f"function:{fn}"))
        _add_knowledge(
            g, store, "hazard", hid, hz.get("title", hid),
            {"description": hz.get("description", ""), "sil": hz.get("sil_level", ""), "detection": hz.get("detection", ""), "mitigation": hz.get("mitigation", "")},
            links,
            f"安全危害 {hid}：{hz.get('title','')}（SIL {hz.get('sil_level','')}）。检测：{hz.get('detection','')}。缓解：{hz.get('mitigation','')}",
        )
        count += 1

    # 概念卡
    for cc in data.get("concept_cards", []):
        term = cc.get("term", f"concept-{count}")
        # 稳定 id：取 term 首词(EBM/ATP/SIL/Bus-Off...)字母
        import re as _re

        first = term.split("（")[0].split()[0]
        cid = _re.sub(r"[^A-Za-z0-9]", "", first) or f"concept-{count}"
        links = []
        blob = term
        for fn, kw in [("F-EBM", "EBM"), ("F-ATP", "ATP"), ("F-NET", "Bus-Off"), ("F-NET", "心跳"), ("F-DOOR", "门")]:
            if kw in blob and f"function:{fn}" in g.nodes:
                links.append(("explains", f"function:{fn}"))
        if "EB" in term and "function:F-EBM" in g.nodes:
            links.append(("explains", "function:F-EBM"))
        _add_knowledge(
            g, store, "concept", cid, term,
            {"plain": cc.get("plain_explanation", ""), "why": cc.get("why_it_matters", "")},
            links,
            f"{term}：{cc.get('plain_explanation','')}（为什么重要：{cc.get('why_it_matters','')}）",
        )
        count += 1

    return count


def _link_faults_to_safety(g: KnowledgeGraph) -> int:
    """把故障(真实)连到真实危害/需求/功能（依据危害 related_sr 与标题语义）。

    纯程序化：遍历已注入的 hazard 节点，按其 title/description 关键词 + related_sr
    把相关 fault 关联过去 —— 每条边都可在 hazard 条目中溯源（机器自证）。
    """
    count = 0
    # 危害标题/描述 → 故障键（与 hazards 文本一致的关键词映射）
    _haz_kw: dict[str, list[str]] = {
        "超速": ["overspeed"],
        "车门打开": ["door_fault", "door_sensor_noise"],
        "门状态故障": ["door_fault"],
        "牵引与制动同时施加": ["traction_brake_conflict"],
        "通信网络故障": ["heartbeat_loss_vcu", "node_restart_storm", "bus_short", "bus_open_circuit", "crc_error_frame"],
        "节点失活": ["heartbeat_loss_vcu"],
        "心跳丢失": ["heartbeat_loss_vcu", "node_restart_storm"],
        "制动执行层失效": ["eb_failure"],
        "请求未落地": ["eb_failure"],
        "速度传感器冗余表决失效": ["speed_sensor_drift", "sensor_stuck"],
        "2oo3": ["speed_sensor_drift"],
        "维护开关误置": ["node_restart_storm"],
        "火灾": [],
        "轨道障碍物": [],
        "EBR 硬线回路": ["brake_actuator_stuck"],
        "紧急制动被不当缓解": [],
        "ATP 超速防护故障": ["overspeed"],
        "ATO": [],
    }
    for n in g.nodes.values():
        if n.kind != "hazard":
            continue
        blob = (n.label + " " + str(n.props.get("description", "")) + " " + str(n.props.get("mitigation", "")))
        for kw, fks in _haz_kw.items():
            if kw in blob:
                for fk in fks:
                    fid = f"fault:{fk}"
                    if fid in g.nodes:
                        # 边带语义：fault -exposes-> hazard（该故障是此危害的诱因之一）
                        for e in g.edges:
                            if e.src == fid and e.dst == n.id and e.kind == "exposes":
                                break
                        else:
                            g.add_edge_raw(fid, n.id, "exposes")
                            count += 1
    return count


def inject_systems(g: KnowledgeGraph, store: VectorStore | None, data: dict) -> int:
    """列车系统分类框架（S1000D 思想对齐）：system 节点 + 设备/故障 → 系统 隶属边。

    让图谱/Agent 站到『列车系统视角』：SYS-BRAKE 下能看到该系统的故障与设备，
    检索可先定位系统族再进细节。每条 system 都带 standard_anchor 与 asset_evidence
    （真实故障键），可溯源不编造车型数据。
    """
    count = 0
    device_system = data.get("device_system", {})
    # 设备 → 系统（已有 device 节点时连）
    for dev_name, sys_code in device_system.items():
        dev_id = f"device:{dev_name}"
        sys_id = f"system:{sys_code}"
        if sys_id in g.nodes and dev_id in g.nodes:
            g.add_edge_raw(dev_id, sys_id, "part_of_system")
    # 系统节点 + 向量文档（先建全部 system 节点，再连故障）
    for sys in data.get("systems", []):
        code = sys["code"]
        sys_id = f"system:{code}"
        g.add_node("system", code, sys["name"], {"families": sys["families"], "anchor": sys.get("standard_anchor", "")})
        if store is not None:
            store.add(
                Doc(
                    doc_id=sys_id,
                    kind="system",
                    text=f"{sys['name']}：{'，'.join(sys['families'])}。"
                    f"承载功能：{'，'.join(sys.get('typical_functions', []))}。"
                    f"标准锚点：{sys.get('standard_anchor', '')}。涉及资产：{sys.get('asset_evidence', '')}",
                    meta={"label": sys["name"], "code": code, "domain": _system_domain_of(code, sys)},
                )
            )
        count += 1
    # 故障 → 系统（按子系统名精确映射到 13 系统域；完整覆盖全部 203 故障）
    # 与 domain/data/domain_systems.json 的 subsystems 字段保持一致（双源同口径）
    _SUB_TO_SYS = {
        "VCU": "SYS-TRAIN",
        "列车控制": "SYS-TRAIN",
        "网络": "SYS-TRAIN",
        "制动": "SYS-BRAKE",
        "牵引": "SYS-TRACTION",
        "车门": "SYS-DOOR",
        "受电弓": "SYS-PANTO",
        "能源": "SYS-BATT",
        "辅助电源": "SYS-AUX",
        "空调": "SYS-HVAC",
        "乘客信息": "SYS-PIS",
        "照明": "SYS-LIGHT",
        "烟火": "SYS-FIRE",
        "乘客安全": "SYS-FIRE",
        "走行部": "SYS-BOGIE",
        "信号": "SYS-SENSING",
    }
    for n in g.nodes.values():
        if n.kind != "fault":
            continue
        fk = n.id.split(":", 1)[1]
        subsystem = str(n.props.get("subsystem", ""))
        sys_code = _SUB_TO_SYS.get(subsystem)
        if sys_code and f"system:{sys_code}" in g.nodes:
            g.add_edge_raw(n.id, f"system:{sys_code}", "belongs_to")
        else:
            # 兜底：文本命中（兼容非标准 subsystem 值）
            for sys in data.get("systems", []):
                ev = sys.get("asset_evidence", "")
                if fk in ev:
                    g.add_edge_raw(n.id, f"system:{sys['code']}", "belongs_to")
                    break
    return count


def _system_domain_of(code: str, sys: dict) -> str:
    """system 文档的分区标签 → 复用到 Q4 域路由（brake/door/network/...）。

    优先取 domain_systems.json 里每个 system 的 "domain" 字段（单一真源），
    字段缺失时退回内建映射。13 域 = 检索分区键：图谱先定位系统 → 域内 topk。
    """
    label = sys.get("domain")
    if label:
        return label
    mapping = {
        "SYS-TRAIN": "network",
        "SYS-BRAKE": "brake",
        "SYS-TRACTION": "traction",
        "SYS-DOOR": "door",
        "SYS-PANTO": "pantograph",
        "SYS-BATT": "battery",
        "SYS-AUX": "aux",
        "SYS-HVAC": "hvac",
        "SYS-PIS": "pis",
        "SYS-LIGHT": "light",
        "SYS-FIRE": "fire",
        "SYS-BOGIE": "bogie",
        "SYS-SENSING": "signal",
    }
    return mapping.get(code, "")


def enrich_graph(g: KnowledgeGraph, store: VectorStore | None = None) -> dict:
    """注入全部可用领域知识。返回注入统计（files: {name: count}）。"""
    report: dict = {"files": {}}
    for fname, fn in [
        ("domain_ebm.json", inject_ebm),
        ("domain_network.json", inject_network),
        ("domain_safety.json", inject_safety),
        ("domain_systems.json", inject_systems),
    ]:
        data = load_domain_json(fname)
        if data:
            report["files"][fname.replace("domain_", "").replace(".json", "")] = fn(g, store, data)
    # 症状资产 + 因果表注入（B 步）：symptom 节点/文档 + 带依据因果边。
    # 需在 inject_systems 之后（causes 反查 fault、indicates 指向 fault/system 均就绪）。
    from .causal import inject_symptoms_causal as _inject_symptoms

    report["symptom_causal"] = _inject_symptoms(g, store)
    # 故障 → 危害 深连（在全部注入完成后，确保 hazard/fault 都已存在）
    report["fault_hazard_links"] = _link_faults_to_safety(g)
    return report
