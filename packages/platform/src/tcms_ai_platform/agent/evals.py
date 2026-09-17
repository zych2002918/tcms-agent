"""Agent 评测（P1-3）：把检索 golden 的“防回退门禁”心智复用到 Agent 行为。

覆盖三类：
1. diagnose（症状诊断）：候选命中预期真实故障（any-of）/ 无匹配场景必须诚实 no_match；
   并机器校验“不编造” —— 候选 fault 必须 ∈ 真实故障字典。
2. free 解析（自由目标）：确定性规则解析出的 fault/action 与 golden 一致；
   预期无匹配时必须 NoFaultMatch（引导，不硬答）。
3. harness 结果指标（自愈/证据）：复用 TaskRun.score 语义对批量运行结果做摘要
   （pass_rate / self_healed / evidence_used / radar 均值）——与既有自证口径一致。

数据：domain/data/agent_golden.yaml（期望值全部来自真实资产）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .diagnoser import diagnose_symptom

GOLDEN_FILE = Path(__file__).resolve().parent.parent / "domain" / "data" / "agent_golden.yaml"
ADVERSARIAL_FILE = (
    Path(__file__).resolve().parent.parent / "domain" / "data" / "diagnose_adversarial.yaml"
)


def load_golden() -> list[dict]:
    if not GOLDEN_FILE.is_file():
        return []
    data = yaml.safe_load(GOLDEN_FILE.read_text(encoding="utf-8"))
    return list(data.get("tasks", []))


def evaluate_diagnose(
    m,
    graph,
    retriever,
    tasks: list[dict] | None = None,
) -> dict:
    """诊断 golden：命中预期 / 无匹配诚实 / 候选不编造（全字典校验）。"""
    tasks = tasks or [t for t in load_golden() if t.get("kind") == "diagnose"]
    rows = []
    passed = 0
    for t in tasks:
        r = diagnose_symptom(m, graph, retriever, t["q"], depth=3)
        candidates = r.get("candidates", [])
        # 不编造：所有候选必须是真实故障键
        fabricated = [c["fault"] for c in candidates if c["fault"] not in m.faults_by_key]
        if t.get("expect_no_match"):
            ok = (r.get("no_match") is True) and not candidates and not fabricated
        else:
            keys = {c["fault"] for c in candidates}
            ok = bool(keys & set(t.get("expect_faults", []))) and not fabricated
        if ok:
            passed += 1
        rows.append(
            {
                "q": t["q"],
                "expect": t.get("expect_faults", []) or (["<no_match>"] if t.get("expect_no_match") else []),
                "matched": r.get("matched"),
                "keys": sorted(keys),
                "fabricated": fabricated,
                "pass": ok,
            }
        )
    total = len(tasks)
    return {"total": total, "passed": passed, "pass_rate": round(passed / total, 3) if total else 0.0, "rows": rows}


def evaluate_free_parse(m, tasks: list[dict] | None = None) -> dict:
    """自由目标解析 golden（确定性规则，不开 LLM）：解析出 fault/action 或 NoFaultMatch。"""
    from .freeform import NoFaultMatch, parse_free_goal

    tasks = tasks or [t for t in load_golden() if t.get("kind") == "free"]
    rows = []
    passed = 0
    for t in tasks:
        if t.get("expect_no_match"):
            try:
                parse_free_goal(m, t["q"], seq=1, use_llm=False)
                ok = False
                fault, action = "", ""
            except NoFaultMatch:
                ok = True
                fault, action = "", ""
        else:
            try:
                parsed = parse_free_goal(m, t["q"], seq=1, use_llm=False)
                fault, action = parsed.fault, parsed.expected
                ok = fault == t.get("expect_fault") and action == t.get("expect_action")
            except NoFaultMatch:
                ok = False
                fault, action = "<no_match>", ""
        if ok:
            passed += 1
        rows.append({"q": t["q"], "expect": f"{t.get('expect_fault')}/{t.get('expect_action')}", "got": f"{fault}/{action}", "pass": ok})
    total = len(tasks)
    return {"total": total, "passed": passed, "pass_rate": round(passed / total, 3) if total else 0.0, "rows": rows}


def load_adversarial() -> list[dict]:
    """读取诊断对抗集（P1-3，失败样本）。"""
    if not ADVERSARIAL_FILE.is_file():
        return []
    data = yaml.safe_load(ADVERSARIAL_FILE.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


def evaluate_adversarial(
    m,
    graph,
    retriever,
    entries: list[dict] | None = None,
) -> dict:
    """诊断对抗集门禁（P1-3 诚实与失败的机器锁）。

    对每条对抗输入跑 diagnose_symptom，机器校验三类不变量：
    - **不编造**：候选 fault 必须 ∈ 真实故障字典（全条目通用）；
    - **expect_no_match**：必须 no_match=True + 零候选 + 回复带诚实引导
      （补充/把握/为空/描述）——不得从"没把握"变成"硬答"；
    - **expect symptom**：必须命中该症状资产，且候选域 ⊆ 症状声明域
      （不得错域、不得输出声明域外的候选）。
    数据：domain/data/diagnose_adversarial.yaml（[P1-3-fix] 条目先证旧实现会错
    才纳入——防橡皮图章）。
    """
    entries = entries if entries is not None else load_adversarial()
    rows = []
    passed = 0
    for e in entries:
        q = e.get("q", "")
        r = diagnose_symptom(m, graph, retriever, q, depth=3)
        candidates = r.get("candidates", [])
        fabricated = [c["fault"] for c in candidates if c["fault"] not in m.faults_by_key]
        sym = r.get("symptom") or {}
        sym_key = sym.get("key")
        cand_domains = {c["domain"] for c in candidates}
        declared = set(sym.get("domains") or [])
        reasons: list[str] = []
        ok = not fabricated
        if fabricated:
            reasons.append(f"编造故障键: {fabricated}")
        if e.get("expect_no_match"):
            if r.get("no_match") is not True:
                ok = False
                reasons.append(f"应 no_match 却匹配 symptom={sym_key!r}")
            if candidates:
                ok = False
                reasons.append(f"no_match 却输出候选: {[c['fault'] for c in candidates]}")
            reply = str(r.get("reply", ""))
            if not any(k in reply for k in ("补充", "把握", "为空", "请描述")):
                ok = False
                reasons.append("no_match 回复缺诚实引导语（补充/把握）")
        else:
            exp = e.get("symptom")
            if r.get("no_match") is not False or not candidates:
                ok = False
                reasons.append(f"未命中: symptom={sym_key!r}")
            elif sym_key != exp:
                ok = False
                reasons.append(f"命中 {sym_key!r} ≠ 期望 {exp!r}")
            leaked = cand_domains - declared
            if leaked:
                ok = False
                reasons.append(f"错域: {sorted(leaked)} 超出声明域 {sorted(declared)}")
        if ok:
            passed += 1
        rows.append(
            {
                "q": q,
                "category": e.get("category", ""),
                "expect": e.get("symptom") or "<no_match>",
                "matched_key": sym_key,
                "no_match": r.get("no_match"),
                "domains": sorted(cand_domains),
                "fabricated": fabricated,
                "reasons": reasons,
                "pass": ok,
            }
        )
    total = len(entries)
    return {"total": total, "passed": passed, "pass_rate": round(passed / total, 3) if total else 0.0, "rows": rows}


def summarize_agent_results(runs: list[dict]) -> dict:
    """Harness 批量运行结果 → 摘要指标（与 TaskRun.score 口径一致：达成/证据/执行/反思）。

    runs 元素至少含 {achieved, reflected, evidence(list|None), score:{...}}。
    用于“Agent 评测复用”：任何 Agent 链路（任务库/自由目标/诊断）都能产出同一套摘要。
    """
    n = len(runs)
    achieved = sum(1 for r in runs if r.get("achieved"))
    healed = sum(1 for r in runs if r.get("reflected") and r.get("achieved"))
    evidence_used = sum(1 for r in runs if r.get("evidence"))
    radar_keys = ["goal_achieved", "evidence_used", "exec_pass", "reflection"]
    radar_means = {
        k: round(sum((r.get("score") or {}).get("radar", {}).get(k, 0) for r in runs) / n, 1) if n else 0.0
        for k in radar_keys
    }
    return {
        "total": n,
        "achieved": achieved,
        "pass_rate": round(achieved / n, 3) if n else 0.0,
        "self_healed": healed,
        "evidence_used_runs": evidence_used,
        "radar_mean": radar_means,
    }
