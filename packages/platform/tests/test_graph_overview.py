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
