"""离线宽泛问法推理（vague-query）：域词 × 故障句式 → 定向推荐该域真实故障/复现场景。

为什么需要它：用户不会总说准确的症状/故障名（"空调坏了""门打不开"）。症状资产覆盖
有限、精确解析可能零命中；此时若只回"请补充细节"，体验是死路。本模块提供**确定性、
不发明**的通用兜底：
1. 用 13 域词表（与检索路由同源 `_DOMAIN_TERMS`）判 domain；
2. 用宽泛故障句式表（坏了/失灵/不工作/打不开…）判"是设备出问题，不是闲聊"；
3. 两者都命中 → 在该域真实故障里，按「有复现场景的优先 → 处置严重度 → 等级」排序，
   给 top 故障 + top 场景，全部真实键、可点选继续（跳图谱/FaultLab/发起 Agent 查证）。

红线：域外/闲聊（无域词或无故障句式）→ 返回 None，绝不硬推荐。
"""

from __future__ import annotations

from .retriever import _DOMAIN_TERMS
from .vector import DOMAIN_ZH, subsystem_domain

# 宽泛"设备坏了"句式（避免把普通陈述/闲聊误判成故障）
_BROKEN_PHRASES = (
    "坏了",
    "失灵",
    "失效",
    "不工作",
    "不干活",
    "不动了",
    "打不开",
    "开不了",
    "关不上",
    "没反应",
    "没动静",
    "不亮",
    "不制冷",
    "不制热",
    "不吹",
    "不出风",
    "停了",
    "罢工",
    "烧了",
    "有问题",
    "出毛病",
    "不好使",
    "不行",
)

# 处置严重度（推荐排序用：越需要立即处理越靠前）
_ACTION_SEV = {"emergency_brake": 0, "shutdown": 1, "derate": 2, "warning": 3, "none": 4, "": 5}
_LEVEL_SEV = {"critical": 0, "major": 1, "minor": 2, "info": 3, "": 4}


def _match_domain(text: str) -> tuple[str, int] | None:
    """命中域：按 13 域词表计命中词数取最高；无命中 → None。"""
    t = (text or "").lower()
    best: tuple[str, int] | None = None
    for dom, terms in _DOMAIN_TERMS.items():
        hits = sum(1 for term in terms if term.lower() in t)
        if hits > 0 and (best is None or hits > best[1]):
            best = (dom, hits)
    return best


def _has_broken_phrase(text: str) -> bool:
    t = (text or "")
    return any(p in t for p in _BROKEN_PHRASES)


def analyze_vague(m, text: str, top_faults: int = 4, top_scenarios: int = 2) -> dict | None:
    """主入口：宽泛问法 → 定向推荐；不适用返回 None（留给诚实 no_match 引导）。"""
    t = (text or "").strip()
    if not t:
        return None
    dom = _match_domain(t)
    if dom is None:
        return None
    domain, _ = dom
    if not _has_broken_phrase(t):
        return None

    # 该域真实故障（子系统域标签 = 路由域）
    faults: list = []
    scen_of: dict[str, int] = {}
    for s in m.scenarios.values():
        for k in s.fault_keys:
            scen_of[k] = scen_of.get(k, 0) + 1
    for f in m.faults_by_key.values():
        if subsystem_domain(f.subsystem) != domain:
            continue
        faults.append(
            {
                "key": f.key,
                "name": f.name,
                "level": f.level,
                "action": f.action,
                "scenario_count": scen_of.get(f.key, 0),
            }
        )
    if not faults:
        return None

    faults.sort(
        key=lambda x: (
            -x["scenario_count"],
            _ACTION_SEV.get(str(x["action"]), 5),
            _LEVEL_SEV.get(str(x["level"]), 4),
            x["key"],
        )
    )
    shown_faults = faults[:top_faults]

    # 该域复现场景（覆盖上面推荐故障的优先，其次按场景归属）
    wanted_keys = {f["key"] for f in shown_faults}
    scen_list: list[dict] = []
    seen_files: set[str] = set()
    for s in m.scenarios.values():
        if len(scen_list) >= top_scenarios:
            break
        if s.file in seen_files:
            continue
        if s.fault_keys & wanted_keys:
            scen_list.append({"file": s.file, "name": s.name})
            seen_files.add(s.file)
    for s in m.scenarios.values():  # 兜底：按故障归属补足
        if len(scen_list) >= top_scenarios:
            break
        if s.file in seen_files:
            continue
        if any(k in scen_of for k in s.fault_keys):
            scen_list.append({"file": s.file, "name": s.name})
            seen_files.add(s.file)

    zh = DOMAIN_ZH.get(domain, domain)
    lines = [
        f"按字面理解，这像是「{zh}」里的设备出了问题（域词 × 故障句式，确定性规则，未用 LLM）。",
        "真实故障字典里、有复现场景可验证的优先列出（可点选继续）：",
    ]
    for f in shown_faults:
        lines.append(
            f"  · {f['name']}（{f['key']}，等级 {f['level']} · 处置 {f['action']}"
            + (f" · {f['scenario_count']} 个场景" if f["scenario_count"] else "")
            + "）"
        )
    if scen_list:
        lines.append("复现场景（可 ▶ FaultLab 播放）：" + "、".join(f"{s['name']}({s['file']})" for s in scen_list))
    lines.append("若这不是你要的部位，请补充：哪个设备/什么现象/伴随哪些告警。")

    return {
        "kind": "vague_domain",
        "domain": domain,
        "domain_zh": zh,
        "reply": "\n".join(lines),
        "faults": [
            {"key": f["key"], "name": f["name"], "level": f["level"], "action": f["action"]}
            for f in shown_faults
        ],
        "scenarios": scen_list,
        "count": len(shown_faults),
    }
