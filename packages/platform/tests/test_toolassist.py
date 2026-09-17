"""P1-a 受约束 function-calling 门禁：编排真工具 / 未开放拦截 / 参数校验 / 诚实降级。

- FakeChat 只负责"决定调哪个工具"，工具执行一律落到真实知识底座（m/g/hr）。
- 红线断言：未开放工具不执行、非法 JSON 拒绝执行、无 key 绝不假装调过工具、
  回复自证 used_tools。
"""

from __future__ import annotations

from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


class FakeChat:
    """脚本化 tool_calls 决策器：记录收到的 system/user/extra，逐轮返回预设。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def _chat_tools(self, system, user, tools, extra_messages=None):
        self.calls.append({"system": system, "user": user, "extra": extra_messages or []})
        nxt = self.script.pop(0) if self.script else {"content": "（空回复）", "calls": []}
        return nxt.get("content"), nxt.get("calls", [])


@NEEDS_UPSTREAM
def _kb():
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    enrich_graph(g, vs)
    return m, g, HybridRetriever(vs, g)


@NEEDS_UPSTREAM
def test_orchestrator_executes_real_tool_and_self_reports():
    """LLM 先要求 kb_search → 编排器用真实底座执行 → 次轮纯文本收尾并自证工具。"""
    from tcms_ai_platform.agent.toolassist import assist

    m, g, hr = _kb()
    fake = FakeChat(
        [
            {"content": None, "calls": [{"id": "c1", "name": "kb_search", "arguments": '{"query": "车门 联锁 发车"}'}]},
            {"content": "根据 kb_search 命中的真实资产：door_fault 与联锁相关，建议验证门联锁场景。", "calls": []},
        ]
    )
    res = assist(m, g, hr, "车门故障会影响发车吗", chat=fake, max_rounds=3)
    assert res["llm_generated"] is True
    assert res["used_tools"] == ["kb_search"], "必须如实自证用过的工具"
    assert res["rounds"] == 2
    assert "kb_search" in res["reply"] and "自证" in res["reply"]
    # 编排器确实向真实底座执行过：工具调用被记录到 extra（role=tool）
    assert any(any(msg.get("role") == "tool" for msg in c["extra"]) for c in fake.calls)


@NEEDS_UPSTREAM
def test_unregistered_tool_is_blocked():
    """LLM 请求未开放工具 → 拦截不执行，且不出现在 used_tools（错误回填给模型）。"""
    from tcms_ai_platform.agent.toolassist import assist

    m, g, hr = _kb()
    fake = FakeChat(
        [
            {"content": None, "calls": [{"id": "x", "name": "delete_all", "arguments": "{}"}]},
            {"content": "我不会执行未开放工具。", "calls": []},
        ]
    )
    res = assist(m, g, hr, "试试删掉全部", chat=fake, max_rounds=3)
    assert res["used_tools"] == [], "未开放工具不得进入 used_tools"
    assert res["llm_generated"] is True
    # 拦截错误以 role=tool 回填给模型（真实可审计），且未执行任何工具
    tool_msgs = [m0["content"] for c in fake.calls for m0 in c["extra"] if m0.get("role") == "tool"]
    assert any("已拦截" in str(x) for x in tool_msgs)


@NEEDS_UPSTREAM
def test_malformed_json_arguments_rejected():
    """工具参数非合法 JSON → 拒绝执行并以 role=tool 错误回填。"""
    from tcms_ai_platform.agent.toolassist import assist

    m, g, hr = _kb()
    fake = FakeChat(
        [
            {"content": None, "calls": [{"id": "b", "name": "kb_search", "arguments": "not-json"}]},
            {"content": "参数有问题，已放弃该调用。", "calls": []},
        ]
    )
    res = assist(m, g, hr, "查询一下", chat=fake, max_rounds=3)
    assert res["llm_generated"] is True
    tool_msgs = [m0["content"] for c in fake.calls for m0 in c["extra"] if m0.get("role") == "tool"]
    assert any("不是合法 JSON" in str(x) for x in tool_msgs)


def test_no_key_falls_back_to_honest_rule_reply(monkeypatch):
    """无可用 chat（llm_available=False）→ llm_generated=false，绝不假装调用工具。"""
    from tcms_ai_platform.agent.toolassist import assist

    monkeypatch.setattr("tcms_ai_platform.agent.llm_backend.llm_available", lambda: False)
    res = assist(object(), object(), object(), "随便问问", chat=None, use_llm=True)
    assert res["llm_generated"] is False
    assert res["used_tools"] == []
    assert "没有真正调用工具" in res["reply"] or "未接入" in res["reply"]


@NEEDS_UPSTREAM
def test_no_key_can_answer_warning_runnable_question_by_rule(monkeypatch):
    """无 LLM 也能确定性回答『什么只是警告/降级但仍能运行』：枚举 action∈warning/derate 真实故障。"""
    from tcms_ai_platform.agent.toolassist import assist

    m, g, hr = _kb()
    monkeypatch.setattr("tcms_ai_platform.agent.llm_backend.llm_available", lambda: False)
    res = assist(m, g, hr, "什么失效了只是警告但能正常运行", chat=None, use_llm=True)
    assert res["llm_generated"] is False
    assert res.get("enumeration", {}).get("kind") == "rule_enum_warning_derate_runnable"
    assert res["enumeration"]["count"] > 0, "故障字典中应有 action∈warning/derate 的真实故障"
    assert "示例" in res["reply"] and "停运" in res["reply"]


@NEEDS_UPSTREAM
def test_kb_filter_assets_real_filters():
    """kb_filter_assets：action/level 过滤只出对应处置；scenario 用 keyword；坏 kind 报错。"""
    from tcms_ai_platform.agent.toolassist import run_tool_safe

    m, g, hr = _kb()
    r = run_tool_safe("kb_filter_assets", {"kind": "fault", "action": "warning", "limit": 5}, m, g, hr)
    assert r["count"] >= 1 and all(it["action"] == "warning" for it in r["items"])
    r2 = run_tool_safe("kb_filter_assets", {"kind": "fault", "action": "shutdown"}, m, g, hr)
    assert all(it["action"] == "shutdown" for it in r2["items"])
    r3 = run_tool_safe("kb_filter_assets", {"kind": "scenario", "keyword": "制动"}, m, g, hr)
    assert r3["count"] >= 1 and all("制动" in it["name"] or "制动" in it["file"] for it in r3["items"])
    assert run_tool_safe("kb_filter_assets", {"kind": "bogus"}, m, g, hr).get("error")


@NEEDS_UPSTREAM
def test_pure_tools_return_real_data():
    """工具执行（纯函数）返回真实资产；错误路径诚实报错。"""
    from tcms_ai_platform.agent.toolassist import run_tool_safe

    m, g, hr = _kb()
    # kb_search 命中真实资产
    r = run_tool_safe("kb_search", {"query": "车门 联锁 发车"}, m, g, hr)
    assert r.get("hits"), "kb_search 应命中真实资产"
    assert all(h.get("doc_id") for h in r["hits"])
    # symptom_diagnose 确定性 + 不编造
    r2 = run_tool_safe("symptom_diagnose", {"text": "仪表盘闪烁但无故障码"}, m, g, hr)
    assert r2["matched"] is True and r2["symptom_key"] == "dashboard_flicker"
    assert all(c["fault"] in m.faults_by_key for c in r2["candidates"])
    # list_scenarios 按故障过滤
    r3 = run_tool_safe("list_scenarios", {"fault_key": "overspeed"}, m, g, hr)
    assert r3["count"] >= 1
    # 错误路径诚实
    assert run_tool_safe("kb_node", {"node_id": "fault:not_exist"}, m, g, hr).get("error")
    assert run_tool_safe("no_such_tool", {}, m, g, hr).get("error")
    assert run_tool_safe("kb_search", "not-dict", m, g, hr).get("error")


def test_agent_free_warning_runnable_kb_answer():
    """/api/agent/free 对『什么只是警告但能正常运行』返回规则枚举答案（无 key 也答）。"""
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    app = create_app(upstream=UPSTREAM)
    client = TestClient(app)
    r = client.post("/api/agent/free", json={"goal": "什么失效了只是警告但能正常运行"})
    assert r.status_code == 200
    b = r.json()
    assert b["no_match"] is True
    assert b.get("kb_answer") and "示例" in b["kb_answer"]
    assert b["kb_items"]["count"] > 0
    assert b["suggested_faults"], "应给可点选的真实故障候选"
    for s in b["suggested_faults"]:
        assert s["key"] and s["name"]
        assert s["action"] in ("warning", "derate"), "只列告警/降级运行类"


def test_http_toolassist_contract_without_key():
    """HTTP：无 key（use_llm=false 强制）→ 200 + 契约字段 + 诚实降级。"""
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    app = create_app(upstream=UPSTREAM)
    client = TestClient(app)
    r = client.post("/api/agent/toolassist", json={"message": "车门故障影响发车吗", "use_llm": False})
    assert r.status_code == 200
    b = r.json()
    for k in ("reply", "llm_generated", "used_tools", "rounds", "tools_available"):
        assert k in b, f"缺字段 {k}"
    assert b["llm_generated"] is False
    assert set(b["tools_available"]) == {
        "kb_search",
        "symptom_diagnose",
        "kb_node",
        "list_scenarios",
        "kb_filter_assets",
    }
