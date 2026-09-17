"""框架版 vs 手写版：可复跑的对照报告。

回答"自己写的怎么跟成熟框架比"这个问题。结论分三层，全部由代码/实测支撑：

1. **任务结果：完全一致**（实测 11/11 逐条相同、指标分毫不差）。
   框架不参与决策——决策在节点里，而节点两版共用。
2. **编排代码量：框架版更短**（声明式状态合并与条件边 vs 手写 merge 与 while）。
3. **能力集：框架版多出的部分不是"更短的代码"，而是"手写要花更多代码且极易错"的能力**
   —— 断点续跑 / 中断审批 / 轨迹回放 / 流式观测。

第 3 条是本项目从 R1 到 R8 一路验证出来的：为 R3b 的 human-in-the-loop 付出的
最大代价不是写审批逻辑，而是**搞清"被中断的节点会从头重跑、副作用必须放在
interrupt 之后"**（当时用最小实验实测才确认）。手写版把这条直接放弃了（自动拒绝）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def code_size() -> dict[str, int]:
    """对比两版**有效行数**（非空、非纯注释），在运行时实测而非手抄。"""
    here = Path(__file__).resolve().parent

    def _loc(p: Path) -> int:
        if not p.is_file():
            return 0
        return sum(
            1 for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )

    files = {
        "nolib_loop": here / "loop.py",
        "graph": here.parent / "graph.py",
        "runner": here.parent / "runner.py",
        "nodes(共用)": here.parent / "nodes.py",
        "state(共用)": here.parent / "state.py",
    }
    return {k: _loc(v) for k, v in files.items()}


@dataclass
class Capability:
    name: str
    graph: str
    nolib: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {"capability": self.name, "graph": self.graph, "nolib": self.nolib, "evidence": self.evidence}


def capability_matrix() -> list[Capability]:
    """能力对照（每条都指出证据来源，不空口对比）。"""
    return [
        Capability("任务结果", "✅", "✅", "实测同集 11/11 逐条一致，指标相同"),
        Capability("工具权限与审计", "✅", "✅", "两版共用同一个 ToolRegistry"),
        Capability("状态落盘", "✅", "❌", "graph：checkpoint 表；nolib：不写任何库"),
        Capability("断点续跑", "✅", "❌", "graph：history() 逐超级步快照；nolib：无"),
        Capability("人工审批中断", "✅ 暂停等人", "❌ 自动拒绝", "graph：interrupt/resume；nolib：无挂起能力"),
        Capability("轨迹回放", "✅", "❌", "graph：get_state_history；nolib：只有返回值"),
        Capability("流式观测", "✅", "❌", "graph：stream()；nolib：跑完才见结果"),
        Capability("编排代码量", "更短", "更长", "见 code_size()：声明式 vs 手写 merge/while"),
    ]


def verify_capabilities(cfg: Any, knowledge: Any, tmp_dir: Path) -> dict[str, bool]:
    """**用事实验证**能力差异，而不是只写在表里。

    验证三件事：框架版确实落盘了 checkpoint；nolib 确实什么都没落；
    需审批的调用在 nolib 上确实被自动拒绝（而不是假装成功）。
    """
    import sqlite3
    from pathlib import Path as _P

    from ..permissions import Permission
    from ..runner import AgentRunner
    from .loop import run_goal

    check: dict[str, bool] = {}
    tmp_dir = _P(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    goal = "验证车门故障必须触发降级处置"

    # 框架版：应有 checkpoint 表 + history 快照
    g_root = tmp_dir / "graph"
    g_cfg = cfg.with_(
        db_path=g_root / "cp.sqlite", memory_dir=g_root / "memory",
        sandbox_dir=g_root / "sandbox", artifacts_dir=g_root / "artifacts",
    )
    runner = AgentRunner(g_cfg, knowledge)
    res = runner.run(goal, thread_id="cap-graph")
    db = _P(g_cfg.db_path)
    tables: set[str] = set()
    if db.is_file():
        with sqlite3.connect(str(db)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check["graph_writes_checkpoint"] = any("checkpoint" in t for t in tables)
    check["graph_has_replay"] = len(runner.history("cap-graph")) >= 5
    check["graph_run_passed"] = res.passed

    # nolib：不写任何 checkpoint 库
    n_root = tmp_dir / "nolib"
    n_cfg = g_cfg.with_(
        db_path=n_root / "cp.sqlite", memory_dir=n_root / "memory",
        sandbox_dir=n_root / "sandbox", artifacts_dir=n_root / "artifacts",
    )
    n_res = run_goal(goal, cfg=n_cfg, knowledge=knowledge, run_id="cap-nolib")
    check["nolib_run_passed"] = n_res.passed
    check["nolib_writes_no_checkpoint"] = not _P(n_cfg.db_path).is_file()

    # 需审批的写入：nolib 只能自动拒绝，且如实记录
    p_root = tmp_dir / "persist"
    p_cfg = g_cfg.with_(
        max_level=Permission.PERSIST,
        db_path=p_root / "cp.sqlite", memory_dir=p_root / "memory",
        sandbox_dir=p_root / "sandbox", artifacts_dir=p_root / "artifacts",
    )
    p_res = run_goal(goal, cfg=p_cfg, knowledge=knowledge, run_id="cap-nolib-persist")
    check["nolib_auto_rejects_approval"] = bool(p_res.approvals) and all(
        not a["approved"] for a in p_res.approvals
    )
    check["nolib_rejection_is_disclosed"] = any(
        "nolib" in str(t.get("detail", "")) or "无法暂停" in str(t.get("detail", ""))
        for t in p_res.trace
    )
    check["nolib_never_silently_writes"] = not list((p_root / "memory").rglob("*.md"))
    return check


def parity_report(tasks: list[Any], cfg: Any, knowledge: Any, workdir: Path) -> dict[str, Any]:
    """在**同一套任务集**上跑两个引擎，逐条比对结果。"""
    from ..eval.harness import Arm, run_arm

    wd = Path(workdir)
    g = run_arm(Arm("graph", {"offline": True}), tasks, base_cfg=cfg, knowledge=knowledge,
                workdir=wd / "graph")
    n = run_arm(Arm("nolib", {"offline": True}, engine="nolib"), tasks, base_cfg=cfg,
                knowledge=knowledge, workdir=wd / "nolib")
    ids = [v.task_id for v in g.verdicts]
    diffs = []
    for tid in ids:
        gv = next(v for v in g.verdicts if v.task_id == tid)
        nv = next(v for v in n.verdicts if v.task_id == tid)
        if gv.matched != nv.matched:
            diffs.append({"task": tid, "graph": gv.matched, "nolib": nv.matched})
    return {
        "tasks": len(ids),
        "graph_match_rate": g.metrics["match_rate"],
        "nolib_match_rate": n.metrics["match_rate"],
        "graph_avg_steps": g.metrics["avg_steps"],
        "nolib_avg_steps": n.metrics["avg_steps"],
        "graph_avg_tool_calls": g.metrics["avg_tool_calls"],
        "nolib_avg_tool_calls": n.metrics["avg_tool_calls"],
        "mismatches": diffs,
        "identical": not diffs,
    }


def render(code: dict[str, int], caps: list[Capability], parity: dict[str, Any] | None) -> str:
    lines = ["框架版（LangGraph） vs 手写最小 loop —— 对照报告", ""]
    lines.append("一、编排层代码量（有效行，运行时实测）")
    lines.append(f"  nolib/loop.py（自己实现编排）        {code['nolib_loop']:>4}")
    lines.append(f"  graph.py（图装配）                   {code['graph']:>4}")
    lines.append(f"  runner.py（checkpoint/审批/回放/日志）{code['runner']:>4}")
    lines.append(f"  nodes.py（两版共用，与框架无关）      {code['nodes(共用)']:>4}")
    lines.append("")
    lines.append("二、能力对照")
    head = f"  {'能力':<16}{'graph':<14}{'nolib':<14}证据"
    lines += [head, "  " + "-" * (len(head) + 20)]
    for c in caps:
        lines.append(f"  {c.name:<16}{c.graph:<14}{c.nolib:<14}{c.evidence}")
    if parity:
        lines += ["", "三、同集实测"]
        lines.append(f"  任务 {parity['tasks']} 条："
                     f"graph {parity['graph_match_rate']:.0%} / nolib {parity['nolib_match_rate']:.0%}")
        lines.append(f"  平均步数 {parity['graph_avg_steps']} / {parity['nolib_avg_steps']}；"
                     f"平均工具调用 {parity['graph_avg_tool_calls']} / {parity['nolib_avg_tool_calls']}")
        lines.append(
            "  逐条一致" if parity["identical"] else f"  不一致：{parity['mismatches']}"
        )
        lines += [
            "",
            "结论：**框架不参与决策，因此不改变任务结果**；它买到的是可落盘、可续跑、",
            "可中断、可回放——这些手写要更多代码，而且极易在'副作用执行几次'这类细节上出错。",
        ]
    return "\n".join(lines)


__all__ = ["Capability", "capability_matrix", "code_size", "parity_report", "render", "verify_capabilities"]
