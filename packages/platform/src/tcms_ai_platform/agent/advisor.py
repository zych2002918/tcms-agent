"""编排顾问（Orchestration Advisor）——多轮对话，而非一次性解析。

为什么存在：用户输入**无法匹配内存故障**时，绝不 422 拒绝（/api/agent/free 的
NoFaultMatch 红线的反面补充）。编排顾问把一句话转成一段可继续的对话：
    意图 → 故障匹配候选 / 澄清追问 / 场景编排建议 / 引导回 TCMS / 自定义新故障流程

输出结构（JSON）：
    reply              中文回复（规则模板，或 LLM key 可用时的 AI 生成）
    intent             match_fault | compose_scenario | clarify | out_of_domain
                       | custom_proposal
    fault_matches      [{key,name,action,level,confidence,matched_on}]
    suggested_steps?   可执行步骤草稿（[{at,action,fault,node,level,expect,impact}]，
                       与 /api/run/custom、/api/faultlab/demo-steps 同构）
    rag_evidence?      [{doc_id,kind,score,text}]（RAG 命中的证据链）
    needs_clarification bool
    followup_question? str

决策流（规则优先 + RAG 语义兜底 + LLM 文案增强；永远有友好回复）：
    1. 规则打分（复用 freeform._score_candidates，锚定真实故障字典）：
       - 唯一高分 → match_fault（解释该故障 + 默认处置 + 建议可编排场景）
       - 弱/多候选  → clarify（列出候选让用户确认）
       - 多候选 + 编排措辞 → compose_scenario（把候选故障组装成可执行步骤草稿）
    2. 规则零候选 → **不 422**：
       - 先做 TCMS 主题判别（资产派生的「具体域 token」）：
           无 TCMS 语义 → out_of_domain（友好引导回主题）
           含编排/处置意图词但无具体故障 → custom_proposal（自定义新故障引导流程）
           含具体域 token（门/弓/心跳/总线…）→ RAG 语义澄清：
               RAG 高相关命中 → clarify（「你可能指这些」+ followup_question）
               否则 → custom_proposal
    3. LLM 参与：llm_available() 时用 LLM 润色回复文案（有真实 key 体验更好）；
       无 key 用规则模板（诚实标注离线，字段 llm_generated=false）。
    4. 与 harness 闭环：match_fault 命中可经 execute=True 直接走 AgentHarness
       真实执行；compose 的 suggested_steps 可直接 POST /api/run/custom。

诚实纪律（与全仓库一致）：
    - 每条 fault_matches 可指回真实故障字典条目（key/name/action/level 全来自资产）；
    - rag_evidence 展示检索证据（doc_id/kind/score/text），供用户核对自己被如何理解；
    - custom_proposal 不假装"新故障已收录"——明确说明字典边界，引导补齐描述；
    - 规则零候选时即使 LLM 可用，也不允许 LLM 自由发明真实故障键（只允许澄清/建议）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import AssetModel
from ..knowledge import HybridRetriever
from .freeform import _ACTION_ZH, _score_candidates

# ---------------------------------------------------------------------------
# 阈值与词表（确定性；规则可解释）
# ---------------------------------------------------------------------------

# 规则命中判定（对齐 freeform.parse_free_goal 的"明确命中"门限）
STRONG_SCORE = 3.0  # key/中文名整体命中即 ≥3
STRONG_GAP = 1.0  # 与次名的分差
# RAG 澄清门限
RAG_MIN_SCORE = 0.18  # 链接证据(顶层命中)的最低分
RAG_DIRECT_FAULT = 0.25  # 直接 fault-kind 命中的最低分
# compose 场景步骤默认节奏（秒）
COMPOSE_FIRST_AT = 1.0
COMPOSE_INJECT_GAP = 3.0
COMPOSE_RECOVER_GAP = 2.0

# 编排措辞：出现即视为"用户在编排多步场景"
_COMPOSE_HINT = (
    "编排",
    "组合",
    "叠加",
    "级联",
    "多故障",
    "序列",
    "先注入",
    "再注入",
    "然后注入",
    "依次",
    "先后",
    "步骤草稿",
)
# 域意图词（无具体故障但仍属 TCMS 测试主题 → custom_proposal）
_GENERIC_INTENT = (
    "故障",
    "处置",
    "注入",
    "恢复",
    "告警",
    "降级",
    "场景",
    "测试",
    "验证",
    "编排",
    "怎么",
    "如何",
    "怎样",
    "处理",
    "应对",
    "模拟",
    "复现",
)
# 除上面意图词外，仍算"具体 TCMS 语义"的高信号短 token（门/弓/温等），
# 允许「门的问题」这类口语化问法绕过规则零候选进入 RAG 语义澄清。
_SHORT_DOMAIN_TOKENS = (
    "门",
    "弓",
    "温",
    "电",
    "压",
    "速",
    "帧",
    "总",
    "心跳",
    "总线",
    "超速",
    "牵引",
    "制动",
    "受电",
    "网压",
    "电量",
    "节点",
    "风暴",
    "传感器",
    "计数器",
    "仲裁",
    "短路",
    "断路",
    "噪声",
    "漂移",
    "卡死",
    "翻转",
    "冲突",
    "拉弧",
    "回路",
    "缓冲",
    "挡位",
    "联锁",
)


def _norm(s: str) -> str:
    return "".join(s.lower().split())


def _strip_latin(s: str) -> str:
    import re

    return re.sub(r"[A-Za-z0-9_]+", "", s)


def _name_base(name: str) -> str:
    """去括号后缀取主体（车门故障（按未关处理）→ 车门故障）。"""
    return name.split("（", 1)[0].strip()


def _asset_specific_tokens(m: AssetModel) -> set[str]:
    """资产派生的"具体语义" token：故障中文名主体/子系统 + 功能/场景名。

    这些是用户口语里最可能用来指代某个具体故障的词（剥离英文与括号），
    叠加高信号短 token（门/弓/温…）后用于判别"用户是否在谈 TCMS 里的什么"。
    """
    toks: set[str] = set(_SHORT_DOMAIN_TOKENS)
    for f in m.faults_by_key.values():
        base = _strip_latin(f.name).strip()
        if base:
            toks.add(base)
            nb = _name_base(base).strip()
            if nb:
                toks.add(nb)
        if f.subsystem:
            toks.add(f.subsystem.strip())
    for fn in m.functions.values():
        base = _strip_latin(fn.name).strip()
        if base:
            toks.add(base)
    for s in m.scenarios.values():
        base = _strip_latin(s.name).strip()
        if base:
            toks.add(base)
    return {t for t in toks if len(t) >= 1}


def _confidence(score: float) -> float:
    """规则分 → 展示置信度（0~1，启发式单调映射，可解释）。"""
    return round(min(1.0, 0.5 + 0.12 * score), 3)


def _default_node(fault: str) -> str:
    """故障注入的默认节点（仅作编排草稿的提示；引擎以 fault 为准）。"""
    return {
        "door_fault": "bcu",
        "door_sensor_noise": "bcu",
        "overspeed": "vcu",
        "traction_loss": "vcu",
        "heartbeat_loss_vcu": "vcu",
        "node_restart_storm": "vcu",
        "eb_failure": "bcu",
        "brake_actuator_stuck": "bcu",
        "soc_low": "bms",
        "temp_high": "bms",
        "pantograph_arc": "vcu",
    }.get(fault, "vcu")


# ---------------------------------------------------------------------------
# 输出模型
# ---------------------------------------------------------------------------


@dataclass
class AdvisorTurn:
    """一次顾问对话回复（全部字段可序列化；与端点 JSON 同构）。"""

    reply: str
    intent: str
    fault_matches: list[dict] = field(default_factory=list)
    suggested_steps: list[dict] | None = None
    rag_evidence: list[dict] | None = None
    needs_clarification: bool = False
    followup_question: str | None = None
    # 附加（诚实标注/闭环信息，前端可选展示）
    llm_generated: bool = False
    matched_fault: str | None = None
    scenario_suggestions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict = {
            "reply": self.reply,
            "intent": self.intent,
            "fault_matches": self.fault_matches,
            "needs_clarification": self.needs_clarification,
            "llm_generated": self.llm_generated,
        }
        if self.suggested_steps is not None:
            out["suggested_steps"] = self.suggested_steps
        if self.rag_evidence:
            out["rag_evidence"] = self.rag_evidence
        if self.followup_question:
            out["followup_question"] = self.followup_question
        if self.matched_fault:
            out["matched_fault"] = self.matched_fault
        if self.scenario_suggestions:
            out["scenario_suggestions"] = self.scenario_suggestions
        return out


# ---------------------------------------------------------------------------
# 步骤草稿组装
# ---------------------------------------------------------------------------


def _step_for(m: AssetModel, fault: str, at: float) -> dict:
    fd = m.fault(fault)
    return {
        "at": round(at, 2),
        "action": "inject",
        "fault": fault,
        "node": _default_node(fault),
        "level": fd.level,
        "expect": fd.action,
        "impact": fd.desc[:24],
    }


def _recover_step(fault: str, at: float) -> dict:
    return {"at": round(at, 2), "action": "recover", "fault": fault}


def _compose_steps(m: AssetModel, faults: list[str]) -> list[dict]:
    """把故障键序列组装成可执行步骤草稿（/api/run/custom 可直接消费）。

    节奏：逐故障错峰注入（3s 间隔），全部注入后逐个恢复。含真实 expect
    （字典默认 action），保证真实引擎执行有断言可判。
    """
    steps: list[dict] = []
    t = COMPOSE_FIRST_AT
    for fk in faults:
        steps.append(_step_for(m, fk, t))
        t += COMPOSE_INJECT_GAP
    for fk in faults:
        steps.append(_recover_step(fk, t))
        t += COMPOSE_RECOVER_GAP
    return steps


def _scenario_suggestions(m: AssetModel, fault: str) -> list[dict]:
    """覆盖该故障的真实资产场景（可点击即播/即跑）。"""
    out = []
    for s in m.scenarios.values():
        if fault in s.fault_keys:
            out.append({"file": s.file, "name": s.name, "steps": len(s.steps)})
    return out


# ---------------------------------------------------------------------------
# RAG 语义澄清（规则零候选 → 找"你可能指哪些真实故障"）
# ---------------------------------------------------------------------------


def _specific_tokens_in(text: str, specific: set[str]) -> set[str]:
    n = _norm(text)
    return {t for t in specific if t and _norm(t) in n}


def _rag_fault_candidates(m: AssetModel, retriever: HybridRetriever, query: str) -> tuple[list[dict], list[dict]]:
    """RAG 顶层命中 → 找出真实故障候选（直接命中 + 领域节点邻接故障）。

    只信任「文本包含查询里具体域 token」的证据（防止通用 threshold 中枢把
    所有故障都连进来的假阳性）。返回 (candidates, evidence)。
    """
    resp = retriever.retrieve(query, k=10)
    hits = resp.get("hits", [])
    specific = _asset_specific_tokens(m)
    q_tokens = _specific_tokens_in(query, specific)
    if not q_tokens:
        return [], []

    best: dict[str, dict] = {}  # fault key -> 汇聚信息
    evidence: list[dict] = []
    for h in hits:
        text = (h.get("text") or "") + " " + (h.get("doc_id") or "")
        score = float(h.get("score", 0.0))
        if score < RAG_MIN_SCORE:
            continue
        # 证据文本必须与查询共享具体域 token（否则视为噪声中枢）
        if not (q_tokens & _specific_tokens_in(text, specific)):
            continue
        evidence.append(
            {
                "doc_id": h.get("doc_id"),
                "kind": h.get("kind"),
                "score": round(score, 3),
                "text": (h.get("text") or "")[:160],
            }
        )
        # 直接 fault 命中
        if h.get("kind") == "fault" and str(h.get("doc_id", "")).startswith("fault:"):
            fk = str(h["doc_id"]).split(":", 1)[1]
            if score >= RAG_DIRECT_FAULT and fk in m.faults_by_key:
                cur = best.get(fk)
                if cur is None or score > cur["score"]:
                    best[fk] = {
                        "key": fk,
                        "name": m.fault(fk).name,
                        "action": m.fault(fk).action,
                        "level": m.fault(fk).level,
                        "score": score,
                        "matched_on": "rag_semantic",
                    }
        # 领域节点邻接的故障（req/hazard/function/interlock/system 等文本锚定具体 token）
        if h.get("kind") in ("requirement", "hazard", "function", "interlock", "mechanism", "concept", "state", "system"):
            for nb in h.get("graph_neighbors") or []:
                nid = str(nb.get("id", ""))
                if nid.startswith("fault:"):
                    fk = nid.split(":", 1)[1]
                    if fk in m.faults_by_key:
                        cur = best.get(fk)
                        if cur is None or score > cur["score"]:
                            best[fk] = {
                                "key": fk,
                                "name": m.fault(fk).name,
                                "action": m.fault(fk).action,
                                "level": m.fault(fk).level,
                                "score": score,
                                "matched_on": "rag_semantic",
                            }

    cands = [
        {
            "key": v["key"],
            "name": v["name"],
            "action": v["action"],
            "level": v["level"],
            "confidence": _confidence(min(6.0, v["score"] * 6.0)),
            "matched_on": v["matched_on"],
        }
        for v in sorted(best.values(), key=lambda d: d["score"], reverse=True)[:5]
    ]
    # 语义排序修正：查询含具体 token（如「门」）时，故障自身名称/描述也含该
    # token 的候选（车门故障/门噪声）应排在「仅靠通用领域节点邻接连到」的
    # 候选前（防 H-09 通信中枢把心跳类故障顶到门问题前面）。
    # 注意：锚定文本只用 name/desc/subsystem——detect 里「看门狗」含「门」，
    # 会把心跳丢失误判成"含门语义"（子串噪声）。
    q_toks = q_tokens
    for c in cands:
        fd = m.fault(c["key"])
        own = f"{fd.name} {fd.desc} {fd.subsystem}"
        anchor = sum(1 for t in q_toks if t and _norm(t) in _norm(own))
        c["_anchor"] = anchor
    cands.sort(key=lambda c: (c.pop("_anchor", 0), c["confidence"]), reverse=True)
    return cands, evidence[:5]


# ---------------------------------------------------------------------------
# 回复模板（离线规则；诚实标注）
# ---------------------------------------------------------------------------


def _describe_fault(m: AssetModel, fk: str) -> str:
    fd = m.fault(fk)
    return (
        f"「{fd.name}」({fd.level}/{fd.action}，SIL {fd.sil})：{fd.desc}。"
        f"检测：{fd.detect}。恢复：{fd.recovery}"
    )


def _rule_reply(m: AssetModel, turn: AdvisorTurn, goal: str) -> str:
    """按意图生成规则模板中文回复（离线确定性，诚实标注）。"""
    i = turn.intent
    if i == "match_fault":
        fk = turn.matched_fault or (turn.fault_matches[0]["key"] if turn.fault_matches else "")
        zh = _ACTION_ZH.get(m.fault(fk).action, m.fault(fk).action) if fk and fk in m.faults_by_key else ""
        lines = [f"识别到你在说：{_describe_fault(m, fk)}。" if fk in m.faults_by_key else f"识别到故障：{fk}。"]
        if fk in m.faults_by_key:
            lines.append(f"对应真实故障键 {fk}，默认处置是 {zh}（真实故障字典 action）。")
        if turn.scenario_suggestions:
            names = "、".join(s["name"] for s in turn.scenario_suggestions[:3])
            lines.append(f"可用现成场景覆盖它：{names}（点「运行」即可真实执行验证）。")
        lines.append("想编排成多步场景，可把步骤草稿补全——描述想验证的行为即可。")
        return "\n".join(lines)
    if i == "compose_scenario":
        keys = [f["key"] for f in turn.fault_matches]
        names = "、".join(f"{m.fault(k).name}" for k in keys if k in m.faults_by_key)
        return (
            f"按你的编排意图，涉及的故障（{names}）已组装成一份可执行步骤草稿"
            "（已附真实字典处置 expect，可直接提交 /api/run/custom 真实执行，"
            "或一键转到 FaultLab 生成演示动画）。需要调整注入时刻/期望处置，直接说。"
        )
    if i == "clarify":
        cands = "；".join(
            f"{i + 1}) {f['name']}（{f['key']}，处置 {f['action']}）"
            for i, f in enumerate(turn.fault_matches)
        )
        return (
            "该描述可能对应多个真实故障，请确认指向哪个：\n"
            f"{cands}\n直接回复序号或故障名即可。"
        )
    if i == "custom_proposal":
        return (
            f"你说的像是 TCMS 故障字典（{len(m.faults_by_key)} 个真实故障）还没收录的新故障，或者是"
            "一个更口语的说法。未确认的内容不会归为已有故障。请补三点：① 怎么注入/发生在哪个"
            "部件；② 会造成什么影响（速度/制动/车门/网络…）；③ 你期望系统如何处置。"
            "据此可组装成步骤草稿（用最接近的字典故障演示，或登记为新故障资产）。"
        )
    # out_of_domain
    return (
        f"这句未在 TCMS 测试主题里找到对应内容（知识库含 {len(m.faults_by_key)} 个真实故障、"
        f"{len(m.scenarios)} 个场景、报文/信号与安全需求）。可以试试说「车门故障不能发车」"
        "「验证超速降级」「VCU 心跳丢失怎么处置」，或直接描述想注入/验证的故障现象。"
    )


def _llm_reply(m: AssetModel, turn: AdvisorTurn, goal: str, llm_chat) -> str | None:
    """LLM 润色回复（失败返回 None → 调用方落回规则模板；诚实降级）。"""
    system = (
        "你是 TCMS 列车控制软件测试平台的「编排顾问」。基于给定的结构化决策，"
        "用简体中文写一段自然、专业、引导式的回复（2-4 句）。只输出回复正文，"
        "不要 JSON、不要复述字段名。"
    )
    payload = {
        "intent": turn.intent,
        "fault_matches": turn.fault_matches[:3],
        "suggested_steps_count": len(turn.suggested_steps or []),
        "scenario_suggestions": [s["file"] for s in turn.scenario_suggestions[:3]],
        "user_message": goal,
    }
    try:
        import json

        text = llm_chat(system, json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        return None
    if not text or not text.strip():
        return None
    return text.strip()[:500]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def advisor_turn(
    m: AssetModel,
    retriever: HybridRetriever,
    message: str,
    draft_steps: list[dict] | None = None,
    history: list[dict] | None = None,
    use_llm: bool = True,
    llm_chat=None,
) -> AdvisorTurn:
    """处理一句用户输入 → 结构化顾问回复（永不抛 NoFaultMatch / 永不 422）。

    参数：
        m          资产模型（故障字典真实来源）
        retriever  混合检索（规则零候选时的 RAG 语义澄清）
        message    用户自然语言（可为故障意图/编排请求/不完整描述/任何话）
        draft_steps 当前前端手动编排的步骤草稿（可选）
        history    历史消息（[{role, content}]，可选；供 LLM 上下文，不依赖）
        use_llm    是否允许 LLM 润色回复文案（调用方按 llm_available() 传入）
        llm_chat   注入式 chat(system,user)->str（测试替身；缺省用 LLMAgentBackend）
    """
    goal = (message or "").strip()
    # 空消息 / 只有标点 → 引导（永不 422）
    if not goal:
        return AdvisorTurn(
            reply="请描述想做什么：验证某个故障、编排一个故障场景，或描述一个异常现象都行。",
            intent="clarify",
            needs_clarification=True,
            followup_question="你想验证哪个故障 / 编排什么场景？",
        )

    faults = list(m.faults_by_key.values())
    cands = _score_candidates(goal, faults)
    fault_names = {f.key: f for f in faults}
    compose_hint = any(t in goal for t in _COMPOSE_HINT)

    # ---- 1) 规则命中 ----
    if cands:
        top = cands[0]
        second = cands[1]["score"] if len(cands) > 1 else 0.0
        strong_single = top["score"] >= STRONG_SCORE and top["score"] - second >= STRONG_GAP
        matched_keys = [h["key"] for h in cands if h["score"] >= 2.5]
        if strong_single and not compose_hint:
            fd = fault_names[top["key"]]
            turn = AdvisorTurn(
                reply="",  # 占位，规则模板稍后填
                intent="match_fault",
                fault_matches=[
                    {
                        "key": top["key"],
                        "name": fd.name,
                        "action": fd.action,
                        "level": fd.level,
                        "confidence": _confidence(top["score"]),
                        "matched_on": ",".join(top["matched_on"]),
                    }
                ],
                needs_clarification=False,
                matched_fault=top["key"],
                scenario_suggestions=_scenario_suggestions(m, top["key"]),
            )
            turn.suggested_steps = _compose_steps(m, [top["key"]]) if draft_steps else None
            turn.reply = _rule_reply(m, turn, goal)
        elif compose_hint and len(matched_keys) >= 1:
            # 编排意图：把规则命中的故障组装成可执行步骤草稿
            fd_list = [fault_names[k] for k in matched_keys if k in fault_names]
            steps = _compose_steps(m, matched_keys)
            turn = AdvisorTurn(
                reply="",
                intent="compose_scenario",
                fault_matches=[
                    {
                        "key": fd.key,
                        "name": fd.name,
                        "action": fd.action,
                        "level": fd.level,
                        "confidence": _confidence(3.0),
                        "matched_on": "rule",
                    }
                    for fd in fd_list
                ],
                suggested_steps=steps,
                needs_clarification=False,
                matched_fault=matched_keys[0] if len(matched_keys) == 1 else None,
            )
            turn.reply = _rule_reply(m, turn, goal)
        else:
            # 弱/多候选 → 澄清
            fm = []
            for h in cands[:5]:
                fd = fault_names[h["key"]]
                fm.append(
                    {
                        "key": h["key"],
                        "name": fd.name,
                        "action": fd.action,
                        "level": fd.level,
                        "confidence": _confidence(h["score"]),
                        "matched_on": ",".join(h["matched_on"]),
                    }
                )
            turn = AdvisorTurn(
                reply="",
                intent="clarify",
                fault_matches=fm,
                needs_clarification=True,
                followup_question="回复序号或故障名即可确认目标。",
            )
            turn.reply = _rule_reply(m, turn, goal)
        return _polish(m, turn, goal, use_llm, llm_chat)

    # ---- 2) 规则零候选 → 不 422 ----
    specific = _asset_specific_tokens(m)
    q_specific = _specific_tokens_in(goal, specific)
    has_intent_word = any(t in goal for t in _GENERIC_INTENT)

    if not q_specific:
        if not has_intent_word:
            # 与 TCMS 无关 → 友好引导回主题（绝不 422）
            return AdvisorTurn(
                reply=_rule_reply(m, AdvisorTurn(reply="", intent="out_of_domain"), goal),
                intent="out_of_domain",
                needs_clarification=True,
                followup_question="要不要试试说一个故障或场景，比如「车门故障」「超速降级」？",
            )
        # 有域意图词但无具体故障 → 自定义新故障引导流程
        turn = AdvisorTurn(
            reply="",
            intent="custom_proposal",
            needs_clarification=True,
            followup_question=(
                "请补充：① 故障怎么注入/发生在哪个部件；② 影响（速度/制动/车门/网络…）；"
                "③ 期望系统如何处置。"
            ),
        )
        turn.reply = _rule_reply(m, turn, goal)
        return _polish(m, turn, goal, use_llm, llm_chat)

    # 含具体域 token（门/弓/温/心跳…）→ RAG 语义澄清
    rag_cands, evidence = _rag_fault_candidates(m, retriever, goal)
    if rag_cands:
        turn = AdvisorTurn(
            reply="",
            intent="clarify",
            fault_matches=rag_cands,
            rag_evidence=evidence,
            needs_clarification=True,
            followup_question="上面哪个最接近你想说的？回复序号或故障名即可。",
        )
        turn.reply = _rule_reply(m, turn, goal)
        return _polish(m, turn, goal, use_llm, llm_chat)

    # RAG 也找不到强相关 → 自定义新故障流程（含证据展示，诚实）
    turn = AdvisorTurn(
        reply="",
        intent="custom_proposal",
        rag_evidence=evidence,
        needs_clarification=True,
        followup_question=(
            f"在 {len(m.faults_by_key)} 个真实故障里未找到匹配项，可能是未收录的新故障。"
            "请描述：① 怎么注入/发生在哪个部件；② 影响；③ 期望处置。据此组装草稿。"
        ),
    )
    turn.reply = _rule_reply(m, turn, goal)
    return _polish(m, turn, goal, use_llm, llm_chat)


def _polish(
    m: AssetModel,
    turn: AdvisorTurn,
    goal: str,
    use_llm: bool,
    llm_chat,
) -> AdvisorTurn:
    """有 key 时用 LLM 把规则回复润色成更自然的文案；失败落回规则（诚实降级）。"""
    if not use_llm:
        return turn
    from .llm_backend import LLMAgentBackend, _api_key

    if llm_chat is None:
        if _api_key() is None:
            return turn
        chat = LLMAgentBackend()._chat
    else:
        chat = llm_chat
    text = _llm_reply(m, turn, goal, chat)
    if text:
        turn.reply = text
        turn.llm_generated = True
    return turn
