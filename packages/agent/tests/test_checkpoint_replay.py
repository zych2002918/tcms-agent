"""轨迹持久化与回放（harness 的核心能力之一）。

checkpoint 不是"日志"，而是**可回放、可续跑、可审计的状态**：
每次运行绑定 thread_id，每个超级步后的完整状态都会落盘。
M5 的情景记忆直接建在这之上。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tcms_agent.runner import AgentRunner


def test_state_is_persisted_to_sqlite(cfg, knowledge) -> None:
    res = AgentRunner(cfg, knowledge).run("验证车门故障必须触发降级处置")
    assert res.thread_id.startswith("run-")
    db = Path(cfg.db_path)
    assert db.is_file(), "checkpoint 数据库应被创建"
    with sqlite3.connect(str(db)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert any("checkpoint" in t for t in tables), f"应有 checkpoint 表，实际: {sorted(tables)}"


def test_history_replays_the_superstep_sequence(cfg, knowledge) -> None:
    runner = AgentRunner(cfg, knowledge)
    res = runner.run("验证车门故障必须触发降级处置")
    snaps = runner.history(res.thread_id)
    assert len(snaps) >= 5, f"应有多步快照，实际 {len(snaps)}"
    # 最新快照（列表首位）应是终局
    assert snaps[0]["finished"] is True
    # 轨迹长度沿时间单调不减（倒序看即递增）
    lens = [s["trace_len"] for s in snaps]
    assert lens == sorted(lens, reverse=True), f"trace 应随超级步累积: {lens}"


def test_distinct_thread_ids_do_not_mix(cfg, knowledge) -> None:
    runner = AgentRunner(cfg, knowledge)
    a = runner.run("验证车门故障必须触发降级处置", thread_id="thread-a")
    b = runner.run("验证超速必须触发降级", thread_id="thread-b")
    assert a.thread_id == "thread-a" and b.thread_id == "thread-b"
    hist_a = runner.history("thread-a")
    hist_b = runner.history("thread-b")
    assert hist_a and hist_b
    # 两条线程的终态步数各自独立，不会互相污染
    assert hist_a[0]["finished"] and hist_b[0]["finished"]


def test_history_of_unknown_thread_is_empty(cfg, knowledge) -> None:
    assert AgentRunner(cfg, knowledge).history("no-such-thread") == []
