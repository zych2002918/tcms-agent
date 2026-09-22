"""默认骨架图谱的**结构不变量**：不许出现"孤立节点"，边不许指向不存在的节点。

## 为什么需要这个文件（真实缺陷，2026-09-22）

进图谱页第一眼看到的是 `/api/kb/overview` 的骨架。实测发现里面**有 3 个节点度为 0**
（`system:SYS-AUX` 辅助供电 / `system:SYS-LIGHT` 照明 / `system:SYS-BATT` 电池储能）：
它们在图上是**没有任何连线的孤零零的点**。用户的第一反应不是"这三个域没有代表故障"，
而是"图谱坏了吧 / 怎么显示不全"。

成因（`kb_overview` 的选点规则）：代表故障只按 `function.fault_keys` 取，
而**没有任何功能挂这三个域的故障** → 它们一个成员都拿不到。

修法是沿图里**既有的边**给零度节点补一个真实成员（不发明关系）。本文件把结果钉住：

- 骨架里每个节点至少有一条边（没有孤立浮点）；
- 每条边的两端都出现在 nodes 里 —— 前端 2D/3D 都是 `if (!a || !b) return null`，
  边指向不存在的节点会被**静默丢弃**，看起来同样像"显示不全"；
- 13 个系统域与 11 个功能一个都不能少（补成员是"加"，不是"换"）；
- 结果**可复现**：同一份资产连续两次请求必须完全一致（否则截图/断言会随机漂移）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"
needs_engine = pytest.mark.skipif(not UPSTREAM.is_dir(), reason=f"上游不存在: {UPSTREAM}")


@pytest.fixture(scope="module")
def overview() -> dict:
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    client = TestClient(create_app(upstream=UPSTREAM))
    r = client.get("/api/kb/overview?limit=3")
    assert r.status_code == 200, r.text
    return r.json()


def _degrees(ov: dict) -> dict[str, int]:
    deg: dict[str, int] = {}
    for e in ov["edges"]:
        deg[e["src"]] = deg.get(e["src"], 0) + 1
        deg[e["dst"]] = deg.get(e["dst"], 0) + 1
    return deg


@needs_engine
def test_no_isolated_nodes_in_skeleton(overview: dict) -> None:
    """骨架里不许有没有连线的点 —— 那会被读成"图谱显示不全/坏了"。"""
    deg = _degrees(overview)
    isolated = [n["id"] for n in overview["nodes"] if deg.get(n["id"], 0) == 0]
    assert not isolated, (
        f"骨架出现孤立节点（度为 0，图上是一个没有连线的浮点）：{isolated}。"
        "修法：在 kb_overview 里沿图既有边给它补一个真实成员，不要靠前端兜底。"
    )


@needs_engine
def test_every_edge_endpoint_is_present(overview: dict) -> None:
    """边两端必须在节点集里：前端的 2D/3D 画布都会静默丢弃"端点缺失"的边。"""
    ids = {n["id"] for n in overview["nodes"]}
    dangling = [e for e in overview["edges"] if e["src"] not in ids or e["dst"] not in ids]
    assert not dangling, f"有 {len(dangling)} 条边的端点不在节点集里（前端会静默丢边）：{dangling[:3]}"


@needs_engine
def test_all_systems_and_functions_are_kept(overview: dict) -> None:
    """13 个系统域 + 11 个功能一个都不能少：补成员是"加节点"，不是"换节点"。"""
    kinds: dict[str, int] = {}
    for n in overview["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds.get("system", 0) >= 13, f"系统域被裁掉了：只有 {kinds.get('system', 0)} 个"
    assert kinds.get("function", 0) >= 11, f"功能被裁掉了：只有 {kinds.get('function', 0)} 个"
    assert kinds.get("fault", 0) >= 11, "代表故障不该少于功能数（每个功能至少一个）"


@needs_engine
def test_overview_is_deterministic() -> None:
    """同一份资产连续两次请求必须完全一致：否则截图与断言会随机漂移。"""
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    client = TestClient(create_app(upstream=UPSTREAM))
    a = client.get("/api/kb/overview?limit=3").json()
    b = client.get("/api/kb/overview?limit=3").json()
    assert [n["id"] for n in a["nodes"]] == [n["id"] for n in b["nodes"]]
    assert a["edges"] == b["edges"]


# ---------------------------------------------------------------------------
# 按类型列节点："一共多少"与"返回多少"必须能分开读
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    return TestClient(create_app(upstream=UPSTREAM))


@needs_engine
def test_kind_listing_total_matches_stats(client) -> None:
    """每个类型的节点总数，必须与 /api/kb/stats 的 by_kind 一致（不许静默少给）。

    真实缺陷（2026-09-22 实测）：`fault` 一类 by_kind=203，而 `/api/kb/nodes` 缺省只返回 200，
    且 body 里没有任何"被截断"的提示 —— 调用方会把 200 读成"一共 200"。
    """
    stats = client.get("/api/kb/stats").json()
    by_kind = stats["graph"]["by_kind"]
    assert len(by_kind) >= 10, f"by_kind 读不到，实际：{by_kind}"
    mismatched: list[str] = []
    for kind, expected in sorted(by_kind.items()):
        r = client.get("/api/kb/nodes", params={"kind": kind, "limit": 100000})
        assert r.status_code == 200, r.text
        got = len(r.json())
        if got != expected:
            mismatched.append(f"{kind}: by_kind={expected}，按类型列出={got}")
    assert not mismatched, "按类型列出的总数与 by_kind 不一致：\n  " + "\n  ".join(mismatched)


@needs_engine
def test_default_truncation_is_announced(client) -> None:
    """缺省 limit 会截断，但必须**显式告知**（响应头）—— 只在文档里写等于没写。"""
    r = client.get("/api/kb/nodes", params={"kind": "fault"})
    assert r.status_code == 200
    assert "X-Total-Count" in r.headers, "缺省截断没有告知总数，调用方会把返回条数当成总数"
    assert r.headers["X-Truncated"] == "true", "fault 一类必然被截断，X-Truncated 应为 true"
    total = int(r.headers["X-Total-Count"])
    assert total >= len(r.json())

    full = client.get("/api/kb/nodes", params={"kind": "fault", "limit": 100000})
    assert full.headers["X-Truncated"] == "false", "拿全量时不该再报截断"
    assert len(full.json()) == total, "全量条数应与 X-Total-Count 相等"


# ---------------------------------------------------------------------------
# 领域知识层必须"有域"，否则在按域检索里整层不可达
# ---------------------------------------------------------------------------


@needs_engine
def test_domainless_docs_stay_bounded_and_reachable(client) -> None:
    """无域文档必须**有界**：域路由检索会把无域文档整段排除，所以"无域"等于"不可达"。

    实测缺陷（2026-09-22）：阈值/联锁/状态/机制/标准/概念/危害共 **111 篇**注入文档都没有
    `domain`，于是路由一旦命中某个域（绝大多数工程问题都会），这一整层就再也搜不到：

      · 「EBM 零速阈值是多少」→ 路由 brake → 8 条命中里没有一条 threshold（文档明明存在）
      · 「开门时列车移动会怎样」→ 路由 door → 没有 interlock（那条规则讲的正是这件事）
      · 「紧急制动管理在什么状态下会缓解」→ 路由 brake → 没有任何 state 文档

    修法：按图谱邻接从**本来就有域**的邻居继承域（`domain/enrichment.py::_inherit_domains`，
    只走一跳、只用既有边，无邻居可继承的诚实留白）。实测 121 → 26。

    本用例守两件事：① 无域数量不得反弹（新注入文档要记得给域）；② 归域后确实能被按域检索召回。
    """
    stats = client.get("/api/kb/stats").json()
    empty = stats["vector"]["by_domain"].get("", 0)
    assert empty <= 30, (
        f"无域向量文档 {empty} 篇（上限 30）—— 按域检索会把它们整段排除。"
        "新增领域知识文档时请在 meta 里给 domain，或确认它能从图谱邻居继承到域。"
    )

    r = client.post("/api/kb/search", json={"query": "EBM 零速阈值是多少", "k": 5})
    assert r.status_code == 200, r.text
    ids = [h["doc_id"] for h in r.json()["hits"]]
    assert any(i.startswith("threshold:") for i in ids), (
        f"阈值类文档在按域检索里仍召回不到（该查询路由到 brake）：{ids}"
    )
