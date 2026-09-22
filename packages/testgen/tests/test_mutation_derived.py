"""派生变异：把杀毒率从「3 个手写点」扩展成「10 键 × 两方向的覆盖矩阵」。

手写的 M1–M3 只能回答「AI 用例有没有覆盖这 3 个行为」。本文件验证派生机制：
按故障键批量生成「处置翻转」变异，让 oracle 镜像里**每一个**有语义分级的键各得一个
变异，于是杀毒率可以做成覆盖矩阵——"AI 生成的用例覆盖了哪几个键、漏了哪几个"。

## 一条必须写下来的边界（来自本文件第一次跑红）

最初的设计意图是"按 `faults.yaml` 的 203 条 FMEA 键派生"。实测不成立：
上游 `faultlevel.action_for()` 只认 `FAULTS` 里那 **10 个有 level→action 语义分级**的键，
对其余 193 个键（如 `heartbeat_loss_vcu`）直接抛 `ValueError`。因此覆盖矩阵的真实规模
是 **10 键 × 漏报/误报两个方向**，而不是 203。这条边界由
`test_mutable_keys_are_exactly_what_action_for_supports` 守住——
不写下来的话，下一次还会有人按 203 去算覆盖率。

这里**不跑真实 pytest**：单测验证机制本身（patch 只翻目标键、翻转一定非等价、
派生变异能走既有 API）。在单测里起 subprocess 跑上游 pytest 既慢，又会把
"机制有缺陷"和"环境有问题"混在一起；真实杀毒跑由 `examples/` 下的实验脚本负责。
"""

from __future__ import annotations

import pytest

from tcms_ai_testgen.execution import ExecutionIntent
from tcms_ai_testgen.models import GeneratedCase
from tcms_ai_testgen.mutation import (
    MUTATIONS,
    SEVERE_ACTIONS,
    derive_fault_mutations,
    list_mutations,
    mutable_fault_keys,
    mutation_patch,
    mutation_relevant,
)

try:  # 上游包（monorepo 成员）——不在同一环境时如实跳过，而不是假装通过
    import tcms.faultlevel as _fl

    HAS_FAULTLEVEL = True
except Exception:  # noqa: BLE001
    HAS_FAULTLEVEL = False

needs_faultlevel = pytest.mark.skipif(not HAS_FAULTLEVEL, reason="上游 tcms.faultlevel 不可用")


@pytest.fixture(autouse=True)
def _restore_mutation_registry():
    """派生变异会写进**全局** `MUTATIONS`：用完清掉，避免污染同会话的其它测试。

    （注册进全局是产品上的刻意设计——`run_mutation` 等既有 API 因此无需改动就能吃
    派生变异；但测试必须自己负责还原，否则 `list_mutations()` 的断言会随执行顺序变化。）
    """
    before = set(MUTATIONS)
    yield
    for name in set(MUTATIONS) - before:
        MUTATIONS.pop(name, None)


# ---------------------------------------------------------------------------
# 1. 边界：能派生的键 == action_for 真正支持的键
# ---------------------------------------------------------------------------


@needs_faultlevel
def test_mutable_keys_are_exactly_what_action_for_supports() -> None:
    """派生变异的键集合必须与 `action_for` 真正支持的键集合**完全一致**。

    这条守的是一个曾经错过的设计假设：按 `faults.yaml` 的 203 键派生是**做不到**的，
    因为只有 10 个键有 level→action 语义分级。边界不写成断言，就会被反复误读。
    """
    keys = mutable_fault_keys()
    assert len(keys) == 10, f"oracle 镜像的键数变了（{len(keys)}），请同步边界说明"
    unsupported = [k for k in keys if k not in _fl.FAULTS]
    assert unsupported == [], f"这些键 action_for 并不支持，不该出现在派生集合里：{unsupported}"


# ---------------------------------------------------------------------------
# 2. patch 只翻目标键
# ---------------------------------------------------------------------------


@needs_faultlevel
def test_derived_patch_flips_exactly_the_target_key(monkeypatch) -> None:
    """派生的 patch 必须**只翻目标键**，其余键的行为一字不动。

    这是覆盖矩阵成立的前提：若它顺手改了别的键，"某用例没杀掉这个变异"
    就不再等价于"它没测这个键"。
    """
    target, other = "door_fault", "overspeed"
    before = {k: _fl.action_for(k) for k in (target, other)}

    # 走真实使用路径：先派生注册，再按名字取 patch（`run_mutation` 也是这么取）
    name = derive_fault_mutations([target])[0]
    monkeypatch.setattr(_fl, "action_for", _fl.action_for)  # 测试结束自动还原
    exec(compile(mutation_patch(name), "<derived-mutation>", "exec"), {})

    assert _fl.action_for(target) != before[target], "目标键的处置必须被翻转"
    assert _fl.action_for(other) == before[other], "其它键的处置不得受影响"


# ---------------------------------------------------------------------------
# 3. 翻转一定非等价（否则 kill_rate 恒 0 会被记在生成器头上）
# ---------------------------------------------------------------------------


@needs_faultlevel
def test_flip_is_never_equivalent_for_every_mutable_key() -> None:
    """对可派生的**每一个**键，翻转后的处置都必须与原值不同。

    等价变异的后果很隐蔽：kill_rate 恒为 0，看起来像"AI 用例没测这个键"，
    实际上是把**变异设计缺陷**记在了生成器头上。
    """
    keys = mutable_fault_keys()
    equivalent = [
        key
        for key in keys
        if ("none" if _fl.action_for(key) in SEVERE_ACTIONS else "emergency_brake")
        == _fl.action_for(key)
    ]
    assert equivalent == [], f"这些键的变异与原始行为等价（kill_rate 恒 0）：{equivalent}"


# ---------------------------------------------------------------------------
# 4. 相关用例判定 + 走既有 API
# ---------------------------------------------------------------------------


def test_derived_relevance_hits_only_the_target_key() -> None:
    """相关用例判定：只认「断言了该键处置」的用例，不误伤其它键的用例。"""
    derive_fault_mutations(["door_fault"])
    relevant = mutation_relevant("action_flip__door_fault")

    def case(
        fault: str = "door_fault",
        purpose: str = "验证处置",
        expected: str = "derate",
        with_exec: bool = True,
    ) -> GeneratedCase:
        return GeneratedCase(
            name="t_case",
            purpose=purpose,
            expected=expected,
            execution=ExecutionIntent(kind="fault_scenario", fault=fault) if with_exec else None,
        )

    assert relevant(case()) is True
    assert relevant(case(fault="overspeed")) is False
    assert relevant(case(fault="overspeed", purpose="验证 door_fault 联锁")) is True
    assert relevant(case(with_exec=False)) is False


def test_derived_mutations_use_the_same_registry_api() -> None:
    """派生变异必须能走既有 API——调用方不为它写特例，`run_mutation` 也能直接吃。"""
    names = derive_fault_mutations(["door_fault", "overspeed"])
    assert names == ["action_flip__door_fault", "action_flip__overspeed"]
    for n in names:
        assert n in list_mutations()
        assert mutation_patch(n).strip(), "派生变异必须有可用的 patch 代码"
        assert callable(mutation_relevant(n))
