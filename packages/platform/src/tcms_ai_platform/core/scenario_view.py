"""场景构成视图：把"这个场景到底注入了什么"讲清楚。

## 为什么需要这个模块

用户从某一个故障（例如"车门传感器偶发噪声"）点进去看动画，却发现动画里
注入了**三个**故障（SOC 偏低、电池温度偏高、车门传感器偶发噪声），
自然会产生疑问：是"偶发噪声需要这些前置故障"，还是"多余注入"？

答案是第三种，也是本模块要如实呈现的：**它们本来就是同一个场景里并列编排的步骤**
（`scenarios/soc_temp_door_noise.yaml` 的第 1/2/3 步），彼此**没有前置依赖**。
数据模型可以直接证明这一点——场景步骤是 `inject/recover + at` 的扁平序列，
没有任何字段表达"故障 A 需要故障 B 先发生"。

因此本模块不做推断，只做两件事：
1. 把场景里每个 inject 步骤**原样摊开**（故障键、等级、期望处置、现象、时刻）；
2. 标出哪一个是"入口故障"（用户点进来的那个），哪些是"同场景一并注入"。

由此，演示时被问"为什么有三个故障"可以直接指着屏幕回答，
而不是临场解释。对**所有**多故障场景通用，不是给某一个场景打的补丁。

## 两个数必须分开（本模块最容易搞错的地方）

- **注入次数**（`total_injections`）：场景里 inject 步骤的条数；
- **不同故障数**（`total_faults`）：去重后的故障键个数。

两者在"恢复后重发"类场景里并不相等——例如 `overspeed_reinject_repeat.yaml`
注入了 2 次却只涉及 1 个不同故障。若把注入次数当成"有几个故障"，
解释就会在这些场景上说错话。这个区分是被一条全库不变式测试逼出来的，
不是事先想到的。
"""

from __future__ import annotations

from typing import Any

#: 等级的中文说法（口径与故障字典一致）
LEVEL_LABEL = {
    "info": "信息",
    "minor": "轻微",
    "major": "严重",
    "critical": "致命",
}

#: 处置的中文说法
ACTION_LABEL = {
    "none": "无处置动作（仅提示）",
    "warning": "告警",
    "derate": "降级运行",
    "emergency_brake": "紧急制动",
    "shutdown": "停机",
}


def scenario_injections(sc_def: Any) -> list[dict]:
    """摊开一个场景的全部 inject 步骤（按注入时刻排序）。

    返回的每一项都是场景 YAML 里**原样写着**的字段，不做补全与推断；
    故障名与处置若能从故障字典取到就一并附上，取不到则留空。
    """
    if sc_def is None:
        return []
    out: list[dict] = []
    for st in getattr(sc_def, "steps", ()) or ():
        if getattr(st, "action", None) != "inject":
            continue
        out.append(
            {
                "fault": st.fault,
                "node": st.node,
                "level": st.level or "",
                "level_label": LEVEL_LABEL.get(st.level or "", st.level or ""),
                "expect": st.expect or "",
                "expect_label": ACTION_LABEL.get(st.expect or "", st.expect or ""),
                "impact": st.impact or "",
                "at": st.at,
            }
        )
    out.sort(key=lambda d: d["at"])
    return out


def scenario_recoveries(sc_def: Any) -> list[dict]:
    """摊开 recover 步骤（用于说明故障何时被恢复）。"""
    if sc_def is None:
        return []
    out = [
        {"fault": st.fault, "at": st.at}
        for st in (getattr(sc_def, "steps", ()) or ())
        if getattr(st, "action", None) == "recover"
    ]
    out.sort(key=lambda d: d["at"])
    return out


def _enrich(fault_key: str, model: Any) -> dict:
    """从故障字典补出故障名与系统处置（资产侧的事实，不编造）。"""
    if model is None or not fault_key:
        return {}
    f = None
    try:
        f = model.faults_by_key.get(fault_key)
    except Exception:  # noqa: BLE001
        f = None
    if f is None:
        return {}
    return {
        "fault_name": getattr(f, "name", "") or "",
        "action": getattr(f, "action", "") or "",
        "action_label": ACTION_LABEL.get(getattr(f, "action", "") or "", ""),
        "sil": getattr(f, "sil", "") or "",
        "desc": getattr(f, "desc", "") or "",
    }


def scenario_composition(sc_def: Any, model: Any = None, entry_fault: str | None = None) -> dict:
    """场景构成说明：注入了几个故障、各自角色、彼此关系。

    `entry_fault` 是用户点进来的那个故障键（可为 None）。
    返回结构直接给前端渲染"为什么有 N 个故障"的说明卡，无需前端再推断。
    """
    if sc_def is None:
        return {"available": False}

    injections = scenario_injections(sc_def)
    for item in injections:
        item.update(_enrich(item.get("fault") or "", model))
        item["role"] = "entry" if (entry_fault and item["fault"] == entry_fault) else "co"
        item["role_label"] = "你点的故障" if item["role"] == "entry" else "同场景一并注入"

    total_inj = len(injections)
    unique_keys: list[str] = []
    for i in injections:
        if i["fault"] not in unique_keys:
            unique_keys.append(i["fault"])
    total = len(unique_keys)

    counts: dict[str, int] = {}
    for i in injections:
        counts[i["fault"]] = counts.get(i["fault"], 0) + 1
    repeated = [
        {
            "fault": k,
            "times": v,
            "name": next((i.get("fault_name") or k for i in injections if i["fault"] == k), k),
        }
        for k, v in counts.items()
        if v > 1
    ]

    entry = next((i for i in injections if i["role"] == "entry"), None)
    unique_others = [k for k in unique_keys if not entry or k != entry["fault"]]

    has_entry = entry is not None
    if total <= 1 and total_inj <= 1:
        relation = "single"
        why = "本场景只注入一个故障。"
    elif total <= 1:
        relation = "repeat_single"
        detail = "、".join(f"{r['name']} ×{r['times']}" for r in repeated)
        why = (
            f"本场景只涉及 1 个故障，但被注入了 {total_inj} 次（{detail}）——"
            "这是「恢复后重发」型编排，用于验证故障反复出现时系统是否稳定处置，"
            "并不是有多个不同的故障。"
        )
    elif has_entry:
        relation = "flat_multi"
        why = (
            f"本场景共涉及 {total} 个不同故障（合计 {total_inj} 次注入），"
            f"你点进来的「{entry.get('fault_name') or entry['fault']}」是其中之一，"
            f"另外 {len(unique_others)} 个是同一场景编排里的并列步骤，"
            "不是它的前置条件——场景定义里没有任何字段表达故障之间的依赖关系。"
        )
    else:
        relation = "flat_multi"
        why = (
            f"本场景共涉及 {total} 个不同故障（合计 {total_inj} 次注入），"
            "它们是同一场景编排里的并列步骤，彼此没有前置依赖——"
            "场景定义里没有任何字段表达故障之间的依赖关系。"
        )

    # 一句可直接照读的演示口径（避免演示现场被问住）
    if total == 1 and total_inj > 1:
        r0 = repeated[0]
        oneliner = f"本场景只涉及 1 个故障「{r0['name']}」，被注入 {r0['times']} 次（恢复后重发）。"
    else:
        parts: list[str] = []
        for i in injections:
            nm = i.get("fault_name") or i["fault"]
            lv = i.get("level_label") or i.get("level") or "?"
            ex = i.get("expect_label") or i.get("expect") or "?"
            parts.append(f"{nm}（{lv}级，期望：{ex}）")
        head = f"本场景共 {total} 个故障"
        if total_inj != total:
            head += f"、{total_inj} 次注入"
        oneliner = head + "：" + "；".join(parts) + "。"

    return {
        "available": True,
        "scenario": getattr(sc_def, "file", ""),
        "scenario_name": getattr(sc_def, "name", ""),
        "desc": getattr(sc_def, "desc", "") or "",
        "total_faults": total,  # 不同故障数
        "total_injections": total_inj,  # 注入次数（"重发"场景下两者不同）
        "repeated": repeated,
        "entry_fault": entry_fault or "",
        "has_entry": has_entry,
        "relation": relation,
        "why": why,
        "oneliner": oneliner,
        "injections": injections,
        "recoveries": scenario_recoveries(sc_def),
        "is_multi": total > 1,
    }


__all__ = [
    "ACTION_LABEL",
    "LEVEL_LABEL",
    "scenario_composition",
    "scenario_injections",
    "scenario_recoveries",
]
