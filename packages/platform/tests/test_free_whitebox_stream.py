"""自由目标路径的白盒流：把「等待动画」换成真实步骤。

## 为什么单独守这条路径

`/api/agent/run/stream` 只覆盖了「任务列表」那条路径；而用户天天用的是
**自由目标**（一句话 → 解析 → 真实执行）。那条路径此前是普通 POST，
运行期间界面只能显示"请求处理中…"——于是出现一个刺眼的落差：
**系统白盒了，用户看到的还是等待动画。**

## 本文件守四件事

1. **流的骨架与任务路径同构**（start → trace* → result → end）：前端复用同一个
   组件与渲染逻辑，不为"自由目标"再写一套白盒；
2. **末尾的 report 与 `POST /api/agent/free` 同构**：两条链各拼一份必然漂移
   （本项目在"注释承诺了、代码没做"这类问题上已踩过多次），所以用测试钉住；
3. **未锚定故障不是错误**：同样以 `result` 交付，由前端渲染候选引导——
   诚实地说"没锚定到"，而不是继续转圈；
4. **"这次到底是谁在决策"如实进流**：换模型必须是可审计的实验，
   报告里要能回答"用的是哪个模型、是跟随设置还是本次手动指定"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"  # monorepo: packages/engine
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

#: 界面上广告过的示例（test_free_goal_situation.ADVERTISED_GOAL_EXAMPLES 同一句），
#: 离线即可解析到 door_fault —— 拿它测流，才代表用户真实会敲的东西。
GOAL = "车门故障了还能发车吗"
UNRELATED = "今天天气不错"


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    return TestClient(create_app(upstream=UPSTREAM))


def _events(client, path: str, **params) -> list[dict]:
    """读一条 SSE 流，返回全部事件（顺序即时间顺序）。"""
    out: list[dict] = []
    with client.stream("GET", path, params=params) as r:
        assert r.status_code == 200, r.text
        assert "text/event-stream" in r.headers.get("content-type", "")
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def _traces(evs: list[dict]) -> list[dict]:
    return [e["entry"] for e in evs if e["type"] == "trace"]


def _result(evs: list[dict]) -> dict:
    got = [e["report"] for e in evs if e["type"] == "result"]
    assert len(got) == 1, f"应当恰好有一个 result 事件，实际 {len(got)}"
    return got[0]


# ---------------------------------------------------------------------------
# 1. 骨架：与任务路径同构
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_free_stream_skeleton_matches_task_path(client):
    """start → trace* → result → end，且 start 里带上这次的目标。"""
    evs = _events(client, "/api/agent/free/stream", goal=GOAL)
    types = [e["type"] for e in evs]
    assert types[0] == "start", types[:3]
    assert types[-1] == "end", types[-3:]
    assert "result" in types and "error" not in types, types
    start = evs[0]
    assert start["kind"] == "free"
    assert start["goal"] == GOAL
    assert "llm" in start, "start 必须如实带上本次使用的后端与模型"


@NEEDS_UPSTREAM
def test_parse_step_comes_first_and_is_structured(client):
    """第一条真实步骤是**目标解析**，且载荷里是解析事实（不是又一次转述）。"""
    evs = _events(client, "/api/agent/free/stream", goal=GOAL)
    tr = _traces(evs)
    assert tr and tr[0]["step"] == "parse", [t["step"] for t in tr][:5]
    p = tr[0]["payload"]
    assert p["stage"] == "目标解析"
    assert p["goal"] == GOAL


@NEEDS_UPSTREAM
def test_execution_steps_and_payloads_are_present(client):
    """解析之后是真实执行链：至少出现决策/执行/断言，且各自带结构化载荷。

    白盒的料如果只有一句话（detail），前端就只能再编一层解释——
    这不是"不够好看"，而是又变回了转述。所以载荷必须真的在。
    """
    evs = _events(client, "/api/agent/free/stream", goal=GOAL)
    steps = {t["step"] for t in _traces(evs)}
    assert {"act", "exec", "verify"} <= steps, f"缺少真实执行步骤：{sorted(steps)}"
    by_step = {t["step"]: t for t in _traces(evs)}
    assert by_step["act"]["payload"].get("candidates"), "决策步骤必须给出完整候选集"
    assert by_step["exec"]["payload"].get("scenario"), "执行步骤必须给出真实场景文件"
    assert "injected" in by_step["exec"]["payload"], "执行步骤必须摊开实际注入了哪些故障"
    assert by_step["verify"]["payload"].get("relevant") is not None, "断言步骤必须给逐条明细"


@NEEDS_UPSTREAM
def test_timeline_is_one_continuous_clock(client):
    """解析与执行共用同一条时间轴（从用户提交那一刻起），不能中途重启计时。

    否则界面上会出现"解析用了 1.2s → 执行从 0.0s 开始"这种自相矛盾的时间，
    白盒反而显得不可信。
    """
    evs = _events(client, "/api/agent/free/stream", goal=GOAL)
    ts = [t["t"] for t in _traces(evs)]
    assert ts == sorted(ts), f"时间戳必须单调不减：{ts}"
    assert ts[-1] > ts[0], "整条链应当有可观察的耗时"


# ---------------------------------------------------------------------------
# 2. 与 POST 同构（防两条链漂移）
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_stream_report_is_isomorphic_to_post(client):
    """流末尾的 report 与 `POST /api/agent/free` **同构**。

    这条是本文件里最重要的守卫：前端只写一套渲染逻辑，靠的就是这个同构。
    一旦有人让两条链分叉（改了一边忘另一边），这里立刻红。
    """
    post = client.post("/api/agent/free", json={"goal": GOAL}).json()
    report = _result(_events(client, "/api/agent/free/stream", goal=GOAL))

    for k in ("goal", "parsed", "matched_task_id", "total", "achieved", "success_rate", "runs"):
        assert k in report, f"流式 report 缺少 POST 有的字段：{k}"
    assert report["goal"] == post["goal"] == GOAL
    assert report["parsed"] == post["parsed"]
    assert report["matched_task_id"] == post["matched_task_id"]
    assert report["total"] == post["total"] and report["achieved"] == post["achieved"]
    # runs 的关键结论必须一致（轨迹条数允许不同：流式多推了解析那一条）
    r0, p0 = report["runs"][0], post["runs"][0]
    assert r0["task_id"] == p0["task_id"] and r0["achieved"] == p0["achieved"]


# ---------------------------------------------------------------------------
# 3. 没锚定 ≠ 错误
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_unanchored_goal_is_delivered_as_result_not_error(client):
    """无关输入（如"今天天气不错"）以 `result.no_match` 交付，不是 error。

    用户看到的应当是"我没锚定到，这里是候选/换说法建议"，而不是一个红条或
    一个永远转下去的圈——后者才是这条路径以前真正的体验。
    """
    evs = _events(client, "/api/agent/free/stream", goal=UNRELATED)
    assert "error" not in [e["type"] for e in evs], evs
    report = _result(evs)
    assert report.get("no_match") is True, report
    assert report.get("followup_question"), "未锚定时必须给用户下一步（不能只说失败）"
    # 解析步骤要如实说"没锚定"，而不是继续显示"命中故障"（第二条 parse 才是结论）
    parse_events = [t for t in _traces(evs) if t["step"] == "parse"]
    assert any(p["payload"].get("no_match") is True for p in parse_events), parse_events


# ---------------------------------------------------------------------------
# 4. 谁在决策：如实标注 + 本次覆盖
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_offline_backend_is_reported_as_mock(client):
    """没配 key 时如实说 mock（离线 Mock 决策），绝不写成 llm。"""
    evs = _events(client, "/api/agent/free/stream", goal=GOAL)
    llm = evs[0]["llm"]
    assert llm["backend"] == "mock"
    assert llm["model"] is None
    assert llm["note"], "必须说明为什么不是 LLM"
    # POST 路径同样如实（同一份身份信息）
    assert client.post("/api/agent/free", json={"goal": GOAL}).json()["llm"]["backend"] == "mock"
    assert client.post("/api/agent/run", json={"task_id": "T-DOOR"}).json()["llm"]["backend"] == "mock"


@NEEDS_UPSTREAM
def test_model_override_is_reported_verbatim(client, monkeypatch):
    """本次运行指定的模型必须**原样**出现在身份里（换模型是可审计的实验）。

    这里不连真实端点：让"有 key"成立 + 打桩对话函数，就足以验证
    "指定的模型确实被用上、且被如实报告"。真实调用由 `tcms-agent eval` 负责。
    """
    from tcms_ai_platform.agent import llm_backend

    monkeypatch.setattr(llm_backend, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(llm_backend.LLMAgentBackend, "_chat", lambda self, s, u: None)

    evs = _events(
        client,
        "/api/agent/free/stream",
        goal=GOAL,
        model="tiny-test-model",
        base_url="https://example.invalid/v1",
    )
    llm = evs[0]["llm"]
    assert llm["backend"] == "llm"
    assert llm["model"] == "tiny-test-model"
    assert llm["base_url"] == "https://example.invalid/v1"
    assert llm["override"] is True, "手动指定的模型必须与'跟随设置'区分开"


@NEEDS_UPSTREAM
def test_default_identity_follows_settings_chain(client, monkeypatch):
    """不指定模型时，身份来自统一解析链（跟随设置），且标 override=False。"""
    from tcms_ai_platform.agent import llm_backend
    from tcms_ai_platform.agent.llm_backend import resolve_llm_config

    monkeypatch.setattr(llm_backend, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(llm_backend.LLMAgentBackend, "_chat", lambda self, s, u: None)

    evs = _events(client, "/api/agent/free/stream", goal=GOAL)
    llm = evs[0]["llm"]
    base, model = resolve_llm_config()
    assert (llm["base_url"], llm["model"]) == (base, model)
    assert llm["override"] is False
