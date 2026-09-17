"""记忆召回：把"以前跑过的运行"和"沉淀下来的技能"按当前目标找回来。

## 召回通道的诚实口径

用 platform 的 `HashedEmbedder`（字符级哈希词袋）做相似度，**与知识底座同一条通道口径**。
它衡量的是**字符重合度**，不是语义：

- 「验证车门故障必须触发降级处置」 vs 「验证车门故障不能发车」→ 高重合 ✓
- 「车厢门打不开」 vs 「车门故障」→ 重合有限（这需要真语义通道，见 R6 的 RAG 升级）

**不把字符重合度说成语义检索**——这条纪律在原三仓里就是明文规定，这里继续遵守。
好处是确定、可复现、零网络，适合做回归门禁；缺口也明确写在案。

## 为什么召回要区分 episodic / procedural

两者用途不同、对 Agent 的价值也不同：
- **episodic**（我上次怎么做的、成没成）→ 帮助 Agent 复用成功路径、避开已知坑；
- **procedural**（沉淀下来的套路）→ 直接提供可执行的方法。
召回结果里分开呈现，让模型知道自己在看哪种信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .journal import RunJournal, RunRecord


@dataclass
class MemoryHit:
    """一条召回结果。"""

    kind: str  # episodic | procedural
    id: str
    title: str
    text: str
    score: float
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "score": round(self.score, 4),
            "meta": self.meta,
            "text": self.text[:300],
        }


def _cos(a: Any, b: Any) -> float:
    import numpy as np

    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(a @ b / (na * nb))


class MemoryIndex:
    """把 RunRecord 与技能文档索引成可召回的向量集合。"""

    def __init__(self, embedder: Any = None):
        if embedder is None:
            from tcms_ai_platform.knowledge.vector import HashedEmbedder

            embedder = HashedEmbedder()
        self.embedder = embedder
        self._vecs: list[Any] = []
        self._hits: list[MemoryHit] = []

    # ---- 构建 ----

    def add_episodic(self, records: list[RunRecord]) -> int:
        n = 0
        for r in records:
            text = self._episodic_text(r)
            self._hits.append(
                MemoryHit(
                    kind="episodic",
                    id=r.run_id,
                    title=f"{'✅' if r.passed else '❌'} {r.goal[:60]}",
                    text=text,
                    score=0.0,
                    meta={
                        "goal": r.goal,
                        "passed": r.passed,
                        "steps": r.steps,
                        "goal_fault": r.goal_fault,
                        "tools_used": r.tools_used,
                        "failed_tools": r.failed_tools,
                        "created_at": r.created_at,
                        "model_kind": r.model_kind,
                    },
                )
            )
            self._vecs.append(self.embedder.embed(text))
            n += 1
        return n

    def add_procedural(self, skills: list[dict[str, Any]]) -> int:
        n = 0
        for s in skills:
            text = f"{s.get('title', '')}\n{s.get('content', '')}"
            self._hits.append(
                MemoryHit(
                    kind="procedural",
                    id=str(s.get("skill_id") or s.get("title") or ""),
                    title=str(s.get("title") or ""),
                    text=str(s.get("content") or ""),
                    score=0.0,
                    meta={
                        "refs": s.get("refs") or [],
                        "evidence": s.get("evidence") or [],
                        "kind": s.get("kind") or "procedural",
                        "created_at": s.get("created_at") or "",
                    },
                )
            )
            self._vecs.append(self.embedder.embed(text))
            n += 1
        return n

    @staticmethod
    def _episodic_text(r: RunRecord) -> str:
        parts = [
            f"目标：{r.goal}",
            f"结果：{'通过' if r.passed else '未通过'}",
            f"故障：{r.goal_fault}" if r.goal_fault else "",
            f"用过的工具：{'、'.join(r.tools_used)}" if r.tools_used else "",
            f"失败的工具：{'、'.join(r.failed_tools)}" if r.failed_tools else "",
            f"未通过原因：{'；'.join(r.reasons)}" if r.reasons else "",
        ]
        return "\n".join(p for p in parts if p)

    def __len__(self) -> int:
        return len(self._hits)

    # ---- 召回 ----

    def recall(
        self,
        query: str,
        *,
        k_episodic: int = 3,
        k_procedural: int = 3,
        min_score: float = 0.05,
        include_self: str | None = None,
    ) -> list[MemoryHit]:
        """按字符重合度召回，episodic 与 procedural 分别取 top-k。

        `include_self`：排除指定 run_id（避免当前运行召回自己）。
        低于 `min_score` 的命中直接丢弃——**宁可不召回，也不塞噪音进上下文**。
        """
        if not self._hits:
            return []
        qv = self.embedder.embed(query)
        scored: list[MemoryHit] = []
        for hit, vec in zip(self._hits, self._vecs):
            if include_self and hit.id == include_self:
                continue
            s = _cos(qv, vec)
            if s < min_score:
                continue
            scored.append(
                MemoryHit(
                    kind=hit.kind, id=hit.id, title=hit.title, text=hit.text, score=s, meta=hit.meta
                )
            )
        scored.sort(key=lambda h: -h.score)
        epi = [h for h in scored if h.kind == "episodic"][:k_episodic]
        pro = [h for h in scored if h.kind == "procedural"][:k_procedural]
        return epi + pro


def build_index(memory_dir: Path, skills: list[dict[str, Any]] | None = None) -> MemoryIndex:
    """从运行日志 + 技能库构建索引。"""
    idx = MemoryIndex()
    idx.add_episodic(RunJournal.under(memory_dir).read_all())
    idx.add_procedural(skills if skills is not None else load_skills(memory_dir))
    return idx


def load_skills(memory_dir: Path) -> list[dict[str, Any]]:
    """读取程序性记忆（`<memory_dir>/procedural/*.md`，与本项目记忆格式一致）。"""
    out: list[dict[str, Any]] = []
    d = Path(memory_dir) / "procedural"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(raw)
        meta["skill_id"] = p.stem
        meta["content"] = body.strip()
        out.append(meta)
    return out


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """解析简单 frontmatter（key: json 值，每行一条；与 write_memory 的写法对应）。"""
    import json

    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    head, body = raw[3:end], raw[end + 4 :]
    meta: dict[str, Any] = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not k:
            continue
        try:
            meta[k] = json.loads(v)
        except json.JSONDecodeError:
            meta[k] = v.strip('"')
    return meta, body


def format_memory_context(hits: list[MemoryHit], max_chars: int = 2000) -> str:
    """把召回结果渲染成注入提示词的文本块。

    **首尾固定、只截中间**：抬头（说明这是记忆不是事实）与免责声明（引用仍需核实）
    必须始终可见。早先的实现把声明放在末尾，长度截断时会被吃掉——模型于是只看到
    "记忆里这么说"，看不到"不得照搬"的警告。这类缺陷测试抓到过一次。
    """
    if not hits:
        return ""
    header = "【历史记忆（供参考，不是当前事实）】"
    footer = "（以上来自你自己的历史记忆；引用资产仍需用工具核实，不得直接照搬。）"
    if max_chars <= len(header) + len(footer) + 20:
        return f"{header}\n{footer}"[:max_chars]

    lines: list[str] = []
    for h in hits:
        tag = "过往运行" if h.kind == "episodic" else "沉淀技能"
        lines.append(f"- [{tag} | 相似度 {h.score:.2f}] {h.title}")
        if h.kind == "episodic":
            m = h.meta
            extra = f"；失败工具={'、'.join(m.get('failed_tools') or [])}" if m.get("failed_tools") else ""
            lines.append(
                f"    结果={'通过' if m.get('passed') else '未通过'}；步数={m.get('steps')}；"
                f"工具={'、'.join(m.get('tools_used') or []) or '—'}{extra}"
            )
        else:
            lines.append("    " + h.text.replace("\n", " ")[:200])

    body = "\n".join(lines)
    budget = max_chars - len(header) - len(footer) - 2
    if len(body) > budget:
        body = body[: max(0, budget - 1)] + "…"
    return f"{header}\n{body}\n{footer}"


__all__ = [
    "MemoryHit",
    "MemoryIndex",
    "build_index",
    "format_memory_context",
    "load_skills",
]
