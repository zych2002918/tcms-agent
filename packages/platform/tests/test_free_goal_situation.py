"""自由目标：只说了「现象」时该怎么办。

## 起因（用户的真实反馈）

界面上给用户的提示把「不能发车」列为**期望**的例子，用户就照着输入，结果得到：

    未在目标里识别出任何故障（可用的 203 个真实故障均未命中）：'不能发车'

用户的疑问很直接：**这明明是提示词里的一个，为什么搜不到？**

## 症结

「不能发车」不是任何故障的**名字**，它是**现象**——而词典恰恰是用现象写的 desc：
`door_fault.desc = 「车门状态不可信，禁止发车」`。也就是说数据是关联的，
只是匹配方向反了：原有打分要求"故障文本 ⊂ 用户目标"，而这里需要的是
"用户目标 ⊂ 故障文本"。所以本层做**反查**。

## 本文件守的纪律（比功能本身更重要）

1. 候选**只能来自故障字典**，一条都不许编造；
2. **现象不映射到 action**：「不能发车」对应的 6 条真实故障里 5 条是 derate、
   1 条是 warning，硬映射成某一个 action 就是编造——处置必须在用户点选故障后由字典给出；
3. **数字要如实**：展示 5 条而实际 6 条时，必须说"共 6 条（显示前 5 条）"，
   不能把切片长度当总数；
4. 没有候选时**不能**再说"上面哪个最接近"——"上面"指向空气，
   比没有提示更糟。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.agent.freeform import (
    NoFaultMatch,
    faults_for_situation,
    find_situations,
    parse_free_goal,
)
from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"  # monorepo: packages/engine
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def model():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    return load_asset_model(UPSTREAM)


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    return TestClient(create_app(upstream=UPSTREAM))


# ---------------------------------------------------------------------------
# 现象识别与反查
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_recognizes_the_situation_phrases_users_actually_type(model):
    """用户说的口语形式与词典的书面形式都要认得（不能发车 / 禁止发车 / 不发车）。"""
    for phrase in ("不能发车", "禁止发车", "不发车", "无法发车", "不允许发车", "不能动车"):
        assert find_situations(phrase) == ["departure_inhibit"], phrase


@NEEDS_UPSTREAM
def test_reverse_lookup_finds_the_real_faults(model):
    """「不能发车」应反查出词典里 desc 写「禁止发车」的那些真实故障。"""
    cands, groups = faults_for_situation(model, "不能发车")
    keys = {c["key"] for c in cands}
    # 这 6 条的 name/desc/action_note 里确实写着「禁止发车」（由资产决定，非硬编码）
    assert {"door_fault", "rear_door_fault", "door_close_fail",
            "door_control_power_loss", "door_lock_fail"} <= keys
    # find_situations 返回内部代号；faults_for_situation 同时给出**给用户看的中文名**
    assert find_situations("不能发车") == ["departure_inhibit"]
    assert groups == ["不能/禁止发车"], groups
    # 每一条都必须指回真实故障字典
    for c in cands:
        assert c["key"] in model.faults_by_key
        assert c["name"] == model.faults_by_key[c["key"]].name
        assert c["matched_field"] in ("name", "desc", "action_note")
        assert c["matched_on"].startswith("situation:departure_inhibit@")


@NEEDS_UPSTREAM
def test_situation_does_not_invent_an_action(model):
    """**红线**：现象不得被硬映射成某个处置。

    「不能发车」的候选里处置并不唯一（derate 与 warning 都有）。
    若这里返回的 action 不是字典里的原值，就说明有人把现象当成了处置词。
    """
    cands, _ = faults_for_situation(model, "不能发车")
    assert cands, "应有候选"
    actions = {c["action"] for c in cands}
    assert len(actions) > 1, f"预期处置不唯一（这正是不能硬映射的理由），实际 {actions}"
    for c in cands:
        assert c["action"] == model.faults_by_key[c["key"]].action, "action 必须原样取自字典"


@NEEDS_UPSTREAM
def test_unrelated_input_yields_no_candidates(model):
    """无关输入不得被"现象反查"接住（它是兜底，不是万能钥匙）。

    注意本函数是**纯函数**：它只回答"这段文字是否表达了某个已知现象"，
    至于要不要用它，由调用方决定——app 只在解析失败（NoFaultMatch）时才查它。
    因此「车门故障 不能发车」这种既有故障名又含现象词的句子，
    本函数仍然会返回候选（它确实表达了该现象），但那不影响正常解析：
    那种句子根本走不到兜底分支（见下一条测试）。
    """
    for goal in ("今天天气不错", "帮我写一首诗", "验证超速必须降级", "空调不制冷"):
        cands, groups = faults_for_situation(model, goal)
        assert cands == [] and groups == [], f"{goal!r} 不该走现象反查"


@NEEDS_UPSTREAM
def test_naming_the_fault_still_works(model):
    """带上故障对象时仍走正常解析（现象反查不能抢戏）。"""
    p = parse_free_goal(model, "车门故障 不能发车", use_llm=False)
    assert p.fault == "door_fault"
    assert p.resolver == "rule"
    # 只给现象则照旧抛 NoFaultMatch（解析器的职责是锚定故障，不是猜）
    with pytest.raises(NoFaultMatch):
        parse_free_goal(model, "不能发车", use_llm=False)


# ---------------------------------------------------------------------------
# HTTP 层：给用户的回答
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_api_suggests_real_faults_for_a_bare_situation(client, model):
    """用户输入「不能发车」时，必须拿到可点选的真实故障，而不是一句"没找到"。"""
    d = client.post("/api/agent/free", json={"goal": "不能发车"}).json()
    assert d.get("no_match") is True
    sf = d.get("suggested_faults") or []
    assert sf, "应给出候选（此前是 0 条，用户只能干瞪眼）"
    for s in sf:
        assert s["key"] in model.faults_by_key, f"候选 {s['key']} 不在故障字典里"
    # 要说明"这是现象不是故障名"，否则用户仍不知道为什么点这些
    assert d.get("situation") and "现象" in d["situation"]["note"]


@NEEDS_UPSTREAM
def test_situation_total_is_truthful_when_truncated(client, model):
    """展示 5 条而实际更多时，必须报**总数**，不能把切片长度当总数。"""
    d = client.post("/api/agent/free", json={"goal": "不能发车"}).json()
    all_cands, _ = faults_for_situation(model, "不能发车")
    total = len(all_cands)
    assert total >= len(d["suggested_faults"])
    assert d["situation"]["total"] == total, "situation.total 必须是真实总数"
    if total > len(d["suggested_faults"]):
        assert f"共 {total} 条" in d["followup_question"], d["followup_question"]
        assert "前 5 条" in d["followup_question"]


@NEEDS_UPSTREAM
def test_followup_never_says_above_when_there_is_nothing_above(client):
    """没有候选时不能说「上面哪个」——"上面"指向空气比没有提示更糟。"""
    d = client.post("/api/agent/free", json={"goal": "今天天气不错"}).json()
    assert d.get("no_match") is True
    assert not (d.get("suggested_faults") or [])
    q = d.get("followup_question") or ""
    assert "上面" not in q, f"没有候选却让用户看「上面」：{q!r}"
    assert "故障对象" in q, "应给出可操作的建议（把故障对象也说出来）"


@NEEDS_UPSTREAM
def test_situation_note_has_no_markdown_markers(client):
    """说明文字会原样渲染在网页上，不能夹带 markdown 星号。"""
    d = client.post("/api/agent/free", json={"goal": "不能发车"}).json()
    assert "**" not in d["situation"]["note"]
    assert "**" not in (d.get("followup_question") or "")


# ---------------------------------------------------------------------------
# 界面上广告过的示例，必须真的能用
# ---------------------------------------------------------------------------
#
# 这一节防的是一类具体而尴尬的缺陷：**界面把某个句子当例子展示给用户，
# 用户照着输入却得不到结果**。「不能发车」就是这么暴露出来的
# （它被列为"期望"的例子，但解析器只认故障名）。
#
# 因此凡是写进 UI 提示/占位符/示例按钮的句子，都要在这里跑一遍。
# 新增示例时**必须同步加到这里**，否则同一个坑会再踩一次。


#: 与前端保持同步的示例清单。来源（改前端提示时请一并核对）：
#:   AgentPage.tsx  placeholder（自由目标输入框）/ 示例按钮
#:   GraphWorkspace.tsx  placeholder
ADVERTISED_GOAL_EXAMPLES = [
    "车门故障了还能发车吗",  # AgentPage placeholder + 示例按钮 + GraphWorkspace placeholder
    "超速后系统该怎么办",  # AgentPage placeholder
    "验证紧急制动失败必须停车",  # AgentPage placeholder
    "紧急制动失败会怎样",  # GraphWorkspace placeholder
]


@NEEDS_UPSTREAM
@pytest.mark.parametrize("goal", ADVERTISED_GOAL_EXAMPLES)
def test_advertised_examples_actually_parse_offline(model, goal):
    """界面上给的例子，必须**离线**（无 LLM）就能解析出真实故障。

    最后一个例子曾经是坏的：故障名是「紧急制动**执行**失败」，用户说
    「紧急制动失败」——名称不是目标的子串，而 bigram 兜底把参数传反了
    （算的是"目标有多少落在名称里"，长句一稀释就掉到阈值外）。
    """
    p = parse_free_goal(model, goal, use_llm=False)  # 故意关掉 LLM：离线也要能用
    assert p.fault in model.faults_by_key, f"解析出的 {p.fault} 不是真实故障键"
    assert p.resolver == "rule"


@NEEDS_UPSTREAM
def test_exact_match_keeps_confidence_over_approximate_match(model):
    """精确命中必须保持"确定"的置信度，不能被近似命中拖成歧义。

    goal「验证车门故障不能发车」下，`door_fault` 是精确子串命中，
    而 `rear_door_fault`（后车门故障）**包含**"车门故障"、属于近似命中。
    若近似命中也拿接近的分值，两者差距会小于「明确命中」要求的 1.0，
    置信度就从 ≥0.9 掉到 0.85 —— 本仓的既有测试正是这样变红的。
    """
    p = parse_free_goal(model, "验证车门故障不能发车", use_llm=False)
    assert p.fault == "door_fault"
    assert p.confidence >= 0.9, f"精确命中不该被降级为歧义：conf={p.confidence}"
    assert p.matched_on == "name", "应标记为精确命中（而非近似的 name~）"


@NEEDS_UPSTREAM
def test_approximate_match_is_marked_and_honest(model):
    """近似命中要如实标注（`name~`）并给较低置信度，不能冒充精确命中。"""
    p = parse_free_goal(model, "紧急制动失败会怎样", use_llm=False)
    assert p.fault == "eb_failure"
    assert "~" in p.matched_on, f"近似命中应标记 name~，实际 {p.matched_on!r}"
    assert p.confidence < 0.9, "近似命中不应给出精确命中的置信度"


@NEEDS_UPSTREAM
@pytest.mark.parametrize(
    "goal",
    ["今天天气不错", "帮我写一首诗", "1+1等于几", "空调不制冷", "列车", "故障", "测试"],
)
def test_generic_input_never_guesses_a_fault(model, goal):
    """**红线**：放松 bigram 方向与阈值之后，泛化输入仍不得被猜成某个故障。

    这些词单独出现时不含任何故障语义；解析器必须抛 NoFaultMatch
    而不是"矮子里拔将军"。阈值 0.5 正是用这批样本标定的
    （实测它们的最高覆盖率只有 0.33）。
    """
    with pytest.raises(NoFaultMatch):
        parse_free_goal(model, goal, use_llm=False)


@NEEDS_UPSTREAM
def test_symptom_style_goal_is_not_misparsed_as_a_fault(model):
    """症状式描述不得被"近似命中"抢走——否则会走错整条路径。

    **这条是真的踩过的坑**：把 bigram 方向改对、阈值放宽到 0.5 之后，
    「开门到位灯闪，门状态可能不可信」（评测集里 T-DIAGNOSE-LAMP 的
    症状式目标）被解析成故障 `door_open_timeout_passenger`（开门到位超时），
    覆盖率 0.60 —— 比应当命中的「紧急制动失败」的 0.57 还高，
    纯阈值根本分不开。后果是这条 diagnose 任务不再走症状诊断路径，
    而是去验证一个具体故障，评测基线从 10/11 掉到 9/11。

    分开它们的是**首尾锚定**：目标里有「开门到位」这个前缀，
    却没有名称尾部「超时」——那只是长句里恰好重合的片段，不是"用户提到了这个故障"。
    而「紧急制动失败」首（紧急）尾（失败）都在，中间只是插了个「执行」。
    """
    for goal in ("开门到位灯闪，门状态可能不可信", "仪表盘闪烁但无故障码"):
        with pytest.raises(NoFaultMatch):
            parse_free_goal(model, goal, use_llm=False)

    # 对照：同一个故障名，只要首尾都说到，就应当被认出来
    exact = parse_free_goal(model, "开门到位超时怎么办", use_llm=False)
    assert exact.fault == "door_open_timeout_passenger"
    assert exact.matched_on == "name", "名称整串出现属精确命中"

    # 首尾都在、但名称不是整串出现（中间插了词）→ 走近似命中，如实标 name~
    approx = parse_free_goal(model, "开门到位偶尔超时怎么办", use_llm=False)
    assert approx.fault == "door_open_timeout_passenger"
    assert "~" in approx.matched_on, f"应为近似命中，实际 {approx.matched_on!r}"


@NEEDS_UPSTREAM
def test_both_ends_rule_keeps_insertion_tolerance(model):
    """首尾锚定不能把"中间插词"这种正常的容忍能力也一起砍掉。"""
    from tcms_ai_platform.agent.freeform import _both_ends_in

    # 目标里首尾都在（中间插了「执行」）→ 允许
    assert _both_ends_in("紧急制动执行失败", "验证紧急制动失败必须停车") is True
    # 目标里只有前缀、尾部缺失 → 不允许
    assert _both_ends_in("开门到位超时", "开门到位灯闪，门状态可能不可信") is False
    # 名称很短时退化为子串包含
    assert _both_ends_in("超速", "超速后系统该怎么办") is True
    assert _both_ends_in("超速", "今天天气不错") is False
