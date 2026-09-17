"""P4 扩展测试：自由 Agent 目标解析（/api/agent/free 后端逻辑）。

规则优先 + LLM 仲裁（无 key 落回规则）：
    - 确定性命中：规则解析把一句话目标映射到真实故障字典条目
    - LLM 仲裁：仅当规则歧义且注入 chat 时触发（测试用替身）
    - 无命中 → NoFaultMatch（API 层转 422）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.agent.freeform import NoFaultMatch, parse_free_goal
from tcms_ai_platform.core import load_asset_model

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def model():
    return load_asset_model(UPSTREAM)


@NEEDS_UPSTREAM
def test_parse_free_goal_door(model):
    """「验证车门故障不能发车」→ 确定性命中 door_fault / derate。"""
    p = parse_free_goal(model, "验证车门故障不能发车", use_llm=False)
    assert p.fault == "door_fault"
    assert p.expected == "derate"
    assert p.resolver == "rule"
    assert p.confidence >= 0.9
    assert p.fault_name == "车门故障（按未关处理）"


@NEEDS_UPSTREAM
def test_parse_free_goal_explicit_action_terms(model):
    """显式处置词应被正确提取（而不只是字典默认）。"""
    p = parse_free_goal(model, "超速需要紧急制动", use_llm=False)
    assert p.fault == "overspeed"
    assert p.expected == "emergency_brake"  # 用户显式说紧急制动，覆盖字典默认 derate


@NEEDS_UPSTREAM
def test_parse_free_goal_latin_name(model):
    """英文前缀中文名（VCU 心跳丢失）应能命中（剥拉丁 + 中文主体匹配）。"""
    p = parse_free_goal(model, "VCU心跳丢失时应该降级", use_llm=False)
    assert p.fault == "heartbeat_loss_vcu"
    assert p.expected == "derate"


@NEEDS_UPSTREAM
def test_parse_free_goal_bigram_order(model):
    """乱序名称（CRC报文校验错误）命中（bigram 覆盖）。"""
    p = parse_free_goal(model, "CRC报文校验错误应告警", use_llm=False)
    assert p.fault == "crc_error_frame"
    assert p.expected == "warning"


@NEEDS_UPSTREAM
def test_parse_free_goal_no_match_raises(model):
    """无关目标 → NoFaultMatch（诚实不猜，API 转 422）。"""
    with pytest.raises(NoFaultMatch):
        parse_free_goal(model, "今天天气不错", use_llm=False)


@NEEDS_UPSTREAM
def test_parse_free_goal_zero_candidate_rejects_even_with_llm(model):
    """产品红线：规则零候选（目标无任何真实故障语义）→ 即使 LLM 仲裁可用、
    注入 chat 返回真实键，也必须 NoFaultMatch（LLM 不允许自由发明故障）。"""
    with pytest.raises(NoFaultMatch):
        parse_free_goal(
            model,
            "今天天气不错",
            use_llm=True,
            llm_chat=lambda s, u: '{"fault": "overspeed", "expected": "derate"}',
        )


@NEEDS_UPSTREAM
def test_parse_free_goal_llm_disambiguates(model):
    """规则多候选歧义（车门故障 vs 超速同分）→ LLM 从候选里消歧选一个。"""
    p = parse_free_goal(
        model,
        "车门故障和超速都要处置",
        use_llm=True,
        llm_chat=lambda s, u: '{"fault": "overspeed", "expected": "derate"}',
    )
    assert p.fault == "overspeed"
    assert p.expected == "derate"
    assert p.resolver == "llm"
    assert p.confidence == 1.0


@NEEDS_UPSTREAM
def test_parse_free_goal_llm_outside_candidates_rejected(model):
    """LLM 输出非候选真实键（候选只有 door_fault/overspeed 却回 eb_failure）→ 拒绝，
    落回规则兜底（resolver=rule），不许 LLM 跳到规则没命中的故障。"""
    p = parse_free_goal(
        model,
        "车门故障和超速都要处置",
        use_llm=True,
        llm_chat=lambda s, u: '{"fault": "eb_failure", "expected": "emergency_brake"}',
    )
    assert p.fault in {"door_fault", "overspeed"}  # 规则候选之一（eb_failure 被拒）
    assert p.resolver == "rule"


@NEEDS_UPSTREAM
def test_parse_free_goal_llm_abstain_falls_back(model):
    """LLM 返回 {fault: null}（无法识别）→ 落回规则兜底，不抛。"""
    p = parse_free_goal(
        model,
        "车门故障怎么处理",
        use_llm=True,
        llm_chat=lambda s, u: '{"fault": null}',
    )
    assert p.fault == "door_fault"
    assert p.resolver == "rule"


@NEEDS_UPSTREAM
def test_parse_free_goal_no_key_falls_back(model, monkeypatch):
    """无 key 且规则未命中 → 不做 LLM 调用，仍抛 NoFaultMatch（离线可复现）。"""
    for k in ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(Path("Z:/no-cred.yaml")))
    with pytest.raises(NoFaultMatch):
        parse_free_goal(model, "报文CRC错误要给出告警提示", use_llm=True)


@NEEDS_UPSTREAM
def test_parse_free_goal_task_id_seq(model):
    """解析结果打包为 T-FREE-{n} 临时任务（Harness 可执行契约）。"""
    p = parse_free_goal(model, "验证车门故障不能发车", use_llm=False, seq=3)
    t = p.to_task("验证车门故障不能发车", seq=3)
    assert t.task_id == "T-FREE-3"
    assert t.target_fault == "door_fault"
    assert t.expected_action == "derate"
    assert t.kb_query  # 整句作 RAG 检索


@NEEDS_UPSTREAM
def test_parse_free_goal_specific_tie_wins(model):
    """子串平局确定性：更具体的故障名（更长命中）优先，而非字典顺序。

    「多区烟火报警」同时含「烟火报警」（单区）子串；「后车门故障」同时含
    「车门故障」子串——规则必须取更长更具体的那个（Q2 扩库后 66 键新增回归）。
    """
    p = parse_free_goal(model, "验证多区烟火报警的处置", use_llm=False)
    assert p.fault == "fire_multizone_alarm"
    p = parse_free_goal(model, "后车门故障不能发车", use_llm=False)
    assert p.fault == "rear_door_fault"
    assert p.expected == "derate"
    # 单区烟火报警仍指向基础探测故障（无歧义）
    p = parse_free_goal(model, "验证烟火报警停车", use_llm=False)
    assert p.fault == "smoke_detected"


@NEEDS_UPSTREAM
def test_parse_free_goal_error_count_is_dynamic(model):
    """未命中文案中的故障数 = len(faults)（随扩库自证，禁止手抄 22/26/66）。"""
    with pytest.raises(NoFaultMatch) as ei:
        parse_free_goal(model, "一般故障", use_llm=False)
    assert str(len(model.faults_by_key)) in str(ei.value)


@NEEDS_UPSTREAM
def test_parse_free_goal_llm_context_injected(model):
    """llm_context（检索证据/多轮上下文）应注入 LLM 消歧提示（辅助候选仲裁）。"""
    seen: dict[str, str] = {}

    def chat(system: str, user: str) -> str:
        seen["user"] = user
        return '{"fault": "door_fault", "expected": "derate"}'

    p = parse_free_goal(
        model,
        "车门故障和超速都要处置",
        use_llm=True,
        llm_chat=chat,
        llm_context="证据：SR-21 后车门故障按未关处理；联锁禁止发车。",
    )
    assert p.fault == "door_fault"
    assert "知识上下文" in seen["user"]
    assert "SR-21" in seen["user"] and "后车门故障" in seen["user"]


@NEEDS_UPSTREAM
def test_parse_free_goal_llm_context_never_bypasses_rule(model):
    """产品红线：llm_context 只在候选仲裁时辅助，规则零候选仍 NoFaultMatch。"""
    seen: dict[str, str] = {}

    def chat(system: str, user: str) -> str:
        seen["user"] = user
        return '{"fault": "overspeed", "expected": "derate"}'

    with pytest.raises(NoFaultMatch):
        parse_free_goal(
            model,
            "今天天气不错",
            use_llm=True,
            llm_chat=chat,
            llm_context="证据：超速监督阈值 160km/h。",
        )
    # 规则零候选在 LLM 之前即抛——chat 根本不应被调用（context 无法绕过红线）
    assert seen == {}


@NEEDS_UPSTREAM
def test_every_fault_name_reachable_by_free_parser(model):
    """Agent 侧无孤儿：每条真实故障用其中文名作目标都能被自由解析命中自身键。

    镜像上游"无孤儿故障不变量"到语义解析层——扩库新增的故障必须能被
    自然语言目标触达（确定性规则路径，不依赖 LLM）。
    """
    miss = []
    for f in model.faults_by_key.values():
        try:
            p = parse_free_goal(model, f"验证{f.name}的处置", use_llm=False)
            if p.fault != f.key:
                miss.append((f.key, p.fault))
        except NoFaultMatch:
            miss.append((f.key, "NoFaultMatch"))
    assert not miss, f"不可达故障（自由解析）：{miss}"
