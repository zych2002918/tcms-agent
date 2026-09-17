"""命令行入口：把 Agent 的能力暴露成可复现的命令。

    tcms-agent tools                列出工具面与权限级别（自检）
    tcms-agent run "<目标>"          跑一个目标（默认离线规则臂，--llm 用真模型）
    tcms-agent history <thread_id>  回放某次运行的轨迹
    tcms-agent doctor               环境自检（知识底座 / 模型 / 存储）

纪律：所有命令都能离线跑；`--llm` 只在真有 key 时生效，否则如实降级并提示。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import AgentConfig
from .models import llm_available
from .permissions import Permission
from .runner import AgentRunner


def _force_utf8() -> None:
    """让 Windows 控制台也能输出中文/emoji（否则 UnicodeEncodeError）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 重配失败不影响主流程
        pass


def cmd_tools(args: argparse.Namespace) -> int:
    from .graph import build_registry
    from .knowledge import build_knowledge

    k = build_knowledge(Path(args.upstream) if args.upstream else None)
    cfg = AgentConfig(upstream=Path(args.upstream) if args.upstream else None)
    reg = build_registry(k, cfg)
    print(f"知识底座：{json.dumps(k.stats(), ensure_ascii=False)}")
    print(f"\n工具面（本次开放到 {cfg.max_level.label}）：")
    for t in reg.describe():
        mark = "✓" if t["allowed"] else "✗"
        print(f"  {mark} [{t['level_label']}] {t['name']}")
        print(f"      {t['description'][:88]}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:  # noqa: ARG001
    from .knowledge import build_knowledge

    print(f"tcms-agent {__version__}")
    ok = True
    try:
        k = build_knowledge()
        print(f"  知识底座  OK  {json.dumps(k.stats(), ensure_ascii=False)}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  知识底座  FAIL {type(e).__name__}: {e}")
    has = llm_available()
    print(f"  LLM key   {'已配置（可用 --llm）' if has else '未配置（将使用离线规则臂）'}")
    db = AgentConfig().db_path
    print(f"  轨迹存储  {db}（目录可写：{db.parent.exists() or _can_mkdir(db.parent)}）")
    if not has:
        print("\n提示：无 key 也能完整运行（离线规则臂走同一张图与同一套工具）。")
    return 0 if ok else 1


def _can_mkdir(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _interactive_approver(payload: dict) -> list[dict]:
    """终端交互式审批：逐项询问，默认拒绝（直接回车 = 不批准）。"""
    print("\n" + "=" * 62)
    print("⚠️  需要人工审批 —— Agent 请求执行持久化写入")
    print("=" * 62)
    print(str(payload.get("note") or ""))
    for i, r in enumerate(payload.get("requests") or [], 1):
        print(f"\n[{i}] 工具：{r.get('name')}   （级别：{r.get('level_label')}）")
        print(f"    参数：{json.dumps(r.get('args'), ensure_ascii=False)[:600]}")
    decisions: list[dict] = []
    for i, r in enumerate(payload.get("requests") or [], 1):
        try:
            ans = input(f"\n批准 [{i}] {r.get('name')} 执行？[y/N] ").strip().lower()
        except EOFError:  # 非交互环境（管道）→ 视为拒绝
            ans = "n"
        decisions.append(
            {
                "approved": ans in ("y", "yes"),
                "reason": "终端交互审批" if ans in ("y", "yes") else "终端未批准",
            }
        )
    return decisions


def cmd_run(args: argparse.Namespace) -> int:
    max_level = Permission.PERSIST if args.allow_write else Permission.EXECUTE
    cfg = AgentConfig(
        offline=not args.llm,
        max_steps=args.max_steps,
        max_level=max_level,
        upstream=Path(args.upstream) if args.upstream else None,
        db_path=Path(args.db) if args.db else AgentConfig().db_path,
    )
    if args.llm and not llm_available():
        print("[!] --llm 指定了真模型，但未检测到可用 key → 自动降级为离线规则臂。")

    approver = None
    if max_level >= Permission.PERSIST:
        if args.yes:
            print("[!] --yes：所有持久化写入将被**自动批准**（不推荐，仅用于受控演示）。")
            approver = lambda _p: {"approved": True, "reason": "--yes 自动批准"}  # noqa: E731
        elif args.no:
            print("[i] --no：持久化写入将一律被拒绝。")
        else:
            approver = _interactive_approver

    runner = AgentRunner(cfg)
    res = runner.run(args.goal, thread_id=args.thread, approver=approver)
    print(res.report)
    print()
    print(f"thread_id = {res.thread_id}   model = {res.model_kind}")
    print(f"工具审计：{json.dumps(res.audit, ensure_ascii=False)}")
    if res.approvals:
        print(f"人工审批：{json.dumps(res.approvals, ensure_ascii=False)}")
    print("\n轨迹：")
    for t in res.trace:
        print(f"  [{t['step']:>2}] {t['node']:<7} {t['event']:<16} {t['detail'][:96]}")
    if args.json:
        print("\n" + json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.passed else 2


def cmd_history(args: argparse.Namespace) -> int:
    cfg = AgentConfig(db_path=Path(args.db) if args.db else AgentConfig().db_path)
    runner = AgentRunner(cfg)
    rows = runner.history(args.thread)
    if not rows:
        print(f"未找到 thread_id={args.thread} 的轨迹（检查 --db 路径）")
        return 1
    print(f"thread_id={args.thread} 共 {len(rows)} 个状态快照：")
    for r in rows:
        nxt = ",".join(r["next"]) or "<结束>"
        print(
            f"  #{r['index']:<3} steps={r['steps']:<2} trace={r['trace_len']:<3} "
            f"evidence={r['evidence_len']:<2} next={nxt:<10} finished={r['finished']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tcms-agent",
        description="TCMS 全流程测试工程师 Agent（LangGraph 编排）",
    )
    p.add_argument("--version", action="version", version=f"tcms-agent {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tools", help="列出工具面与权限级别")
    t.add_argument("--upstream", help="上游引擎目录（默认自动解析）")
    t.set_defaults(func=cmd_tools)

    d = sub.add_parser("doctor", help="环境自检")
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("run", help="跑一个目标")
    r.add_argument("goal", help="自然语言目标，如「验证车门故障必须触发降级处置」")
    r.add_argument("--llm", action="store_true", help="使用真 LLM（需 key，否则自动降级）")
    r.add_argument("--max-steps", type=int, default=12, help="最大决策步数（默认 12）")
    r.add_argument("--thread", help="指定 thread_id（默认自动生成）")
    r.add_argument("--db", help="轨迹数据库路径")
    r.add_argument("--upstream", help="上游引擎目录（默认自动解析）")
    r.add_argument("--json", action="store_true", help="同时输出完整 JSON")
    r.add_argument(
        "--allow-write",
        action="store_true",
        help="开放 R3 持久化工具（写长期记忆 / 提升归档）——默认关闭",
    )
    r.add_argument("--yes", action="store_true", help="自动批准所有持久化写入（不推荐）")
    r.add_argument("--no", action="store_true", help="一律拒绝持久化写入（配合 --allow-write）")
    r.set_defaults(func=cmd_run)

    h = sub.add_parser("history", help="回放某次运行的轨迹")
    h.add_argument("thread", help="thread_id")
    h.add_argument("--db", help="轨迹数据库路径")
    h.set_defaults(func=cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
