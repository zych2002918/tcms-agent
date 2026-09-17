"""t2 扩展测试（HTTP 面）：编排顾问端点永不 422 + demo-steps 端点契约。

与 test_advisor.py（决策层）互补：本文件锁定 HTTP 契约——
    - /api/agent/advisor：任何输入（天气/门的问题/空/乱码）都 200，绝不 422
    - /api/agent/free 对照：同样「今天天气不错」仍 422（红线段落不变）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    app = create_app(upstream=UPSTREAM)
    from fastapi.testclient import TestClient

    return TestClient(app)


@NEEDS_UPSTREAM
def test_advisor_endpoint_door(client):
    """HTTP：车门故障 → 200 match_fault + door_fault。"""
    r = client.post("/api/agent/advisor", json={"message": "车门故障不能发车"})
    assert r.status_code == 200
    b = r.json()
    assert b["intent"] == "match_fault"
    assert b["matched_fault"] == "door_fault"
    assert b["fault_matches"][0]["key"] == "door_fault"
    assert "reply" in b and b["reply"]
    # 与决策层结构一致（可被前端直接渲染）
    assert b["needs_clarification"] is False


@NEEDS_UPSTREAM
def test_advisor_endpoint_weather_no_422(client, monkeypatch):
    """HTTP 红线：无 key「今天天气不错」→ 200 out_of_domain（绝不 422）。"""
    for k in ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(Path("Z:/no-cred.yaml")))
    r = client.post("/api/agent/advisor", json={"message": "今天天气不错"})
    assert r.status_code == 200
    b = r.json()
    assert b["intent"] == "out_of_domain"
    assert b["out_of_domain"] is True  # 前端「域外/离线」标注键
    assert b["needs_clarification"] is True
    # 对照：域外输入到 /api/agent/free → 200 no_match + 无伪造候选（非 422 死路，
    # 也不让 LLM 猜键——产品红线升级为「不 422 + 不硬猜」）
    r2 = client.post("/api/agent/free", json={"goal": "今天天气不错"})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["no_match"] is True
    assert not b2["suggested_faults"]


@NEEDS_UPSTREAM
def test_advisor_endpoint_out_of_domain_flag_false_on_match(client):
    """out_of_domain 标志：正常匹配 → false（前端不必靠 intent 推断）。"""
    r = client.post("/api/agent/advisor", json={"message": "车门故障不能发车"})
    assert r.status_code == 200
    b = r.json()
    assert b["intent"] == "match_fault"
    assert b["out_of_domain"] is False


@NEEDS_UPSTREAM
def test_advisor_endpoint_never_422_any_input(client):
    """HTTP 红线扫描：任何输入都不 422（天气/门/空/乱码/超长）。"""
    for msg in (
        "今天天气不错",
        "门的问题",
        "车门关不上",
        "   ",
        "",
        "!!!",
        "asdf qwer 1234",
        "我想知道现在几点",
        "帮我看看我的银行卡余额",
    ):
        r = client.post("/api/agent/advisor", json={"message": msg})
        assert r.status_code == 200, f"输入 {msg!r} 应 200 而非 {r.status_code}"
        b = r.json()
        assert b["intent"] in {
            "match_fault",
            "compose_scenario",
            "clarify",
            "out_of_domain",
            "custom_proposal",
        }
        assert b["reply"]


@NEEDS_UPSTREAM
def test_advisor_endpoint_compose_with_draft(client):
    """HTTP：编排意图 → compose_scenario，suggested_steps 结构可被 /api/run/custom 消费。"""
    r = client.post("/api/agent/advisor", json={"message": "我想编排车门故障和超速的叠加序列"})
    assert r.status_code == 200
    b = r.json()
    assert b["intent"] == "compose_scenario"
    steps = b.get("suggested_steps")
    assert steps
    # 直接提交给 /api/run/custom 真实执行成功（编排顾问闭环）
    rr = client.post("/api/run/custom", json={"name": "advisor草稿", "steps": steps})
    assert rr.status_code == 200
    assert rr.json()["all_passed"] is True


@NEEDS_UPSTREAM
def test_advisor_endpoint_door_question_rag_evidence(client):
    """HTTP：门的问题 → clarify + rag_evidence 证据链（用户可见自己被如何理解）。"""
    r = client.post("/api/agent/advisor", json={"message": "门的问题"})
    assert r.status_code == 200
    b = r.json()
    assert b["intent"] == "clarify"
    assert b["fault_matches"]
    assert b["rag_evidence"]  # RAG 证据链存在
    assert all({"doc_id", "kind", "score"} <= set(e) for e in b["rag_evidence"])
