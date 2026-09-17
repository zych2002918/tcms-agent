"""Q3 原子组合器（Composer）：一句话多故障意图 → 原子化可执行计划 + 逐条溯源。

与 freeform（单故障）互补：freeform 解决"验证/处置一个故障"，composer 解决
"把这些故障组合成一个可执行场景"——同一套规则打分（锚定真实故障字典）提取
用户点名的一组故障，按序错峰编排注入/恢复，期望取字典默认 action
（真实引擎可判），并为每一步给出三栏式溯源（源资产 / 图谱系统 / Agent 建议）。

产品红线与 freeform 一致：组合只允许使用规则命中的**真实故障键**，
绝不发明故障；无任何命中 → ComposeError（调用方转 200 + ok=False 引导）。
"""

from __future__ import annotations

import re

from ..knowledge.vector import DOMAIN_ZH, subsystem_domain
from .advisor import _compose_steps

# 组合上限：一次编排最多组装 6 个故障（保持可读/可执行/可审计）
COMPOSE_CAP = 6
# 规则命中强度门槛：与 freeform 明确命中一致（name/key 3.0 起）
MIN_SCORE = 3.0

# 时序连接词/标点：用来把"先A，后B，随后C，最后D"切成原子子句
_CLAUDE_SPLIT = re.compile(r"[，,。；;\n]|先|再|然后|随后|接着|后来|继而|之后|最后|最后再")

# 收尾/期望类动作词（单独成句时 = 整链期望，不是故障）
_FINAL_ACTION_TERMS = {
    "emergency_brake": ("紧急制动", "紧急刹车", "立即制动", "紧急停车", " EB", "eb"),
    "derate": ("降级运行", "限速运行", "降级", "限制功率"),
    "shutdown": ("停机", "关断", "停运", "断电"),
    "warning": ("只告警", "仅告警", "报警即可", "告警"),
}


class ComposeError(ValueError):
    """组合目标里找不到任何真实故障（调用方转 200 + ok=False，不硬 422）。"""


def _mention_keys(m, goal: str) -> list[str]:
    """从目标文本提取规则命中的真实故障键（score≥3.0，候选已按得分/长度排序）。"""
    from .freeform import _score_candidates  # noqa: PLC0415 函数级避免循环

    cands = [h for h in _score_candidates(goal, list(m.faults_by_key.values())) if h["score"] >= MIN_SCORE]
    keys: list[str] = []
    for h in cands:
        if h["key"] not in keys:
            keys.append(h["key"])
    return keys


def build_plan(m, keys: list[str], goal: str = "") -> dict:
    """把一组真实故障键组装成可执行计划（steps 兼容 /api/run/custom）。

    溯源：每个故障给出 source_asset（字典字段）/ source_system（13 域标签）/
    source_agent（规则组合规划说明）/ example_scenarios（库内相似模板 ≤2）。
    """
    steps = _compose_steps(m, keys)
    provenance: list[dict] = []
    systems: set[str] = set()
    for fk in keys:
        fd = m.faults_by_key[fk]
        dom = subsystem_domain(fd.subsystem)
        systems.add(DOMAIN_ZH.get(dom, dom) if dom else "通用")
        examples = [
            s.name for s in m.scenarios.values()
            if fk in s.fault_keys
        ][:2]
        provenance.append(
            {
                "fault": fk,
                "name": fd.name,
                "subsystem": fd.subsystem,
                "system_zh": DOMAIN_ZH.get(dom, dom) if dom else "通用",
                "source_asset": {
                    "action": fd.action, "level": fd.level, "sil": fd.sil,
                    "desc": fd.desc, "detect": fd.detect,
                },
                "source_system": {"domain": dom or None, "zh": DOMAIN_ZH.get(dom, "通用") if dom else "通用"},
                "source_agent": "规则组合规划：按目标点名顺序错峰注入，期望=字典默认 action，恢复在全部注入后依次执行",
                "example_scenarios": examples,
            }
        )
    actions = sorted({m.faults_by_key[k].action for k in keys})
    return {
        "goal": goal,
        "faults": keys,
        "steps": steps,
        "provenance": provenance,
        "summary": {
            "fault_count": len(keys),
            "systems": sorted(systems),
            "actions": actions,
            "recover_after_inject": True,
        },
    }


def plan_compose(m, goal: str, max_faults: int = COMPOSE_CAP, history: list[str] | None = None) -> dict:
    """一句话多故障意图（可带多轮上下文）→ 可执行组合计划。

    history：前几轮用户输入（list[str]，按时间顺序）。规则路径先解析当前 goal；
    若未命中任何故障，则回看 history 里的点名（"再加上刚才那个"式续编）。
    未识别到任何真实故障时抛 ComposeError（诚实引导，不猜不造）。
    """
    goal = (goal or "").strip()
    if not goal:
        raise ComposeError("目标不能为空：请一句话点名要组合的故障（如「车门故障加超速级联」）。")

    keys = _mention_keys(m, goal)
    from_history: list[str] = []
    if not keys and history:
        for line in history:
            for k in _mention_keys(m, line or ""):
                if k not in from_history:
                    from_history.append(k)
        keys = from_history[:max_faults]
    else:
        keys = keys[:max_faults]

    if not keys:
        raise ComposeError(
            f"目标里未识别出任何真实故障（可用 {len(m.faults_by_key)} 条均未命中）：{goal!r}。"
            "请直接点名故障名（如：车门故障 / 超速 / 烟火报警）并用「组合/级联/编排」说明意图。"
        )
    plan = build_plan(m, keys, goal)
    if from_history:
        plan["summary"]["history_resolved"] = True
        plan["summary"]["history_faults"] = from_history
    return plan


# ---------------------------------------------------------------------------
# 时序连锁分句解析（v2）："先A后B随后C最后D" → 原子故障序列
# ---------------------------------------------------------------------------


def _split_clauses(goal: str) -> list[str]:
    """按时序连接词/标点切成子句，保留顺序；去掉空句。"""
    parts = [p.strip() for p in _CLAUDE_SPLIT.split(goal or "")]
    return [p for p in parts if p]


def _final_action_of(clause: str) -> str | None:
    """子句是否只表达了『收尾期望』（如"最后紧急制动"）→ 返回 action，否则 None。"""
    c = clause.lower()
    for action, terms in _FINAL_ACTION_TERMS.items():
        if any(t.lower() in c for t in terms):
            return action
    return None


def _clause_faults(m, clause: str) -> list[str]:
    """单句内规则命中（按出现顺序，≥MIN_SCORE）。"""
    from .freeform import _score_candidates  # noqa: PLC0415

    return [
        h["key"]
        for h in _score_candidates(clause, list(m.faults_by_key.values()))
        if h["score"] >= MIN_SCORE
    ]


def _domain_candidates_for(m, clause: str) -> dict | None:
    """未命中句子但有域词×故障句式 → 给出该域候选故障（供用户点选，不自动加入）。"""
    try:
        from ..knowledge.vague import analyze_vague

        return analyze_vague(m, clause)
    except Exception:  # noqa: BLE001
        return None


def plan_compose_seq(m, goal: str, max_faults: int = COMPOSE_CAP, picks: list[dict] | None = None) -> dict:
    """按"先…后…随后…最后…"逐原子子句解析，而不是只挑整句话里的第一个故障。

    picks：用户对未锚定子句的点选并入 [{clause, key}]——clause 必须是该句原文
    （与 unresolved.clause 一致），key 必须 ∈ 该句域候选的真实键；否则该子句
    保持未锚定（诚实门禁：不认任意键、不跨子句错位并入）。被并入的子句按它在
    句中的原始位置进 keys（保持时序），其余未锚定子句继续返回供用户逐个点选。

    返回 {keys(按时序), unresolved:[{clause, domain_candidates?}], final_action?,
    picked_count, goal}；一条真实故障都没锚定 → ComposeError（诚实引导）。
    """
    goal = (goal or "").strip()
    if not goal:
        raise ComposeError("目标不能为空：请一句话描述时序（如「先车门故障，后空调失效，随后牵引失效，最后紧急制动」）。")

    clauses = _split_clauses(goal)
    if not clauses:
        clauses = [goal]

    picks_by_clause: dict[str, str] = {}
    for p in picks or []:
        c = str(p.get("clause", "")).strip()
        if c:
            picks_by_clause[c] = str(p.get("key", ""))

    keys: list[str] = []
    unresolved: list[dict] = []
    final_action: str | None = None
    picked_count = 0
    for cl in clauses:
        hits = _clause_faults(m, cl)
        if hits:
            for k in hits:
                if k not in keys:
                    keys.append(k)
            continue
        act = _final_action_of(cl)
        if act:
            final_action = act  # 收尾期望（对整链，非某个故障）
            continue
        # 未命中 → 若像"设备+坏了/失效"则给出域候选（诚实让用户点选，不自动塞）
        dc = _domain_candidates_for(m, cl)
        allowed = {f["key"] for f in (dc or {}).get("faults", [])} if dc else set()
        pk = picks_by_clause.get(cl)
        if pk and pk in allowed:  # 只认该子句域候选里的真实键（跨子句/任意键一律不并入）
            if pk not in keys:
                keys.append(pk)
            picked_count += 1
            continue
        unresolved.append({"clause": cl, "domain_candidates": dc})
    if not keys:
        raise ComposeError(
            f"按时序逐句都没识别出真实故障（可用 {len(m.faults_by_key)} 条均未命中）：{goal!r}。"
            "请直接点名故障键（如 车门故障/超速/烟火报警），可逐句原子化编排。"
        )
    keys = keys[:max_faults]
    out: dict = {
        "goal": goal,
        "keys": keys,
        "unresolved": unresolved,
        "final_action": final_action,
        "picked_count": picked_count,
    }
    return out
