"""反思 Harness（P4-B）：让生成器「失败了自己改」——AI 测开的闭环层。

背景：管线目前是「生成 → 执行 → 报告」一次性。真 LLM 会犯错（如幻觉
AlarmLevel=-1 被真实执行器拦下），但没有修正环节。reflect_loop 给生成器
第二次机会：失败 → 分类 → 带证据修正 → 重跑，量化 self-heal_rate。

设计（采纳对抗审查，docs/decisions.md D6）：
    * 失败分类：
        class1 域值幻觉（EncodeError 值越界/非法枚举）——oracle 事实可修；
        class2 断言错配（断言与真实解码语义不符）——RAG 上游金标证据可修；
        class3 目标不支持（未知报文/信号/故障键）——无正确语义，计 replaced
              不算 healed（RAG 零命中是判据之一）。
    * diff 门禁（防「换说法假装自愈」）：修正后的 execution 必须与失败版
      实质不同（kind/op/args 任一变化），且断言数不减少；否则计 unhealed。
    * 状态机指标（逐例）：
        PASS→PASS        稳定通过
        FAIL→PASS        healed（自愈）
        FAIL→FAIL(同因)   not-progressed
        FAIL→FAIL(异因)   regressed/异因
      self-heal_rate = healed / (healed + not-progressed + 异因)
    * 修复器两种实现：
        - MockReflector：确定性「修复器」，模拟 LLM 在证据充足时修正
          （注入已知可修失败验证闭环机制，离线可复现）；
        - LLMReflector：真 LLM 修正（demo 用，1-2 批）。
    * 证据注入：oracle 事实（信号枚举/范围/raw-vs-decoded）+ RAG 金标片段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from tcms_ai_testgen.execution import ExecutionIntent
from tcms_ai_testgen.executor_real import run_real
from tcms_ai_testgen.models import GeneratedCase

# ---------------------------------------------------------------------------
# 失败分类
# ---------------------------------------------------------------------------


@dataclass
class FailureInfo:
    kind: str  # class1 | class2 | class3 | unknown
    reason: str = ""
    signal: Optional[str] = None
    message: Optional[str] = None


_ENCODE_ERR_RE = re.compile(
    r'Expected signal "([A-Za-z0-9_]+)" value (?:greater|less).*?in message "([A-Za-z0-9_]+)"', re.S
)


def classify_failure(case: GeneratedCase, stdout: str) -> FailureInfo:
    """从真实执行输出分类失败原因（只看失败用例相关段）。

    简化实现：EncodeError 域值 → class1；断言 AssertionError（值不匹配）→
    class2（缺上游语义证据）；其它 → unknown。
    """
    m = _ENCODE_ERR_RE.search(stdout)
    if m:
        return FailureInfo(kind="class1", reason="域值幻觉(EncodeError)",
                           signal=m.group(1), message=m.group(2))
    if "AssertionError" in stdout:
        return FailureInfo(kind="class2", reason="断言错配(AssertionError)")
    return FailureInfo(kind="unknown", reason="未分类失败")


# ---------------------------------------------------------------------------
# diff 门禁
# ---------------------------------------------------------------------------


def execution_diff(a: Optional[ExecutionIntent], b: Optional[ExecutionIntent]) -> bool:
    """新旧 execution 是否实质不同（防「换说法假装自愈」）。

    判定实质变化：kind / setup-op序列 / expect-op序列 / fault 任一不同，
    且 b 的断言数 >= a 的断言数（不得删断言）。None 视为全不同。
    """
    if a is None or b is None:
        return a is not b
    if a.kind != b.kind or a.fault != b.fault:
        return True
    a_ops = [(s.op, tuple(sorted(s.args.items()))) for s in a.setup + a.expect]
    b_ops = [(s.op, tuple(sorted(s.args.items()))) for s in b.setup + b.expect]
    if a_ops == b_ops:
        return False
    # 断言数不得减少
    if len(b.expect) < len(a.expect):
        return False
    return True


# ---------------------------------------------------------------------------
# 状态机 / 报告
# ---------------------------------------------------------------------------


@dataclass
class CaseOutcome:
    name: str
    round1: str  # PASS | FAIL
    round2: str  # PASS | FAIL | SKIP(未重跑)
    transition: str  # stable | healed | not_progressed | regressed | skipped
    failure_kind: str = ""


def _transition(r1: str, r2: Optional[str]) -> str:
    if r2 is None:
        return "skipped"
    if r1 == "PASS" and r2 == "PASS":
        return "stable"
    if r1 == "FAIL" and r2 == "PASS":
        return "healed"
    if r1 == "FAIL" and r2 == "FAIL":
        return "not_progressed"
    return "regressed"


@dataclass
class ReflectReport:
    total: int
    outcomes: list[CaseOutcome] = field(default_factory=list)
    stdout_round1: str = ""
    stdout_round2: str = ""

    @property
    def healed(self) -> int:
        return sum(1 for o in self.outcomes if o.transition == "healed")

    @property
    def stable(self) -> int:
        return sum(1 for o in self.outcomes if o.transition == "stable")

    @property
    def not_progressed(self) -> int:
        return sum(1 for o in self.outcomes if o.transition == "not_progressed")

    @property
    def self_heal_rate(self) -> float:
        """自愈率 = healed / 失败用例中尝试修复的（healed+not_progressed）。"""
        denom = self.healed + self.not_progressed
        return self.healed / denom if denom else 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "healed": self.healed,
            "stable": self.stable,
            "not_progressed": self.not_progressed,
            "self_heal_rate": round(self.self_heal_rate, 3),
            "outcomes": [
                {"name": o.name, "r1": o.round1, "r2": o.round2,
                 "transition": o.transition, "kind": o.failure_kind}
                for o in self.outcomes
            ],
        }


# ---------------------------------------------------------------------------
# 修复器
# ---------------------------------------------------------------------------


class MockReflector:
    """确定性修复器：模拟 LLM 在证据充足时修正失败用例。

    v1 只修 class1（域值幻觉）：把越界/非法值钳到 oracle 给的合法范围。
    这模拟「给了 oracle 事实就能修」——用于离线证明闭环机制与 diff 门禁。
    """

    def __init__(self, oracle_facts: Optional[dict] = None):
        #: message.signal -> (min, max) 物理范围（来自 oracle/资产）
        self.oracle_facts: dict[tuple[str, str], tuple[float, float]] = oracle_facts or {}

    def fix(self, case: GeneratedCase, info: FailureInfo) -> Optional[GeneratedCase]:
        """修正失败用例；无法修返回 None（调用方计 unhealed）。"""
        if info.kind != "class1" or case.execution is None:
            return None
        new_ex = case.execution.model_copy(deep=True)
        changed = False
        for step in new_ex.setup + new_ex.expect:
            args = step.args
            msg = args.get("message")
            sig = args.get("signal")
            if not msg or not sig:
                continue
            bounds = self.oracle_facts.get((msg, sig))
            if bounds is None:
                continue
            lo, hi = bounds
            val = args.get("value")
            if isinstance(val, (int, float)):
                fixed = min(max(float(val), lo), hi)
                if fixed != float(val):
                    args["value"] = int(fixed) if isinstance(val, int) else fixed
                    changed = True
        if not changed:
            return None
        case2 = case.model_copy(deep=True)
        case2.execution = new_ex
        return case2


class LLMReflector:
    """真 LLM 修正器（demo 用，需 API key）。构造修正 prompt 并调用模型。

    fix() 要求客户端提供 `complete(prompt) -> str` 能力——OpenAICompatClient
    的 generate_cases 语义不符，故这里直接依赖其底层 chat 完成修正（修正
    prompt 不是"生成一批用例"，而是"改一条失败用例"）。
    """

    #: 信号事实表（派生自 executor_real.ENUM_SIGNAL_TEXTS 镜像 + 物理范围）：
    #: message.signal -> 枚举文本/物理范围。修正时注入失败信号的真实域值，
    #: 防 LLM 再次编造。枚举部分与 executor_real 同源（不双写）。
    SIGNAL_FACTS: dict[tuple[str, str], dict] = {}

    @classmethod
    def _signal_facts(cls) -> dict[tuple[str, str], dict]:
        """构建信号事实（枚举来自 executor_real 镜像表，范围内置）。"""
        if cls.SIGNAL_FACTS:
            return cls.SIGNAL_FACTS
        from tcms_ai_testgen.executor_real import ENUM_SIGNAL_TEXTS

        ranges: dict[tuple[str, str], tuple[int, int]] = {
            ("AlarmEvent", "AlarmCode"): (0, 255),
            ("AlarmEvent", "AlarmLevel"): (0, 3),
            ("VehicleSpeed", "SpeedKmh"): (0, 200),
            ("TractionBrakeHandle", "HandlePosition"): (0, 16),
            ("EnergyStatus", "SocPercent"): (0, 100),
            ("DoorControl", "AllDoorsClosed"): (0, 1),
        }
        for (msg, sig), texts in ENUM_SIGNAL_TEXTS.items():
            lo = min(texts)
            hi = max(texts)
            ranges[(msg, sig)] = (lo, hi)
        for (msg, sig), (lo, hi) in ranges.items():
            entry: dict = {"min": lo, "max": hi, "note": "raw 值" if (msg, sig) in ENUM_SIGNAL_TEXTS else "物理值"}
            if (msg, sig) in ENUM_SIGNAL_TEXTS:
                entry["enum"] = list(ENUM_SIGNAL_TEXTS[(msg, sig)].values())
                entry["note"] = "decode 后是文本枚举，encode 收 raw"
            cls.SIGNAL_FACTS[(msg, sig)] = entry
        return cls.SIGNAL_FACTS

    def __init__(self, client, rag_index=None, max_evidence_chars: int = 1200):
        self.client = client
        self.rag_index = rag_index
        self.max_evidence_chars = max_evidence_chars

    def _evidence(self, case: GeneratedCase, info: FailureInfo) -> str:
        parts: list[str] = []
        # 1) 失败信号的真实域值（oracle 事实）—— class1 的修复依据
        if info.signal and info.message:
            fact = self._signal_facts().get((info.message, info.signal))
            if fact:
                enum_hint = ""
                if fact.get("enum"):
                    # 枚举信号：encode 的 value 必须 raw int，禁止用文本枚举！
                    enum_hint = (
                        f"（该信号是枚举信号：encode 的 value 必须用 raw int "
                        f"{fact['min']}..{fact['max']}，数字对应 {fact['enum']}，"
                        "禁止填文本！）"
                    )
                parts.append(
                    f"该信号真实域值（必须遵守）：{info.message}.{info.signal} "
                    f"min={fact['min']} max={fact['max']} {enum_hint}"
                )
            else:
                parts.append(
                    f"注意：{info.message}.{info.signal} 不在已知信号表，若该信号不存在"
                    "请输出空 JSON（目标不支持）"
                )
        # 2) RAG 上游金标（class2 断言错配的修复依据）
        if self.rag_index is not None:
            hits = self.rag_index.retrieve(case.purpose + " " + case.expected, top_k=2)
            if hits:
                parts.append("上游真实测试金标（模仿其断言形态）：")
                parts.extend(h.snippet(max_chars=350) for h in hits)
        text = "\n".join(parts)
        return text[: self.max_evidence_chars]

    def fix_prompt(self, case: GeneratedCase, info: FailureInfo, stdout: str) -> str:
        ex = case.execution.model_dump_json(indent=1) if case.execution else "无 execution"
        # 截取失败输出关键行（EncodeError/断言），不贴整段 traceback
        err_lines = [ln for ln in stdout.splitlines()
                     if "Error" in ln or "assert" in ln or "FAILED" in ln][:8]
        err_text = "\n".join(err_lines) or stdout[:500]
        return f"""该用例真实执行失败，请修正它的 execution 并输出**修正后的 execution JSON**（裸 JSON 或 {{"cases": [...]}} 外壳均可，只输出 json fence）：

失败用例 execution：
{ex}

真实执行失败信息（节选）：
{err_text}

失败类型：{info.kind}（{info.reason}）

要求：
1. 只改 execution 中导致失败的部分（如越界的 value、错误的枚举断言）；
2. 不得删除断言、不得改用例名、不得新增其它用例；
3. execution 必须与原来实质不同（改了才算修）；
4. 若无法修正（目标本身不支持），输出空 JSON {{"cases": []}}。

证据（以此为准，禁止编造）：
{self._evidence(case, info)}
"""

    def fix(self, case: GeneratedCase, info: FailureInfo, stdout: str) -> Optional[GeneratedCase]:
        """调用真 LLM 修正；失败/无实质变化返回 None（计 unhealed）。

        兼容两种响应形态：{"cases":[...]} 外壳 或 裸 execution JSON。
        """
        from tcms_ai_testgen.llm import extract_json
        from tcms_ai_testgen.models import GeneratedCase as GC

        prompt = self.fix_prompt(case, info, stdout)
        raw = self._chat(prompt)
        payload = extract_json(raw)
        if payload is None:
            return None
        # 形态 1：cases 外壳 → 找同名且 diff
        if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
            for item in payload["cases"]:
                try:
                    c = GC.model_validate(item)
                except Exception:
                    continue
                if c.name == case.name and execution_diff(case.execution, c.execution):
                    return c
            return None
        # 形态 2：裸 execution → 直接套回原 case
        try:
            new_ex = ExecutionIntent.model_validate(payload)
        except Exception:
            return None
        if execution_diff(case.execution, new_ex):
            c2 = case.model_copy(deep=True)
            c2.execution = new_ex
            return c2
        return None

    def _chat(self, prompt: str) -> str:
        """底层 chat 调用（OpenAICompatClient.complete）。"""
        if not hasattr(self.client, "complete"):
            raise RuntimeError("LLMReflector 需要支持 complete(prompt) 的客户端")
        return self.client.complete(prompt, temperature=0.1)


# ---------------------------------------------------------------------------
# 反思闭环
# ---------------------------------------------------------------------------


def reflect_loop(
    cases: list[GeneratedCase],
    upstream_root: str | Path,
    reflector: Callable[[GeneratedCase, FailureInfo, str], Optional[GeneratedCase]],
    *,
    rerun_all: bool = True,
) -> ReflectReport:
    """两轮闭环：round1 全量执行 → 失败分类 → 修复器修正失败项 → round2 全量重跑。

    状态机指标按 name 归因（不重命名）。rerun_all=True 时 round2 重跑全部
    （含 PASS 项验证没被改坏）；False 只跑修正项（不推荐，破坏分母）。
    """
    root = Path(upstream_root)
    r1 = run_real(cases, root)

    # 失败用例按 name 定位（从 stdout 解析失败名）
    failed_names = _failed_names(r1.stdout)
    outcomes: list[CaseOutcome] = []
    fixed: dict[str, GeneratedCase] = {}
    fix_stdout_by_case: dict[str, FailureInfo] = {}
    for c in cases:
        state1 = "FAIL" if c.name in failed_names else "PASS"
        info = FailureInfo(kind="")
        if state1 == "FAIL":
            info = classify_failure(c, r1.stdout)
            fixed_c = reflector(c, info, r1.stdout)
            if fixed_c is not None and execution_diff(c.execution, fixed_c.execution):
                fixed[c.name] = fixed_c
            fix_stdout_by_case[c.name] = info
        outcomes.append(CaseOutcome(name=c.name, round1=state1, round2="SKIP",
                                    transition="", failure_kind=info.kind))

    if not fixed:
        # 无任何修复成功：FAIL 项如实标 not_progressed（round2 无意义）
        for o in outcomes:
            if o.round1 == "FAIL":
                o.round2 = "FAIL"
                o.transition = "not_progressed"
            else:
                o.transition = "stable"
        return ReflectReport(total=len(cases), outcomes=outcomes,
                             stdout_round1=r1.stdout)

    # round2：用修正版替换失败项，全量重跑。修正版必须仍可编译——
    # 不可编译的修正丢弃（否则它会在 round2 静默缺席 = 假自愈）
    round2_cases: list[GeneratedCase] = []
    for c in cases:
        f = fixed.get(c.name)
        if f is not None:
            from tcms_ai_testgen.executor_real import compile_case

            if compile_case(f) is None:
                # 修正不可编译：不算 fixed，保留原失败用例进 round2
                fixed.pop(c.name)
                round2_cases.append(c)
                continue
        round2_cases.append(f if c.name in fixed else c)
    if not fixed:
        for o in outcomes:
            if o.round1 == "FAIL":
                o.round2 = "FAIL"
                o.transition = "not_progressed"
            else:
                o.transition = "stable"
        return ReflectReport(total=len(cases), outcomes=outcomes,
                             stdout_round1=r1.stdout)

    r2 = run_real(round2_cases, root)
    r2_failed = _failed_names(r2.stdout)
    for o in outcomes:
        state2 = "FAIL" if o.name in r2_failed else "PASS"
        o.round2 = state2
        o.transition = _transition(o.round1, state2)

    return ReflectReport(total=len(cases), outcomes=outcomes,
                         stdout_round1=r1.stdout, stdout_round2=r2.stdout)


def _failed_names(stdout: str) -> set[str]:
    """从 pytest 输出解析失败用例名（同 mutation._failed_case_names）。"""
    names: set[str] = set()
    for m in re.finditer(r"FAILED\s+\S*?::(\w+)\b", stdout):
        names.add(m.group(1))
    return names


__all__ = [
    "CaseOutcome",
    "FailureInfo",
    "LLMReflector",
    "MockReflector",
    "ReflectReport",
    "classify_failure",
    "execution_diff",
    "reflect_loop",
]
