"""变异杀毒实验（P3 质量证据）—— 无需真 LLM 也能证明生成用例有效。

审查裁定（docs/decisions.md D1-E）：compile_rate / pass_rate 本身是幸存者
偏差——用例若只是「回声 spec」会全过但毫无杀毒力。**变异杀毒**才是质量证据：
把被测对象（上游 tcms-can-test 的 simulator/faultlevel 行为）翻转成「带 bug 的
实现」，再跑同一批生成用例。能识别出变异的用例 = 有真实断言力（kills the
mutant）；测不出变异的用例 = 断言太弱或根本没测到该行为。

方法（不改上游一行源码）：
    生成 pytest 文件时，在文件头部注入一个 conftest 级 fixture 把目标类方法
    monkeypatch 成变异版；变异版本与原始版本用**同一批生成用例**跑，
    统计：
        killed  = 在变异版上 FAIL 的用例数（成功杀毒）
        survived = 在变异版上仍 PASS 的用例数（漏网）
        kill_rate = killed / (killed + survived)
    注意：只有「原始版 PASS」的用例才纳入杀毒统计（本来就失败的用例在变异
    版上也失败不构成杀毒——它没有证明任何东西）。

设计：
    - mutation 描述 = {name, patch_lines}：patch_lines 是注入文件的 python
      代码（monkeypatch fixture），编译期校验只允许 import 上游 + 赋值。
    - 安全：变异代码只存在于**生成的临时 pytest 文件**，由 subprocess 在
      上游 venv 跑；tcms-ai-testgen 仓库与上游源码均不被修改。
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from tcms_ai_testgen.executor_real import _HEADER, RealExecResult, compile_case
from tcms_ai_testgen.models import GeneratedCase

#: 变异定义：patch 代码 + 相关用例判定（字符串匹配 execution/自然语言）。
#: 相关 = 该变异翻转的行为被此用例断言覆盖（应被杀）。
MutationSpec = dict[str, object]


def _relevant_door(case: GeneratedCase) -> bool:
    if not case.execution:
        return False
    return any(
        "DoorControl" in str(s.args) for s in case.execution.setup + case.execution.expect
    ) or "车门" in (case.purpose + case.expected)


def _relevant_encode(case: GeneratedCase) -> bool:
    if not case.execution:
        return False
    return any(
        s.op == "expect_encode_error" or s.op == "expect_encode_ok" for s in case.execution.expect
    )


def _relevant_overspeed(case: GeneratedCase) -> bool:
    if not case.execution:
        return False
    return case.execution.fault == "overspeed" or "overspeed" in (case.purpose + case.expected).lower()


#: 内置变异库：每个 = 对上游一个真实行为做「安全翻转」（有意引入 bug）。
#: patch 代码用 autouse fixture 在测试前替换类方法（test 文件 import 上游类）。
MUTATIONS: dict[str, MutationSpec] = {
    # M1: 车门故障不再置 Fault（门状态 2 被吞成 Closed 0）→ 车门故障断言应 fail
    "door_fault_ignored": {
        "desc": "车门故障被吞成 Closed（set_door_state 变异）",
        "targets": "DoorControl 信号断言（Fault）",
        "patch": """
import tcms.simulator as _sim
_orig_set_door = _sim.TCMSNodeSimulator.set_door_state

def _buggy_set_door(self, door_index, state):
    if state == 2:
        state = 0  # 变异：Fault 被吞成 Closed
    _orig_set_door(self, door_index, state)

_sim.TCMSNodeSimulator.set_door_state = _buggy_set_door
""",
        "relevant": _relevant_door,
    },
    # M2: 编码不再拒绝越界（db.encode_message 直通不校验）→ 越界拒绝断言应 fail
    "encode_never_rejects": {
        "desc": "越界编码不再抛 EncodeError",
        "targets": "expect_encode_error / expect_encode_ok",
        "patch": """
import cantools.database.can as _can

def _lax_encode_factory(self, message_name_or_tree, data, scaling=True, padding=False):
    # 变异：无条件返回零填充帧，永不抛 EncodeError
    msg = self.get_message_by_name(message_name_or_tree) if isinstance(message_name_or_tree, str) else message_name_or_tree
    return b"\\x00" * msg.length

_can.Database.encode_message = _lax_encode_factory
""",
        "relevant": _relevant_encode,
    },
    # M3: overspeed 处置动作被改成 emergency_brake（本应 derate）→ 故障场景断言应 fail
    "overspeed_action_flipped": {
        "desc": "overspeed 处置动作被翻转（derate→emergency_brake）",
        "targets": "expect_action(overspeed)",
        "patch": """
import tcms.faultlevel as _fl
_orig_action = _fl.action_for

def _flipped_action(fault_name, mode="auto"):
    if fault_name == "overspeed":
        return "emergency_brake"  # 变异：处置动作被翻转
    return _orig_action(fault_name, mode)

_fl.action_for = _flipped_action
""",
        "relevant": _relevant_overspeed,
    },
}

# ---------------------------------------------------------------------------
# 派生变异：让杀毒率从「3 个点」变成「覆盖矩阵」
# ---------------------------------------------------------------------------
#
# 手写的 M1–M3 只能回答"AI 用例有没有覆盖这 3 个行为"。把同一个机制按 FMEA 字典
# 参数化之后，字典里每一条键都各得一个变异，杀毒率于是能做成**覆盖矩阵**：
# "AI 生成的用例覆盖了 203 条键里的哪些、漏了哪些"——那才是"AI 写的测试到底行不行"
# 最直接的证据形态。机制与 M3 完全一致（替换 `faultlevel.action_for`），只是键可参数化。

#: 处置取值（与上游 `tcms.faultlevel` 一致，顺序即紧急程度）。
ACTION_ORDER = ("none", "warning", "derate", "emergency_brake", "shutdown")
#: "会真的做点什么"的处置：翻转时降为 none（漏报），其余升为 emergency_brake（误报）。
SEVERE_ACTIONS = ("derate", "emergency_brake", "shutdown")

#: 派生变异名格式：`action_flip__<fault_key>`。
DERIVED_PREFIX = "action_flip__"


def _flip_policy(action: str) -> str:
    """把一个处置翻到**语义相反的一侧**。"""
    return "none" if action in SEVERE_ACTIONS else "emergency_brake"


def _relevant_for_fault(key: str) -> Callable[[GeneratedCase], bool]:
    """相关用例 = 断言了这个键处置的用例（`execution.fault` 命中，或意图/预期里提到键名）。"""

    def _rel(case: GeneratedCase) -> bool:
        if not case.execution:
            return False
        if case.execution.fault == key:
            return True
        return key.lower() in (case.purpose + case.expected).lower()

    return _rel


def fault_action_mutation(key: str) -> tuple[str, MutationSpec]:
    """把一个故障键参数化为「处置翻转」变异（与手写 M3 同机制）。

    翻转规则刻意取"换到相反一侧"，而不是固定换成某个值：若目标键原本就等于那个
    固定值，变异将**等价于原实现**，kill_rate 恒为 0，会被误读成"用例不够好"。
    两个方向也都对应真实的安全缺陷形态：
      - 原本会处置的（derate / emergency_brake / shutdown）→ 降为 none（**漏报**）
      - 原本不处置的（none / warning）→ 升为 emergency_brake（**误报**）
    """
    name = f"{DERIVED_PREFIX}{key}"
    spec: MutationSpec = {
        "desc": f"{key} 的处置被翻到相反一侧（漏报 / 误报）",
        "targets": f"断言 {key} 处置的用例（expect_action / 故障场景断言）",
        "patch": f'''
import tcms.faultlevel as _fl
_orig_action = _fl.action_for
_SEVERE = {SEVERE_ACTIONS!r}

def _flipped_action(fault_name, mode="auto"):
    if fault_name != {key!r}:
        return _orig_action(fault_name, mode)
    return "none" if _orig_action(fault_name, mode) in _SEVERE else "emergency_brake"

_fl.action_for = _flipped_action
''',
        "relevant": _relevant_for_fault(key),
    }
    return name, spec


def mutable_fault_keys() -> list[str]:
    """可做「处置变异」的故障键 = oracle 镜像里那 10 个有语义分级的键。

    **边界（必须如实说明，别把它读成 203）**：`faults.yaml` 有 203 条 FMEA 键、
    且全部可注入，但只有这 10 个键有 level→action 语义分级（上游 `faultlevel.FAULTS`）
    ——`action_for()` 对其余 193 个键会抛 `ValueError`。所以"按字典 203 键派生变异"
    需要另换一个 patch 点（数据层而非函数层），本函数不假装能做到。
    覆盖矩阵的实际规模就是这 10 键 × 漏报/误报两个方向。
    """
    from tcms_ai_testgen.oracle import FAULT_LEVELS

    # 复用 oracle 的静态镜像，而不是另读一份 faults.yaml：生成期派生期望用的就是它，
    # 变异能覆盖的键集合必须与生成期能派生期望的键集合**一致**，否则杀毒率算不准。
    return list(FAULT_LEVELS)


def derive_fault_mutations(keys: Iterable[str]) -> list[str]:
    """按故障键批量派生变异并注册进 `MUTATIONS`，返回变异名列表。

    注册是刻意的：`mutation_patch` / `mutation_relevant` / `list_mutations` /
    `run_mutation` 因此**无需任何改动**就能直接吃派生变异。

    用法（把杀毒率做成覆盖矩阵）::

        from tcms_ai_testgen.mutation import (
            derive_fault_mutations, mutable_fault_keys, run_mutation,
        )
        for name in derive_fault_mutations(mutable_fault_keys()):
            mr = run_mutation(cases, name, upstream_root, baseline=base)
            # mr.kill_rate > 0 → 这批判例覆盖了该键的处置；== 0 → 漏了这个键
    """
    names: list[str] = []
    for key in keys:
        name, spec = fault_action_mutation(key)
        MUTATIONS[name] = spec
        names.append(name)
    return names


#: 兼容旧引用（MUTATION_TARGETS 由 desc/targets 取代）
MUTATION_TARGETS: dict[str, str] = {
    name: str(spec["targets"]) for name, spec in MUTATIONS.items()
}


def mutation_patch(name: str) -> str:
    """取内置变异 patch 代码；未知变异抛 KeyError。"""
    if name not in MUTATIONS:
        raise KeyError(f"未知变异: {name}（可用 {list(MUTATIONS)}）")
    return str(MUTATIONS[name]["patch"])


def mutation_relevant(name: str) -> Callable[[GeneratedCase], bool]:
    """取变异的「相关用例」判定函数。"""
    spec = MUTATIONS[name]
    fn = spec.get("relevant")
    if fn is None:
        return lambda case: True
    return fn  # type: ignore[return-value]


def list_mutations() -> list[str]:
    return list(MUTATIONS)


@dataclass
class MutationResult:
    """一批用例对单个变异的杀毒统计。

    口径（docs/metrics.md）：kill_rate 只对「与该变异相关的用例」计算——
    把无关用例放进分母会让杀毒率虚低（它们本就该 survive）。
    """

    mutation: str
    total: int  # 原始版 PASS 的用例数
    relevant: int  # 与变异相关的用例数（应被杀）
    killed: int  # 相关用例中被杀的数（变异版 FAIL）
    survived: int  # 相关用例中漏网的数（变异版仍 PASS）
    stdout: str = ""

    @property
    def kill_rate(self) -> float:
        return self.killed / self.relevant if self.relevant else 0.0

    @property
    def overall_kill_rate(self) -> float:
        """朴素杀毒率（分母=全部用例）——文档对比用。"""
        return self.killed / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "mutation": self.mutation,
            "total": self.total,
            "relevant": self.relevant,
            "killed": self.killed,
            "survived": self.survived,
            "kill_rate": round(self.kill_rate, 3),
            "overall_kill_rate": round(self.overall_kill_rate, 3),
        }


def build_mutant_file(cases: list[GeneratedCase], mutation: str) -> tuple[str | None, list[str]]:
    """生成变异版 pytest 文件源码（纯函数，离线可测）。

    返回 (code, compiled_case_names)；无一条可编译返回 (None, [])。
    """
    patch = mutation_patch(mutation)
    bodies: list[str] = []
    names: list[str] = []
    for case in cases:
        body = compile_case(case)
        if body is None:
            continue
        bodies.append(body)
        names.append(case.name)
    if not bodies:
        return None, []
    code = _HEADER + "\n"
    code += (
        "\nimport pytest\n\n\n"
        "@pytest.fixture(autouse=True)\n"
        "def _mutate(monkeypatch):\n"
        "    # 注入变异：替换被测对象行为（见 mutation 库）\n"
    )
    for ln in patch.strip().splitlines():
        code += "    " + ln + "\n"
    code += "\n\n" + "\n\n".join(bodies) + "\n"
    return code, names


def _compute_killed(
    stdout: str, baseline_stdout: str, relevant_compiled: list[str]
) -> int:
    """从变异版/原始版 pytest 输出计算杀毒数（纯函数，离线可测）。

    只统计「原始版 PASS 且变异版 FAIL」的相关用例；变异版未收集到任何
    用例（patch 语法错等）时返回 0（如实反映，不误报杀毒）。
    """
    if "no tests ran" in stdout:
        return 0
    failed_names = _failed_case_names(stdout)
    baseline_failed_names = _failed_case_names(baseline_stdout)
    killed = 0
    for name in relevant_compiled:
        if name in baseline_failed_names:
            continue  # 原始版就失败：不算杀毒（它没证明任何东西）
        if name in failed_names:
            killed += 1
    return killed


def run_mutation(
    cases: list[GeneratedCase],
    mutation: str,
    upstream_root: str | Path,
    *,
    keep_artifacts: bool = False,
    baseline: Optional[RealExecResult] = None,
) -> MutationResult:
    """跑单个变异：生成变异版 pytest 并在上游执行，返回杀毒统计。

    口径：kill_rate 分母 = 相关用例（relevant），非全部用例。
    baseline 传原始版结果可跳过重复跑原始版（默认自动跑一遍拿 total）。
    """
    root = Path(upstream_root)
    relevant_fn = mutation_relevant(mutation)
    relevant_names = {c.name for c in cases if relevant_fn(c) and compile_case(c) is not None}
    code, compiled_names = build_mutant_file(cases, mutation)
    if code is None:
        return MutationResult(mutation=mutation, total=0, relevant=0, killed=0, survived=0)

    # 先跑原始版（拿 baseline total = 原始 PASS 数）
    if baseline is None:  # pragma: no cover - 需上游
        from tcms_ai_testgen.executor_real import run_real

        baseline = run_real(cases, root)

    if not (root / "tests" / "conftest.py").is_file():  # pragma: no cover - 需上游
        raise FileNotFoundError(f"非 tcms-can-test 仓库根: {root}")
    tests_dir = root / "tests"
    gen_path = tests_dir / "test_ai_generated_mutant.py"
    gen_path.write_text(code, encoding="utf-8")

    # 解释器解析：优先上游仓自带的 venv（本地 Windows 布局），
    # 不存在时退回当前解释器——CI/Linux 下上游是干净 checkout，没有 .venv。
    # 注意：不能写成 `str(py) if py else ...`——Path 对象恒为真，回退永不生效。
    py = root / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = None
    cmd = [str(py) if py else sys.executable, "-m", "pytest", str(gen_path),
           "-q", "--no-header"]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)  # pragma: no cover - subprocess
    stdout = proc.stdout  # pragma: no cover

    baseline_stdout = baseline.stdout if baseline is not None else ""
    total = baseline.passed if baseline else 0
    relevant_compiled = [n for n in compiled_names if n in relevant_names]
    killed = _compute_killed(stdout, baseline_stdout, relevant_compiled)  # pragma: no cover

    if not keep_artifacts:  # pragma: no cover - 文件清理随执行
        try:
            gen_path.unlink()
        except OSError:
            pass

    return MutationResult(
        mutation=mutation,
        total=total,
        relevant=len(relevant_compiled),
        killed=killed,
        survived=max(0, len(relevant_compiled) - killed),
        stdout=stdout,
    )


def _failed_case_names(stdout: str) -> set[str]:
    """从 pytest 输出解析失败用例名（`FAILED path::test_name` 行）。"""
    names: set[str] = set()
    for m in re.finditer(r"FAILED\s+\S*?::(\w+)\b", stdout):
        names.add(m.group(1))
    return names


__all__ = [
    "ACTION_ORDER",
    "DERIVED_PREFIX",
    "MUTATIONS",
    "SEVERE_ACTIONS",
    "MUTATION_TARGETS",
    "MutationResult",
    "_compute_killed",
    "_failed_case_names",
    "build_mutant_file",
    "derive_fault_mutations",
    "fault_action_mutation",
    "mutable_fault_keys",
    "list_mutations",
    "mutation_patch",
    "mutation_relevant",
    "run_mutation",
]
