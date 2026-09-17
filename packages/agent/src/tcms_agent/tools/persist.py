"""R3 持久化工具：唯一会改变系统状态的副作用，因此**必须过人工审批**。

两个工具，各自带一道独立于审批的**机器门禁**（审批是人的判断，门禁是程序的判断，
两者不能互相替代）：

    write_memory      写长期记忆 → 门禁：内容里引用的每个资产 id 必须真实存在
    promote_artifact  把沙箱产物提升为正式归档 → 门禁：产物必须真的在沙箱里

为什么记忆写入要卡"引用门禁"：
长期记忆一旦被幻觉污染，会通过后续检索**自我强化**——这比单次回答出错严重得多。
所以纪律是：**进得了长期记忆的，必须是能机器核实的**。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..knowledge import KnowledgeContext
from ..permissions import Permission
from .registry import ToolSpec

MEMORY_KINDS = ("episodic", "procedural", "semantic")


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


@dataclass
class PersistentStore:
    """长期记忆与正式归档的落点。"""

    memory_dir: Path
    artifacts_dir: Path

    def memory_file(self, kind: str, title: str) -> Path:
        slug = _slugify(title)
        d = Path(self.memory_dir) / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{slug}.md"

    def artifact_file(self, name: str) -> Path:
        d = Path(self.artifacts_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{_slugify(name)}.json"


def _slugify(text: str) -> str:
    """把标题转成安全文件名（中文保留，其余替换）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "").strip(), flags=re.UNICODE).strip("-")
    return (s or "untitled")[:60]


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

_WRITE_MEMORY_PARAMS = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "记忆标题（作为文件名，宜简短明确）"},
        "content": {"type": "string", "description": "记忆正文（Markdown）"},
        "kind": {
            "type": "string",
            "enum": list(MEMORY_KINDS),
            "description": "记忆类型：episodic(某次运行的事实) / procedural(可复用技能) / semantic(领域结论)",
        },
        "refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "该记忆引用的真实资产 id 列表（如 fault:door_fault、req:SR-21、"
                "scenario:door_cascade.yaml）。**必须全部真实存在**，否则写入会被拒绝。"
            ),
        },
    },
    "required": ["title", "content", "kind", "refs"],
}

_PROMOTE_PARAMS = {
    "type": "object",
    "properties": {
        "archive": {
            "type": "string",
            "description": "沙箱中执行产物的相对路径（run_scenario/verify_fault_action 结果里的 archive 字段）",
        },
        "name": {"type": "string", "description": "正式归档名（不含扩展名）"},
    },
    "required": ["archive", "name"],
}


def _write_memory(
    ctx: KnowledgeContext,
    store: PersistentStore,
    args: dict[str, Any],
    run_id: str,
    check_ref,
) -> dict:
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    kind = str(args.get("kind") or "").strip()
    refs = [str(r).strip() for r in (args.get("refs") or []) if str(r).strip()]
    if not title or not content:
        return {"error": "write_memory 需要非空 title 与 content"}
    if kind not in MEMORY_KINDS:
        return {"error": f"kind 必须是 {list(MEMORY_KINDS)} 之一，收到: {kind!r}"}
    if not refs:
        return {"error": "write_memory 必须给出 refs（长期记忆不允许无出处的结论）"}

    # ---- 机器门禁：引用必须全部真实存在 ----
    bad = [r for r in refs if not check_ref(r)[0]]
    if bad:
        return {
            "error": f"写入门禁拒绝：以下引用在真实资产中不存在，疑似幻觉: {bad}",
            "gate": "reference_check",
            "rejected_refs": bad,
            "hint": "先用 kb_search / fault_detail 确认正确的资产 id 再写入",
        }

    path = store.memory_file(kind, title)
    front = {
        "title": title,
        "kind": kind,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_id": run_id,
        "refs": sorted(set(refs)),
        "refs_verified": True,
    }
    yaml_lines = "\n".join(
        f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()
    )
    path.write_text(f"---\n{yaml_lines}\n---\n\n{content}\n", encoding="utf-8")
    return {
        "written": str(path),
        "kind": kind,
        "refs_verified": len(refs),
        "gate": "reference_check passed",
        "note": "该记忆已通过引用门禁；后续检索可复用",
    }


def _promote_artifact(
    store: PersistentStore, sandbox_root: Path, args: dict[str, Any]
) -> dict:
    rel = str(args.get("archive") or "").strip()
    name = str(args.get("name") or "").strip()
    if not rel or not name:
        return {"error": "promote_artifact 需要 archive 与 name"}
    src = Path(sandbox_root) / rel
    root = Path(sandbox_root).resolve()
    try:
        resolved = src.resolve()
    except OSError as e:
        return {"error": f"归档路径无法解析: {e}"}
    # 门禁：只允许沙箱内的产物（拒绝路径穿越与任意文件读取）
    if not str(resolved).startswith(str(root)):
        return {"error": "promote_artifact 只允许提升沙箱内的产物（路径越界已拒绝）"}
    if not resolved.is_file():
        return {"error": f"沙箱中不存在该产物: {rel}"}

    dst = store.artifact_file(name)
    shutil.copy2(resolved, dst)
    blob = dst.read_bytes()
    return {
        "promoted_to": str(dst),
        "source": rel,
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest()[:16],
        "note": "产物已提升为正式归档，可长期追溯",
    }


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_persist_tools(
    ctx: KnowledgeContext,
    store: PersistentStore,
    sandbox_root: Path,
    *,
    run_id: str = "adhoc",
) -> list[ToolSpec]:
    """构建 R3 持久化工具（**必须**配合人工审批使用）。"""
    from ..nodes import check_reference  # noqa: PLC0415 - 避免模块级循环导入

    def _wm(args: dict) -> dict:
        return _write_memory(ctx, store, args, run_id, lambda r: check_reference(r, ctx))

    def _pa(args: dict) -> dict:
        return _promote_artifact(store, sandbox_root, args)

    return [
        ToolSpec(
            name="write_memory",
            description=(
                "把一条**已验证**的结论写入长期记忆（跨运行复用）。"
                "refs 必须给出真实资产 id 且全部存在，否则会被引用门禁拒绝。"
                "该操作会改变系统状态，需人工审批。"
            ),
            level=Permission.PERSIST,
            parameters=_WRITE_MEMORY_PARAMS,
            func=_wm,
        ),
        ToolSpec(
            name="promote_artifact",
            description=(
                "把沙箱里的执行产物提升为正式归档（长期可追溯）。"
                "archive 用 run_scenario / verify_fault_action 返回的 archive 字段。"
                "该操作会改变系统状态，需人工审批。"
            ),
            level=Permission.PERSIST,
            parameters=_PROMOTE_PARAMS,
            func=_pa,
        ),
    ]


__all__ = ["MEMORY_KINDS", "PersistentStore", "build_persist_tools"]
