"""自由 Agent 目标解析（Freeform Goal → 任务）——像 DSH Harness 一样自然语言驱动。

把用户的自然语言目标（如「验证车门故障不能发车」）解析为 Harness 可执行任务：
    goal_text → (target_fault, expected_action)

设计（规则优先 + LLM 兜底决策）：
    - 规则解析：锚定真实资产（故障字典 key/name/desc 全字段匹配），处置词表
      把口语处置映射到规范 action（emergency_brake/derate/shutdown/warning/none）。
      命中故障键与期望处置后输出置信度 confidence（0~1，可解释）。
    - LLM 决策：仅当 llm_available() 且规则解析歧义（多个候选故障/候选处置）
      时才请求 LLM 仲裁（复用 llm_backend 的 OpenAI 兼容通道，含自动降级）；
      无 key 或请求失败 → 规则解析结果直接可用（诚实标注 resolver="rule"）。
    - 解析出的任务用临时 TaskDef（task_id=f"T-FREE-{n}"）交给 AgentHarness.run_task，
      与内置任务共用同一套真实执行 + 评分闭环。

诚实纪律（与全仓库一致）：
    - 解析结果必须能指回故障字典真实条目（故障名/处置 action 全部来自 faults.yaml）；
    - 无任何故障键命中 → 抛出 NoFaultMatch（调用方转 422 中文提示），不猜。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import AssetModel
from .tasks import TaskDef

# 处置词表：口语处置表达 → 规范 action（与故障字典 action 枚举对齐）
_ACTION_TERMS: dict[str, tuple[str, ...]] = {
    "emergency_brake": ("紧急制动", "急刹", "紧急停车", "立即制动", "紧急刹车", "eb", "emergency"),
    "derate": ("降级", "限速", "降功率", "降载", "限功率", "derate", "限力"),
    "shutdown": ("停车", "断电", "下电", "关断", "隔离", "shutdown"),
    "warning": ("告警", "报警", "警告", "提示", "warning", "警示"),
    "none": ("仅记录", "不处置", "none", "记录"),
}
# 规范 action 的中文短名（响应展示用）
_ACTION_ZH = {
    "emergency_brake": "紧急制动",
    "derate": "降级",
    "shutdown": "停车/断电",
    "warning": "告警",
    "none": "仅记录",
}


@dataclass(frozen=True)
class FreeParse:
    """一次自由目标解析结果（全部字段可序列化）。"""

    fault: str  # 命中故障键（真实 faults.yaml key）
    fault_name: str  # 故障中文名（资产派生）
    expected: str  # 期望处置（规范 action）
    expected_zh: str  # 处置中文名
    confidence: float  # 0~1（规则命中强度；LLM 仲裁成功给 1.0）
    resolver: str  # rule(确定性) / llm(LLM 仲裁)
    matched_on: str  # 命中依据（key/name/desc 等，可解释）

    def to_task(self, goal: str, seq: int) -> TaskDef:
        """把解析结果打包成临时任务（T-FREE-{seq}）交给 Harness 执行。"""
        return TaskDef(
            task_id=f"T-FREE-{seq}",
            title=f"自由目标：{self.fault_name} → {self.expected_zh}",
            goal=goal,
            target_fault=self.fault,
            expected_action=self.expected,
            kb_query=goal,  # 自由任务用整句做 RAG 检索（证据链）
        )


class NoFaultMatch(ValueError):
    """自然语言目标里找不到任何故障字典条目（调用方转 422）。"""


def _norm(s: str) -> str:
    """小写 + 去空白（比较用，保留中文原样）。"""
    return "".join(s.lower().split())


def _name_base(name: str) -> str:
    """去掉中文名括号后缀，取主体（如「车门故障（按未关处理）」→「车门故障」）。"""
    return name.split("（", 1)[0].strip()


def _strip_latin(s: str) -> str:
    """剥掉名称/文本里的纯拉丁片段（英文 key、单位等），只留中文主体。
    例：「VCU 心跳丢失」→「 心跳丢失」→ 规整后「心跳丢失」。
    中文名才是用户口语的自然锚点；英文部分已由 key 命中单独覆盖。"""
    import re

    return re.sub(r"[A-Za-z0-9_]+", "", s)


def _bigram_overlap(text_a: str, text_b: str) -> float:
    """两文本字符 2-gram 交集覆盖率（容忍语序/插词，如「CRC报文校验错误」vs
    「报文 CRC 校验错误」）。返回 a 中 bigram 被 b 覆盖的比例（0~1）。"""
    a = _norm(text_a)
    b = _norm(text_b)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ba = {a[i : i + 2] for i in range(len(a) - 1)}
    bb = {b[i : i + 2] for i in range(len(b) - 1)}
    if not ba:
        return 0.0
    return len(ba & bb) / len(ba)


def _score_candidates(goal: str, faults: list) -> list[dict]:
    """对全部故障打分：字段命中 + 子串强度 → [(fault_key, score, matched_on, name)]。

    故障匹配在**原始目标**上进行（不做处置词预处理，避免误伤含处置词的
    故障名如「紧急制动执行失败」）；处置词只参与期望动作提取。
    score = 命中字段数 × 权重 + 子串覆盖率；只返回命中项（>0）。
    """
    goal_n = _norm(goal)
    goal_zh = _norm(_strip_latin(goal))  # 中文主体（用户口语的自然锚点）
    hits = []
    for f in faults:
        score = 0.0
        matched_on = []
        spec_len = 0  # 命中的名称串长度（平局时更长=更具体者优先，见排序键）
        # 键命中权重最高（键是场景 YAML 锚点；英文目标可直接命中 key）
        if f.key and _norm(f.key) in goal_n:
            score += 4.0
            matched_on.append("key")
        # 中文名整体或主体（去括号/去拉丁前缀）命中
        name_zh = _norm(_strip_latin(f.name))
        base_zh = _norm(_strip_latin(_name_base(f.name) or f.name))
        if name_zh and name_zh in goal_zh:
            score += 3.0
            matched_on.append("name")
            spec_len = max(spec_len, len(name_zh))
        elif base_zh and base_zh in goal_zh:
            score += 3.0
            matched_on.append("name")
            spec_len = max(spec_len, len(base_zh))
        # 名称中文 bigram 覆盖 ≥ 0.6 → 语义近似命中（容忍语序/插词；阈值防误伤）
        elif base_zh and _bigram_overlap(goal_zh, base_zh) >= 0.6:
            score += 2.5
            matched_on.append("name")
            spec_len = max(spec_len, len(base_zh))
        # 描述字段子串命中（降权防误伤）
        for field, weight, label in (
            ("desc", 2.0, "desc"),
            ("detect", 1.0, "detect"),
            ("inject", 0.5, "inject"),
            ("recovery", 0.5, "recovery"),
        ):
            v = getattr(f, field, None) or ""
            for tok in (v[:24], v):  # 先试描述前 24 字，再整段
                if tok and tok in goal:
                    score += weight
                    matched_on.append(label)
                    break
        if score > 0:
            hits.append(
                {
                    "key": f.key,
                    "name": f.name,
                    "action": f.action,
                    "score": round(score, 3),
                    "spec_len": spec_len,  # 名称串长度：同分时更长者=更具体（后车门故障 > 车门故障）
                    "matched_on": sorted(set(matched_on)),
                }
            )
    # 同分平局倾向"更长更具体"的名称命中，保证确定性（多区烟火报警 > 烟火报警）
    hits.sort(key=lambda h: (-h["score"], -h["spec_len"]))
    return hits


def _expected_action(goal: str, fallback: str, faults: list) -> tuple[str, float]:
    """从目标里提取期望处置（规范 action + 置信度）。

    处置词命中 → action；否则用最可能故障的字典默认处置（置信度降档）。
    """
    for action in ("emergency_brake", "derate", "shutdown", "warning", "none"):
        for t in _ACTION_TERMS[action]:
            if t in goal:
                return action, 1.0
    # 无显式处置词 → 回退字典默认（语义：验证该故障必须触发其默认处置）
    return fallback, 0.7


def parse_free_goal(
    model: AssetModel,
    goal: str,
    seq: int = 1,
    use_llm: bool = True,
    llm_chat=None,
    llm_context: str | None = None,
) -> FreeParse:
    """把自然语言目标解析为 (fault, expected) 的确定性/LLM 混合解析。

    参数：
        model   —— 资产模型（故障字典真实来源）
        goal    —— 用户自然语言目标
        seq     —— 临时任务序号（T-FREE-{seq}）
        use_llm —— 是否允许 LLM 仲裁歧义（调用方按 llm_available() 传入）
        llm_chat—— 注入式 chat(system,user)->str（测试替身；缺省用 LLMAgentBackend）
        llm_context —— 可选检索证据/多轮上下文（仅注入 LLM 消歧提示；不进规则
                       路径——规则零候选仍抛 NoFaultMatch，红线不变）
    """
    goal = (goal or "").strip()
    if not goal:
        raise NoFaultMatch("目标不能为空")

    faults = list(model.faults_by_key.values())
    cands = _score_candidates(goal, faults)

    # 一、明确命中：唯一高分故障 → 确定性规则解析
    if cands:
        top = cands[0]
        second_score = cands[1]["score"] if len(cands) > 1 else 0.0
        if top["score"] >= 3.0 and top["score"] - second_score >= 1.0:
            expected, _conf_e = _expected_action(goal, top["action"], faults)
            confidence = round(0.9 + 0.1 * min(1.0, top["score"] / 6.0), 3)
            return FreeParse(
                fault=top["key"],
                fault_name=top["name"],
                expected=expected,
                expected_zh=_ACTION_ZH.get(expected, expected),
                confidence=confidence,
                resolver="rule",
                matched_on=",".join(top["matched_on"]),
            )

    # 二、规则零候选 → 目标里没有任何真实故障语义 → 直接未命中。
    # 产品红线：LLM 仲裁只允许「从规则候选里消歧」，不允许自由发明故障；
    # 无关目标（如「今天天气不错」）即使 LLM 可用也必须 422，不得硬猜真实键。
    if not cands:
        raise NoFaultMatch(
            f"未在目标里识别出任何故障（可用的 {len(faults)} 个真实故障均未命中）：{goal!r}"
        )

    # 三、规则弱/多候选歧义 → LLM 仲裁（仅当可用，从候选里挑一个）；失败落回规则兜底
    if use_llm:
        parsed = _llm_disambiguate(model, goal, cands, llm_chat, context=llm_context)
        if parsed is not None:
            return parsed

    # 四、规则兜底：取最高分（置信度按差距打折）
    top = cands[0]
    expected, _conf_e = _expected_action(goal, top["action"], faults)
    gap = top["score"] - (cands[1]["score"] if len(cands) > 1 else 0.0)
    confidence = round(min(0.85, 0.55 + 0.1 * top["score"] + 0.1 * gap), 3)
    return FreeParse(
        fault=top["key"],
        fault_name=top["name"],
        expected=expected,
        expected_zh=_ACTION_ZH.get(expected, expected),
        confidence=confidence,
        resolver="rule",
        matched_on=",".join(top["matched_on"]),
    )


def _llm_disambiguate(
    model: AssetModel, goal: str, cands: list[dict], llm_chat=None, context: str | None = None
) -> FreeParse | None:
    """LLM 仲裁：从规则候选里选一个故障 + 期望处置。失败返回 None（落回规则兜底）。

    产品红线：LLM 只允许在 cands（规则已命中真实故障语义的候选）里挑，
    不允许自由发明/硬猜真实键——cands 为空时调用方（parse_free_goal）已抛
    NoFaultMatch，本函数只处理歧义消解。llm_chat 注入（测试/演示替身）时
    跳过 key 门禁；否则须 llm_available()（无 key 直接放弃仲裁，避免空等）。
    """
    from .llm_backend import LLMAgentBackend, _api_key

    if llm_chat is None and _api_key() is None:
        return None
    candidate_keys = {h["key"] for h in cands}
    rows = cands[:8]
    faults_txt = "\n".join(
        f"- {h['key']}（{h['name']}，默认处置 {h['action']}）" for h in rows
    )
    system = (
        "你是 TCMS 列车控制测试的语义解析器。把用户的一句话测试目标映射到"
        "故障字典条目（key）与期望处置动作（emergency_brake/derate/shutdown/warning/none）。"
        "只能选候选列表里的 key，处置必须是五种动作之一。"
        "若目标与任何候选都不匹配，输出 {\"fault\": null} 表示无法识别。"
        "只输出 JSON：{\"fault\": \"<key>\" 或 null, \"expected\": \"<action>\"}。"
    )
    user = f"用户目标：{goal}\n\n候选故障（规则已命中部分语义，请消歧）：\n{faults_txt}"
    if context:
        user += f"\n\n知识上下文（检索证据，仅辅助消歧，不许超出候选键）：\n{context[:400]}"
    if llm_chat is not None:
        text = llm_chat(system, user)
    else:
        text = LLMAgentBackend()._chat(system, user)
    if not text:
        return None
    import json

    try:
        start = text.find("{")
        obj = json.loads(text[start : text.rfind("}") + 1])
        fk_raw = obj.get("fault")
        fk = str(fk_raw).strip() if fk_raw is not None else ""
        exp = str(obj.get("expected", "")).strip()
    except Exception:  # noqa: BLE001 - LLM 输出非 JSON → 落回规则兜底
        return None
    # 强校验：LLM 输出必须 ∈ 规则候选键（真实故障集合的子集），否则视为无法识别
    if fk not in candidate_keys or exp not in _ACTION_ZH:
        return None
    fd = model.faults_by_key[fk]
    return FreeParse(
        fault=fk,
        fault_name=fd.name,
        expected=exp,
        expected_zh=_ACTION_ZH.get(exp, exp),
        confidence=1.0,
        resolver="llm",
        matched_on="llm_decision",
    )
