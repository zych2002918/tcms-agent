"""诊断会话锚点记忆（P1-1）：多轮追问的"证据引用式"上下文。

设计（借鉴成熟 agent harness 的"会话状态"模式，但严格贴合诊断诚实纪律）：
- **只存锚点，不存散文摘要**：每轮记忆 = 上一轮命中的症状资产引用（symptom key
  + 完整 symptom dict）+ 上一轮真实候选故障键（证据引用）+ 用户连续输入的现象
  事实（原样短文本，最多 N 条）。语义本体始终留在图谱/症状资产上，防止
  "压缩摘要丢语义 / 摘要漂移"。
- 追问触发：用户输入含"刚才/上一轮/再说/继续/那个部位"等指代词、且本轮直接
  检索落空时 → 复用锚点症状继续走因果链（conversation continuity）。
- TTL 清理：会话过期自动清除（不泄漏、不无限增长）；`cleanup` 由每次诊断调用触发。

线程安全：服务端 FastAPI 同步端点跑在线程池，用锁保护读改写。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

# 追问/指代词（P1-1）：命中这些词视为"接着上一轮聊"，即使本轮文本直配落空
FOLLOW_UP_TOKENS = (
    "刚才",
    "上一轮",
    "上一条",
    "之前那个",
    "再说一下",
    "再说",
    "继续",
    "还是那个",
    "跟刚才",
    "那个部位",
    "刚才那个",
    "补充一下",
    "另外还有",
)

MAX_FACTS = 4  # 每会话最多保留的现象事实条数


def is_follow_up(text: str) -> bool:
    """是否包含追问/指代词（据此决定是否允许复用上轮症状锚点）。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(k in t for k in FOLLOW_UP_TOKENS)


def build_anchor(resp: dict, query: str) -> dict:
    """从一轮 diagnose 响应构建锚点（只保留证据引用，不存散文）。"""
    sym = resp.get("symptom")
    cands = resp.get("candidates") or []
    return {
        "symptom": dict(sym) if sym else None,
        "candidates": [
            {"fault": c.get("fault"), "name": c.get("name"), "domain": c.get("domain")}
            for c in cands[:6]
            if c.get("fault")
        ],
        "facts": [str(query).strip()[:200]],
        "updated_at": time.time(),
    }


class AnchorMemory:
    """进程内会话锚点存储（TTL 过期清理；键=session_id）。"""

    def __init__(
        self,
        ttl_s: float = 1800.0,
        now: Callable[[], float] = time.time,
        max_sessions: int = 200,
    ) -> None:
        self.ttl_s = ttl_s
        self._now = now
        self.max_sessions = max_sessions
        self._store: dict[str, dict] = {}
        self._lock = threading.RLock()

    def get(self, session_id: str | None) -> dict | None:
        if not session_id:
            return None
        with self._lock:
            a = self._store.get(session_id)
            if a is None:
                return None
            if self._now() - float(a.get("updated_at", 0.0)) > self.ttl_s:
                self._store.pop(session_id, None)
                return None
            return dict(a)  # 浅拷贝：facts/candidates 由调用方自行扩展

    def put(self, session_id: str, anchor: dict) -> None:
        if not session_id:
            return
        with self._lock:
            prev = self._store.get(session_id)
            # 合并事实队列（保留最近 MAX_FACTS 条，防无限增长）
            facts = list(prev.get("facts", [])) if prev else []
            new_facts = [f for f in (anchor.get("facts") or []) if f and f not in facts]
            facts = (new_facts + facts)[:MAX_FACTS]
            merged = dict(anchor)
            merged["facts"] = facts
            merged["updated_at"] = self._now()
            self._store[session_id] = merged
            # 会话数上限兜底：超限丢最旧（低水位清扫）
            if len(self._store) > self.max_sessions:
                for sid in sorted(
                    self._store, key=lambda s: float(self._store[s].get("updated_at", 0.0))
                )[: len(self._store) - self.max_sessions]:
                    self._store.pop(sid, None)

    def cleanup(self) -> int:
        """清除过期会话；返回清除数。每次诊断入口调用。"""
        with self._lock:
            expired = [
                sid
                for sid, a in self._store.items()
                if self._now() - float(a.get("updated_at", 0.0)) > self.ttl_s
            ]
            for sid in expired:
                self._store.pop(sid, None)
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
