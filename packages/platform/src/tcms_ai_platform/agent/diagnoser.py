"""症状多跳诊断规划器（C 步：harness/RAG 层的"无码症状→诊断步骤建议"）。

输入：一句症状类描述（无故障码，如「仪表盘闪烁但无故障码」）。
管线：
    1. kb 检索症状资产（RAG）：在向量库中找 kind=symptom 的命中（词元重合闸门，
       防噪声/防错域）；无命中 → 诚实 no_match + 需补充引导，绝不编造故障码；
    2. 图谱多跳：沿因果边取候选链 —— 症状 -indicates-> 怀疑故障（depth1），
       再沿 fault -causes-> fault 反查疑似根因（depth2+，见 graph.causal_chain）；
    3. 诊断建议：对每个候选给出"验证哪个真实故障 / 查哪个报文信号（detect）/
       用哪个现成场景复现"，置信度按 跳数×依据类型 确定性计算；
    4. 溯源：每条建议带 因果边 basis/note + 命中文档，derived 候选明确标注
       "仅示意，不可当已确认故障码"。

置信度口径（P2-2）：`confidence` = **候选排序的依据充分性分数，不是概率**。
取值只由 (跳数, 依据类型) 决定（见 _CONF），对外不宣称概率，只用于同批候选
排序与 UI 展示（排序分/依据分）；口径锁值测试防漂移，UI 文案不得暗示概率。

多轮追问（P1-1）：可传入上一轮 `session_anchor`（证据引用，见 diagnose_memory；
非散文摘要），当本轮直配落空且语句带"刚才/继续/那个部位"等指代词时复用锚点
症状继续走链，并在 evidence.session 中如实标注"使用了上一轮锚点"。

可分性澄清（P1-2）：候选 top1/top2 置信接近（差 <0.15）且同域或同跳时不硬排
第一——返回 `clarification`（需补充哪类区分性观测，文本全部来自真实
detect/场景，不发明），reply 以追问口吻给出。

诚实纪律：
    - 只输出真实故障字典键（faults.yaml 203 条）/ 真实系统域；
    - 证据不足 → uncertain=true + 明确"需补充 X"；
    - LLM（可选）只在候选集内做排序/文案，不得自由发明故障（默认离线规则）。
"""

from __future__ import annotations

import re

from ..core.models import AssetModel
from ..domain.causal import symptoms as _catalog_symptoms
from ..knowledge import HybridRetriever, KnowledgeGraph
from .diagnose_memory import is_follow_up

# 与 retriever._route_via_graph 同款"通用/诊断中性字"（防止仅因"故障/检测/报警"
# 等词重合而误判语义命中）
_GENERIC_CJK = frozenset(
    "故障检测处置注入恢复系统等级动作期望监控告警报警方法分析流程事件影响信息"
)

# 车辆框架/状态类"弱证据字"（P1-3 对抗集修复）：这些字散布在多数症状本体与
# 用户口语里（列车/车厢/状态/正常/吗…），若计入共享字闸门会让"这列车整体都
# 正常吧"这类无码废话误中某个症状（如 clock_jump）。闸门计数时剔除，但**不**
# 影响向量相似度排序（召回面不变，只收紧"够不够格算症状证据"）。
_SYMPTOM_CONTEXT_CJK = frozenset(
    "列车辆厢乘客人室舱驾驶台司机操运内上中下前左右"
    "状态正常否是否还在好都也很太" + "的有没无吗呢哪个这那"
)

# 置信度表（P2-2 口径）：**排序分数，非概率** —— 语义 = "该候选在证据链中的
# 依据充分性排序值"，只由 (跳数, 依据类型) 单调决定，不与真实概率挂钩。
# UI/文案必须按"排序分/依据分"展示，不得按百分比读作概率；锁值测试防漂移。
_CONF = {
    (1, "real_mechanism"): 0.85,  # 症状直接指向的真实机制候选
    (1, "derived"): 0.5,  # 症状直接指向的示意候选
    (2, "real_mechanism"): 0.7,  # 一级嫌疑的真实根因（causes 反查）
    (2, "derived"): 0.4,
    (3, "real_mechanism"): 0.6,
    (3, "derived"): 0.35,
}
_SYMPTOM_MIN_ABS_SCORE = 0.05  # hashed 余弦绝对下限（防噪声）
_SYMPTOM_MIN_SHARED_CJK = 2  # 查询与症状文档共享的（非通用）中文字数下限


def _non_generic_cjk(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]", text or "")) - _GENERIC_CJK


def _zh_domain_zh(domain: str) -> str:
    from ..knowledge.vector import DOMAIN_ZH

    return DOMAIN_ZH.get(domain, domain)


def _fault_domain_of(m: AssetModel, fault_key: str) -> str:
    """故障键 → 13 域分区标签（domain_systems.json 单一真源派生）。"""
    from ..knowledge.vector import subsystem_domain

    fd = m.faults_by_key.get(fault_key)
    return subsystem_domain(fd.subsystem) if fd else ""


def _confidence(hop: int, basis: str) -> float:
    return _CONF.get((min(hop, 3), basis), 0.3)


def _scenario_files(m: AssetModel, fault_key: str) -> list[str]:
    return sorted(s.file for s in m.scenarios.values() if fault_key in s.fault_keys)


def _detect_summary(m: AssetModel, fault_key: str) -> str:
    fd = m.faults_by_key.get(fault_key)
    if fd is None:
        return ""
    return f"查 {fd.detect}" if fd.detect else fd.desc


# ---------------------------------------------------------------------------
# 匹配：检索症状资产（RAG）
# ---------------------------------------------------------------------------


def match_symptom(
    m: AssetModel,
    retriever: HybridRetriever,
    text: str,
    k: int = 8,
) -> dict | None:
    """在 kb 中找症状资产命中 → {key,name,...,score,doc_text} | None。

    只采信 kind=symptom 的文档；噪声闸门（P1-3 对抗集修复）：
    - 绝对分下限；
    - 共享字闸门只对症状**本体**（name + description）计数，**不**比对向量文档里
      拼接的模板尾巴（涉及域 / 疑似候选故障清单 / 诚实标注）——否则候选清单里
      的"列车级时间同步丢失"会让任何含"列车"的无码废话（如"列车地板漏水了"）
      蹭过 ≥2 字闸门误中 network 症状；
    - 车辆框架/状态类弱证据字（列车/车厢/状态/正常/吗…）不参与计数。
    """
    if not text.strip():
        return None
    # 症状资产跨 13 域分布：若按域词路由，用户口语里的域词（如“客室”）可能把
    # 症状文档隔在另一分区外（客室→hvac，而“客室灯组频闪”症状属 light）。
    # 故症状匹配走**全局检索**（不分区），再按 kind/噪声闸门过滤 —— 只影响召回面，
    # 不做错域路由。
    hits = retriever.store.search(text, k=max(k * 2, 16), domains=None)
    # 症状本体缓存：{key: "name description"}（仅正文，剔除模板/候选清单尾巴）
    clean_by_key = {
        s.get("key", ""): f"{s.get('name', '')} {s.get('description', '')}"
        for s in _catalog_symptoms()
    }
    qchars = _non_generic_cjk(text) - _SYMPTOM_CONTEXT_CJK
    best: dict | None = None
    for h in hits:
        if h.get("kind") != "symptom":
            continue
        score = float(h.get("score", 0.0))
        if score < _SYMPTOM_MIN_ABS_SCORE:
            continue
        doc_id = str(h.get("doc_id", ""))
        key = doc_id.split(":", 1)[1] if doc_id.startswith("symptom:") else ""
        clean = clean_by_key.get(key)
        if not clean:
            continue  # 不在症状目录 → 不采信（防注入模板噪声）
        tchars = _non_generic_cjk(clean) - _SYMPTOM_CONTEXT_CJK
        if len(qchars & tchars) < _SYMPTOM_MIN_SHARED_CJK:
            continue
        if best is None or score > best["score"]:
            entry = {
                "key": key,
                "name": (h.get("meta") or {}).get("name", key),
                "score": round(score, 4),
                "doc_text": str(h.get("text", ""))[:400],
                "annotation": (h.get("meta") or {}).get("annotation", ""),
            }
            for s in _catalog_symptoms():
                if s.get("key") == key:
                    entry.update(
                        {
                            "description": s.get("description", ""),
                            "domains": s.get("domains", []),
                            "evidence": s.get("evidence", ""),
                        }
                    )
                    break
            best = entry
    return best


# ---------------------------------------------------------------------------
# 诊断主流程
# ---------------------------------------------------------------------------


def diagnose_symptom(
    m: AssetModel,
    graph,
    retriever: HybridRetriever,
    text: str,
    depth: int = 3,
    max_candidates: int = 8,
    use_llm: bool = False,
    llm_chat=None,  # noqa: ARG001 - 预留 LLM 候选内仲裁；默认规则路径
    session_anchor: dict | None = None,  # P1-1: 上轮锚点（证据引用，见 diagnose_memory）
) -> dict:
    """症状文本 → 多跳诊断结果（结构化；绝不编造故障码）。

    session_anchor：多轮追问时传入上一轮锚点 {symptom, candidates, facts}；
    本轮直配落空且语句含指代词（刚才/继续/那个部位…）→ 复用锚点症状继续走链，
    并在 evidence.session 如实标注 anchor_used=True（证据引用，非散文摘要）。
    """
    text = (text or "").strip()
    if not text:
        r0 = _no_match(text, "输入为空 —— 请描述你观察到的异常现象（部位/工况）。")
        r0["related_assets"] = []
        return r0

    anchor = session_anchor or {}
    anchor_used = False
    sym = match_symptom(m, retriever, text)
    if sym is None:
        a_sym = anchor.get("symptom")
        if a_sym and is_follow_up(text):
            # 追问指代上轮：复用锚点症状（本体仍在症状资产/图谱上），诚实标注
            sym = dict(a_sym)
            anchor_used = True
        else:
            r1 = _no_match(
                text,
                "未在症状资产中找到匹配 —— 需要补充：① 哪个部位/设备；② 什么工况下发生；"
                "③ 是否伴随其它现象或告警。未确认的描述不会归为某个故障。",
            )
            # 不空手引导：先尝试"宽泛问法推理"（域词×故障句式 → 定向推荐），再给相关资产
            from ..knowledge.vague import analyze_vague

            try:
                vg = analyze_vague(m, text)
            except Exception:  # noqa: BLE001
                vg = None
            if vg:
                r1["recommendation"] = vg
            r1["related_assets"] = _related_assets(retriever, text)
            return r1

    sym_id = f"symptom:{sym['key']}"
    walk = graph.causal_chain(sym_id, depth=depth) if sym_id in graph.nodes else {
        "seed": sym_id, "depth": depth, "chains": [], "node_count": 0,
    }

    # 合并候选：同一 fault 保留最浅跳 + 更强依据；chains 汇总溯源（含出处 refs）
    cand_by_key: dict[str, dict] = {}
    all_refs: set[str] = set()
    for hops in walk.get("chains", []):
        path_keys: list[str] = []
        path_names: list[str] = []
        for h in hops:
            to_id = h["to"]
            kind, key = (to_id.split(":", 1) + [""])[:2]
            if kind == "fault":
                cand = cand_by_key.setdefault(
                    key,
                    {
                        "fault": key,
                        "name": "",
                        "domain": "",
                        "domain_zh": "",
                        "level": "",
                        "action": "",
                        "sil": "",
                        "detect": "",
                        "scenarios": [],
                        "chains": [],
                        "best_hop": 99,
                        "basis": "derived",
                    },
                )
                hop_no = len(path_keys) + 1
                basis = h.get("basis") or "derived"
                if hop_no < cand["best_hop"] or (
                    hop_no == cand["best_hop"] and basis == "real_mechanism" and cand["basis"] != "real_mechanism"
                ):
                    cand["best_hop"] = hop_no
                    cand["basis"] = basis
                if h.get("note"):
                    cand.setdefault("notes", []).append(h["note"])
                hop_refs = _asset_refs([h.get("from", ""), to_id])
                all_refs.update(hop_refs)
                cand["chains"].append(
                    {
                        "from": h.get("from", ""),
                        "to": to_id,
                        "path": list(path_names),
                        "hop": hop_no,
                        "rel": h.get("rel", ""),
                        "basis": basis,
                        "note": h.get("note", ""),
                        "refs": hop_refs,  # P2-1：每链可点到资产 file:key
                    }
                )
                fd = m.faults_by_key.get(key)
                if fd is not None:
                    cand["name"] = fd.name
                    cand["level"] = fd.level
                    cand["action"] = fd.action
                    cand["sil"] = fd.sil
                    cand["detect"] = fd.detect
                    cand["domain"] = _fault_domain_of(m, key)
                    cand["domain_zh"] = _zh_domain_zh(cand["domain"])
                    if not cand["scenarios"]:
                        cand["scenarios"] = _scenario_files(m, key)[:4]
            elif kind == "system":
                pass  # system 域级怀疑保留在 notes（当前因果表未用，前瞻兼容）
            path_keys.append(kind + ":" + key if kind else "")
            path_names.append(_graph_label(graph, to_id))

    candidates = []
    for key, c in cand_by_key.items():
        fd = m.faults_by_key.get(key)
        if fd is None:
            continue  # 诚实：只输出真实故障
        candidates.append(
            {
                "fault": key,
                "name": fd.name,
                "domain": c["domain"],
                "domain_zh": c["domain_zh"] or "通用",
                "hop": c["best_hop"],
                "basis": c["basis"],
                "confidence": round(_confidence(c["best_hop"], c["basis"]), 3),
                "level": fd.level,
                "action": fd.action,
                "sil": fd.sil,
                "check": _detect_summary(m, key),
                "scenarios": c["scenarios"],
                "notes": list(dict.fromkeys(c.get("notes", [])))[:3],
                "chains": c["chains"][:2],
                "derived": c["basis"] == "derived",
            }
        )
    candidates.sort(
        key=lambda c: (-c["confidence"], c["hop"], 0 if c["basis"] == "real_mechanism" else 1)
    )
    candidates = candidates[:max_candidates]

    # —— 可选：LLM 只在候选内仲裁（重排，禁止引入新故障键）——
    llm_used = False
    if use_llm and candidates:
        chat = llm_chat
        if chat is None:
            try:
                from .llm_backend import LLMAgentBackend, _api_key

                if _api_key() is not None:
                    chat = LLMAgentBackend()._chat
            except Exception:  # noqa: BLE001 - LLM 不可用则保持规则排序
                chat = None
        if chat is not None:
            order = _llm_arbitrate_candidates(candidates, sym, text, chat)
            if order:
                rank = {k: i for i, k in enumerate(order)}
                candidates.sort(key=lambda c: rank.get(c["fault"], len(rank)))
                llm_used = True

    plan = _build_plan(candidates)
    reply = _rule_reply(text, sym, candidates, plan)
    if llm_used:
        reply += "\n（LLM 已在候选内重排诊断顺序 —— 未引入候选之外的任何故障键。）"

    # P1-2: 可分性不足 → 追问区分性观测，不硬排第一（仍不发明）
    clar = _ambiguity(candidates) if candidates else None
    if clar:
        reply += "\n\n" + _ambiguity_suffix(clar)

    # P1-1: 使用了上一轮锚点 → reply 与 evidence 如实标注（证据引用）
    if anchor_used:
        reply += (
            "\n（本轮回溯上一轮症状锚点「"
            + str(sym.get("name") or sym.get("key") or "")
            + "」继续走因果链 —— 记忆只存证据引用，不存摘要。）"
        )

    evidence = {
        "symptom_hit": {
            "doc_id": f"symptom:{sym['key']}",
            "kind": "symptom",
            "score": sym["score"],
            "text": sym["doc_text"],
        },
        "causal_edges_real": sum(1 for c in candidates if not c["derived"]),
        "causal_edges_derived": sum(1 for c in candidates if c["derived"]),
        "edge_refs": sorted(all_refs),  # P2-1：本诊段全部出处 file:key（可点到资产）
        "recent_runs": _recent_runs(
            graph, [f for c in candidates for f in c["scenarios"]]
        ),  # P2-3：复现场景近期真实执行记录（无则 []）
    }
    if anchor:
        evidence["session"] = {
            "anchor_present": True,
            "anchor_used": anchor_used,
            "prior_symptom_key": (anchor.get("symptom") or {}).get("key"),
            "prior_candidates": [c["fault"] for c in (anchor.get("candidates") or [])],
            "facts": list((anchor.get("facts") or [])[:2]),
        }
    return {
        "query": text,
        "matched": True,
        "no_match": False,
        "symptom": sym,
        "uncertain": not candidates,
        "reply": reply,
        "candidates": candidates,
        "plan": plan,
        "evidence": evidence,
        "clarification": clar,
        "session_anchor_used": anchor_used,
        "no_fault_code_invented": True,
        "llm_generated": llm_used,
    }


def _graph_label(graph, node_id: str) -> str:
    n = graph.nodes.get(node_id)
    if n is None:
        return node_id
    # fault label "名字 (F-TCMS-xxx)" → 名字
    label = n.label
    if " (" in label:
        return label.split(" (", 1)[0]
    return label


def _llm_arbitrate_candidates(candidates: list[dict], sym: dict, query: str, chat) -> list[str] | None:
    """LLM 候选内仲裁：只允许在给定 fault 键内重排/挑序；返回顺序或 None（失败/非法）。

    纪律（红线）：提示词明令禁止输出候选之外的故障键；返回列表经“有效集”过滤，
    非候选键一律丢弃，绝不进入结果 —— LLM 无法自由发明故障。
    """
    import json
    import re

    keys = [c["fault"] for c in candidates]
    # 只仲裁规则排序最靠前的前 4 个（候选已按置信度排好；模型长中文提示易空返回，故极简化）
    sel = candidates[:4]
    user_text = (
        f"症状：{query[:40]}\n"
        f"候选（只能原样使用这些英文键）：{', '.join(c['fault'] for c in sel)}\n"
        "请挑 1-3 个你认为最可能的：先逐行列出所选英文键，再给一句理由。"
    )
    system = (
        "你是 TCMS 症状诊断候选仲裁器。你只能原样使用用户给出的候选故障英文键，挑选并排序；"
        "严禁编造或使用候选之外的任何故障键。"
    )
    try:
        raw = chat(system, user_text)
    except Exception:  # noqa: BLE001 - LLM 失败 = 规则排序保底
        return None
    if not raw or not str(raw).strip():
        return None
    raw_s = str(raw)
    order = None
    m = re.search(r"\[[^\]]*\]", raw_s, flags=re.S)
    if m:
        try:
            order = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            order = None
    if not isinstance(order, list):
        order = None
    if order is None:
        # 兜底：LLM 未给纯 JSON 数组时，按候选键在回复文本中的首次出现顺序取序（只认候选键）
        found = sorted((raw_s.find(k), k) for k in keys if raw_s.find(k) >= 0)
        if found:
            order = [k for _, k in found]
    if not isinstance(order, list) or not order:
        return None
    valid = set(keys)
    picked = [str(k) for k in order if str(k) in valid]
    rest = [k for k in keys if k not in picked]
    if not picked:
        return None
    return picked + rest


def _build_plan(candidates: list[dict]) -> list[dict]:
    """候选 → 诊断步骤建议（验证哪条故障/查哪个报文信号/用什么场景复现）。"""
    plan = []
    for i, c in enumerate(candidates, start=1):
        verify = f"检查真实故障 {c['fault']}（{c['name']}，{c['level']}/{c['action']}）"
        if c["check"]:
            verify += f"：{c['check']}"
        step = {
            "step": i,
            "phase": "verify_fault",
            "fault": c["fault"],
            "name": c["name"],
            "domain": c["domain"],
            "domain_zh": c["domain_zh"],
            "hop": c["hop"],
            "basis": c["basis"],
            "confidence": c["confidence"],
            "description": verify[:220],
            "scenarios": c["scenarios"],
            "note": c["notes"][0] if c["notes"] else "",
            "derived": c["derived"],
        }
        plan.append(step)
    return plan


def _basis_zh(basis: str) -> str:
    return "真实机制" if basis == "real_mechanism" else "示意(derived)"


def _rule_reply(text: str, sym: dict, candidates: list[dict], plan: list[dict]) -> str:
    lines: list[str] = []
    if not candidates:
        lines.append(
            f"按症状描述「{text}」命中了症状资产「{sym.get('name', sym.get('key', ''))}」，"
            "但图谱上没有可审计的因果链候选 —— 说明该症状资产还没被因果表覆盖，"
            "无法凭猜给出故障结论。需补充更具体的现象（部位/工况/伴随告警）。"
        )
        return "\n".join(lines)
    lines.append(
        f"按症状描述「{text}」，匹配到症状资产「{sym.get('name', '')}」"
        f"（涉及域：{'、'.join(sym.get('domains', []))}；诚实标注：{sym.get('annotation', '')}）。"
    )
    lines.append("图谱沿因果边给出的诊断候选（按置信度排序，逐条可溯源）：")
    for i, c in enumerate(candidates[:6], start=1):
        lines.append(
            f"{i}. {c['name']}（{c['fault']}，域 {c['domain_zh']}，"
            f"第{c['hop']}跳 · {_basis_zh(c['basis'])} · 置信 {c['confidence']:.2f}）"
            f"{('：' + c['notes'][0]) if c['notes'] else ''}"
        )
    if any(c["derived"] for c in candidates):
        lines.append(
            "标注：上表中 marked derived 的候选仅为示意提示（证据较弱），"
            "不能当成已确认故障码 —— 需按「检查」动作逐条验证后再下结论。"
        )
    if plan:
        first = plan[0]
        lines.append(
            f"建议先验证：{first['description']}"
            + (f"；可用现成场景复现：{', '.join(first['scenarios'][:3])}" if first["scenarios"] else "")
        )
    else:
        lines.append("当前候选不足以构成验证计划 —— 需补充现象细节。")
    lines.append("以上全部故障键均来自真实故障字典（不编造故障码）。")
    return "\n".join(lines)


def _related_assets(retriever, text: str, k: int = 4) -> list[dict]:
    """no_match 时给"可能相关资产"（真实 fault/scenario，可点击跳图谱/演示），不空手引导。"""
    t = (text or "").strip()
    if not t:
        return []
    out: list[dict] = []
    for h in retriever.store.search(t, k=max(k * 2, 8), domains=None):
        if h.get("kind") not in ("fault", "scenario"):
            continue
        if float(h.get("score", 0.0)) < _SYMPTOM_MIN_ABS_SCORE:
            continue
        out.append(
            {"doc_id": h["doc_id"], "kind": h["kind"], "text": str(h.get("text", ""))[:120]}
        )
        if len(out) >= k:
            break
    return out


def _no_match(query: str, reason: str) -> dict:
    return {
        "query": query,
        "matched": False,
        "no_match": True,
        "symptom": None,
        "uncertain": True,
        "reply": reason,
        "candidates": [],
        "plan": [],
        "evidence": {},
        "clarification": None,
        "recommendation": None,
        "related_assets": [],
        "no_fault_code_invented": True,
        "llm_generated": False,
    }


def _asset_refs(node_ids: list[str]) -> list[str]:
    """图节点 id 列表 → 去重后的资产出处 `file:key`（P2-1：evidence 可点到资产）。"""
    seen: set[str] = set()
    out: list[str] = []
    for nid in node_ids:
        if not nid:
            continue
        r = KnowledgeGraph.node_asset_ref(nid)
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _recent_runs(graph, scenario_files: list[str], limit: int = 5) -> list[dict]:
    """P2-3：给定复现场景文件列表 → 该资产近期的真实执行记录（可机器自证）。

    扫描 executed 边（run -executed-> scenario:<file>）；只在相关场景上浮出，
    无记录时返回 []（诚实：没跑过就是没跑过）。
    """
    if not scenario_files:
        return []
    wanted = {f"scenario:{f}" for f in scenario_files}
    out: list[dict] = []
    for e in graph.edges:
        if e.kind != "executed":
            continue
        rid, nid = (e.src, e.dst) if e.src.startswith("run:") else (e.dst, e.src)
        if not rid.startswith("run:") or nid not in wanted:
            continue
        rn = graph.nodes.get(rid)
        if rn is None:
            continue
        props = rn.props or {}
        out.append(
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
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# 可分性澄清（P1-2）：top1/top2 不可区分 → 追问"缺哪个观测"，不硬排
# ---------------------------------------------------------------------------

_AMBIGUITY_CONF_GAP = 0.15  # top1-top2 置信差小于该值视为"接近"


def _ambiguity(candidates: list[dict]) -> dict | None:
    """候选可分性判断：top1/top2 置信接近且同域或同跳 → 返回澄清需求。

    返回 {needs_more, kind, between:[{fault,name}], distinguishing_observations,
    hint} | None。观测文本全部来自候选真实 check（detect/desc 资产），不发明。
    """
    if len(candidates) < 2:
        return None
    a, b = candidates[0], candidates[1]
    gap = float(a["confidence"]) - float(b["confidence"])
    same_domain = a.get("domain") == b.get("domain")
    same_hop = a.get("hop") == b.get("hop")
    if gap >= _AMBIGUITY_CONF_GAP or not (same_domain or same_hop):
        return None
    obs: list[str] = []
    for c in (a, b):
        check = str(c.get("check") or "").strip()
        if check and check not in obs:
            obs.append(check)
    return {
        "needs_more": True,
        "kind": "disambiguate",
        "between": [
            {"fault": a["fault"], "name": a["name"]},
            {"fault": b["fault"], "name": b["name"]},
        ],
        "distinguishing_observations": obs[:4],
        "hint": (
            f"两个候选置信接近（差 <{_AMBIGUITY_CONF_GAP:.2f}）且同域/同跳，"
            "不能仅凭描述硬排第一 —— 需补充能区分两者的观测后继续。"
        ),
    }


def _ambiguity_suffix(clar: dict) -> str:
    """把澄清需求转成 reply 的追问段落（诚实：承认不可区分，给区分路径）。"""
    a, b = clar["between"][0], clar["between"][1]
    lines = [
        "⚠ 候选不可区分 —— 不硬排第一。",
        f"「{a['name']}」与「{b['name']}」置信接近且同域/同跳，请补充区分性观测：",
    ]
    for o in clar["distinguishing_observations"] or []:
        lines.append(f"  · {o}")
    lines.append("若无法补充，请按各候选的「检查」动作逐一验证，勿把任一候选当已确认结论。")
    return "\n".join(lines)
