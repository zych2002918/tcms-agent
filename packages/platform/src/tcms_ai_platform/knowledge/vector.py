"""P2 向量索引：轻量、离线优先、可插拔 embedder。

设计（红线：知识底座不依赖任何 LLM API 即可跑通）：
- `Embedder` 抽象：`embed(text) -> np.ndarray`。默认 `HashedEmbedder`：
  确定性 token 哈希 → 归一化向量（中文按字符 + 英文按词切分，无需分词库），
  保证离线可用且对"故障/信号名/枚举"这类短结构化文本有效。
- 可插拔：真语义近义为**可选**通道——`ApiEmbedder`（OpenAI 兼容
  /embeddings，无 key/模型探测失败/请求失败 → 自动降级 HashedEmbedder）；
  本地 BGE/sentence-transformers 同样只需实现同一接口并注入 store，
  检索代码零改动。
  **注意：默认哈希通道无近义能力——本平台"语义"的准确措辞是"字符级确定性
  通道 + 图谱显式边证据的 GraphRAG 风格混合检索"。**
- `VectorStore`：add(document) / search(query, k) → 带 score 的命中。
  score = cosine；命中带 doc_id / kind / text / meta，供 GraphRAG 混合。

语料来源（P2 种子，全部真实）：
    FMEA 故障条目（desc/detect/inject/recovery）、场景描述、RTM verifies、
    被测功能描述 —— 由 store 构建器从 AssetModel 派生。
"""

from __future__ import annotations

import hashlib
import json as _json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np


@dataclass
class Doc:
    doc_id: str  # 全局唯一（如 fault:overspeed / req:SR-01）
    kind: str  # fault / requirement / function / scenario
    text: str  # 检索文本
    meta: dict = field(default_factory=dict)


class Embedder(ABC):
    @abstractmethod
    def embed(self, text: str) -> np.ndarray: ...

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """批量嵌入(基座实现=逐条 embed)。真语义实现(API)可覆盖为单次批量请求。"""
        return [self.embed(t) for t in texts]


def _tokens(text: str) -> list[str]:
    """轻量切分：中文按字 + 英文按词 + 数字。无需分词库。"""
    # 英文/数字词
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text)
    # 中文字符（去空白）
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    out = [w.lower() for w in words]
    out.extend(chars)
    return out


class HashedEmbedder(Embedder):
    """确定性 token 哈希向量（离线，维度 DIM）。"""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in _tokens(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


class ApiEmbedder(Embedder):
    """OpenAI 兼容 /embeddings 真语义向量通道（P0-2，**可选**）。

    设计（诚实降级，与 LLM 后端"无 key 自动落回 Mock"同一哲学）：
    - 显式开启（调用方确认端点可用）后才有 API 网络调用；未开启/无 key/
      base_url 缺失/模型探测失败/请求失败 → 自动落回 fallback（默认
      HashedEmbedder），离线与断网行为与旧版逐字节一致。
    - 模型解析优先级：构造显式 model → env EMBEDDING_MODEL（由上层
      make_kb_embedder 注入）→ GET /models 自动探测（取 id 含
      embedding/text-vec 的第一个，缓存于实例）。
    - embed_batch 按 chunk 分批；单批失败 → 该批逐条降级，不丢文档。
    - api_active：最近一次批量嵌入是否真正走了 API（供上层"近义增益 golden"
      判断当前通道是否可用，避免把"降级后的哈希余弦"误当真语义证据）。
    """

    _EMBEDDING_HINTS = ("embedding", "text-vec", "text-vector")

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str | None = None,
        fallback: Embedder | None = None,
        timeout: float = 25.0,
        chunk: int = 64,
        client=None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = (model or "").strip() or None
        self.fallback = fallback if fallback is not None else HashedEmbedder()
        self.timeout = timeout
        self.chunk = max(1, chunk)
        self.api_active = False  # 最近一次批量嵌入是否成功走真 API
        self._client = client  # 测试注入 httpx.Client(MockTransport)
        self._lazy_client = None
        self._resolved_model: str | None = None
        self._resolve_done = False

    # ---- 内部：客户端 / 模型解析 ----

    def _http(self):
        if self._client is not None:
            return self._client
        if self._lazy_client is None:
            import httpx

            self._lazy_client = httpx.Client(timeout=self.timeout)
        return self._lazy_client

    def _resolve_model(self) -> str | None:
        """返回当前生效的 embedding 模型 id；不可用返回 None（走降级）。"""
        if self._resolve_done:
            return self._resolved_model
        self._resolve_done = True
        if self.model:
            self._resolved_model = self.model
            return self._resolved_model
        if not self.api_key or not self.base_url:
            return None
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            r = self._http().get(f"{self.base_url}/models", headers=headers, timeout=self.timeout)
            if r.status_code != 200:
                return None
            for it in (r.json().get("data") or []):
                mid = str(it.get("id") or "")
                s = f"{mid} {it.get('owned_by') or ''}".lower()
                if any(h in s for h in self._EMBEDDING_HINTS):
                    self._resolved_model = mid
                    return mid
        except Exception:  # noqa: BLE001 - 探测失败视为不可用（诚实降级）
            return None
        return None

    def _api_embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """单次 /embeddings 请求；任何失败返回 None（不抛，交由上层降级）。"""
        m = self._resolve_model()
        if not m or not self.api_key or not self.base_url:
            return None
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        r = self._http().post(url, headers=headers, json={"model": m, "input": list(texts)}, timeout=self.timeout)
        if r.status_code != 200:
            return None
        rows = []
        for d in r.json().get("data") or []:
            emb = d.get("embedding")
            if not emb:
                return None
            rows.append(list(emb))
        return rows if len(rows) == len(texts) else None

    # ---- Embedder 接口 ----

    def embed(self, text: str) -> np.ndarray:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(texts), self.chunk):
            chunk = list(texts[i : i + self.chunk])
            try:
                rows = self._api_embed_batch(chunk)
                if rows is not None:
                    self.api_active = True
                    out.extend(np.asarray(r, dtype=np.float32) for r in rows)
                    continue
            except Exception:  # noqa: BLE001 - 任一环节失败 → 该批逐条降级
                pass
            self.api_active = False
            out.extend(self.fallback.embed(t) for t in chunk)
        return out


class VectorStore:
    """内存向量库（可分片有界）：add / search（余弦）。

    有界分层设计（Q4）：
    - 每个 doc 可带 domain 分区标签（meta["domain"]），search 可按 domains 过滤 →
      检索先路由到域、再域内语义 topk（不会在庞大库中迷失）。
    - 分区容量上限：add 时可传 partition_caps {domain: max_docs}；超限自动拒绝
      （fail-loud 防静默溢出），由调用方做淘汰/归档。
    - 无 domain 的 doc 归 "" 分区（全局兼容，原行为不变）。
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        partition_caps: dict[str, int] | None = None,
    ) -> None:
        self.embedder = embedder or HashedEmbedder()
        self.docs: list[Doc] = []
        self._vectors: list[np.ndarray] = []
        self.partition_caps = dict(partition_caps or {})

    def _domain_of(self, doc: Doc) -> str:
        return str(doc.meta.get("domain", "") or "")

    def add(self, doc: Doc) -> bool:
        """加文档；返回是否成功（超分区上限时拒绝并返回 False，不静默溢出）。"""
        dom = self._domain_of(doc)
        cap = self.partition_caps.get(dom)
        if cap is not None:
            cur = sum(1 for d in self.docs if self._domain_of(d) == dom)
            if cur >= cap:
                return False
        self.docs.append(doc)
        self._vectors.append(self.embedder.embed(doc.text))
        return True

    def add_many(self, docs: list[Doc]) -> int:
        """批量加文档；返回成功数（超分区上限的拒绝）。嵌入走 embed_batch
        （API 通道一次请求多句，失败逐条降级；哈希通道行为与逐条一致）。"""
        if not docs:
            return 0
        vecs = self.embedder.embed_batch([d.text for d in docs])
        accepted = 0
        for d, v in zip(docs, vecs):
            dom = self._domain_of(d)
            cap = self.partition_caps.get(dom)
            if cap is not None:
                cur = sum(1 for x in self.docs if self._domain_of(x) == dom)
                if cur >= cap:
                    continue
            self.docs.append(d)
            self._vectors.append(v)
            accepted += 1
        return accepted

    def partition_stats(self) -> dict:
        """各分区文档数（供 UI/自检展示「有界索引」）。"""
        out: dict[str, int] = {}
        for d in self.docs:
            dom = self._domain_of(d)
            out[dom] = out.get(dom, 0) + 1
        return out

    def search(self, query: str, k: int = 8, domains: list[str] | None = None) -> list[dict]:
        """余弦 topk；domains 非空时只在该分区内检索（图谱路由后调用）。"""
        if not self.docs:
            return []
        idx = list(range(len(self.docs)))
        if domains is not None:
            dset = {d for d in domains if d != ""} | (set() if "" in (domains or []) else set())
            if dset or "" in (domains or []):
                idx = [
                    i
                    for i in idx
                    if (self._domain_of(self.docs[i]) in dset)
                    or (self._domain_of(self.docs[i]) == "" and "" in (domains or []))
                ]
                if not idx:
                    return []
        qv = self.embedder.embed(query)
        scores = []
        for i in idx:
            s = float(np.dot(qv, self._vectors[i]))
            scores.append((s, i))
        scores.sort(key=lambda t: t[0], reverse=True)
        out = []
        for s, i in scores[:k]:
            d = self.docs[i]
            out.append(
                {
                    "doc_id": d.doc_id,
                    "kind": d.kind,
                    "text": d.text,
                    "meta": d.meta,
                    "score": round(s, 4),
                }
            )
        return out

    def stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for d in self.docs:
            by_kind[d.kind] = by_kind.get(d.kind, 0) + 1
        return {"docs": len(self.docs), "by_kind": by_kind, "by_domain": self.partition_stats()}


# ---------------------------------------------------------------------------
# 语料构建：从 AssetModel 派生真实语料
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 域分区（Q4 有界分层：检索先路由到域，再域内向量 topk；向量默认字符级哈希，
# 真语义嵌入为可选通道（ApiEmbedder），与词法/图谱证据同层融合）
# ---------------------------------------------------------------------------
# 域词汇单一真源 = domain/data/domain_systems.json（13 系统域，每个 system 带
# domain 标签字段）：system/code→标签、subsystem→标签、device→标签全部从该
# JSON 派生，与 enrichment.inject_systems 的 system 文档分区同口径，
# 杜绝"向量一套词汇 / 图谱一套词汇"的漂移。JSON 缺失/损坏时退回内建兜底表。

_DOMAIN_JSON = Path(__file__).resolve().parent.parent / "domain" / "data" / "domain_systems.json"

# 内建兜底（与 JSON 同口径；正常路径 JSON 是唯一来源）
_FALLBACK_SYSTEM_DOMAIN: dict[str, str] = {
    "SYS-TRAIN": "network",
    "SYS-BRAKE": "brake",
    "SYS-TRACTION": "traction",
    "SYS-DOOR": "door",
    "SYS-PANTO": "pantograph",
    "SYS-BATT": "battery",
    "SYS-AUX": "aux",
    "SYS-HVAC": "hvac",
    "SYS-PIS": "pis",
    "SYS-LIGHT": "light",
    "SYS-FIRE": "fire",
    "SYS-BOGIE": "bogie",
    "SYS-SENSING": "signal",
}


@lru_cache(maxsize=1)
def _domain_table() -> dict:
    """从 domain_systems.json 派生 {system: code→label, subsystem, device}。"""
    table = {
        "system": dict(_FALLBACK_SYSTEM_DOMAIN),
        "subsystem": {},
        "device": {},
    }
    try:
        data = _json.loads(_DOMAIN_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 缺文件/坏 JSON → 内建兜底，不阻断离线加载
        return table
    label_of = {}
    for s in data.get("systems", []):
        code = s["code"]
        label_of[code] = s.get("domain") or _FALLBACK_SYSTEM_DOMAIN.get(code, "")
    table["system"] = label_of
    for s in data.get("systems", []):
        lab = label_of[s["code"]]
        for sub in s.get("subsystems", []):
            table["subsystem"][sub] = lab
    for dev, code in data.get("device_system", {}).items():
        table["device"][dev] = label_of.get(code, "")
    return table


def system_domain(code: str) -> str:
    """系统码（SYS-*）→ 分区域标签。"""
    return _domain_table()["system"].get(code or "", "")


def subsystem_domain(subsystem: str) -> str:
    """故障子系统 → 分区域标签；未收录 → ''（归全局分区，不误塞错域）。"""
    return _domain_table()["subsystem"].get(subsystem or "", "")


def device_domain(device: str) -> str:
    """发送设备（DBC 节点名）→ 分区域标签（报文/信号分区用）。"""
    return _domain_table()["device"].get(device or "", "")


DOMAIN_ZH: dict[str, str] = {
    "network": "网络/列车控制",
    "brake": "制动",
    "traction": "牵引/ATP",
    "door": "车门",
    "pantograph": "受电弓/高压",
    "battery": "电池/储能",
    "aux": "辅助供电",
    "hvac": "空调暖通",
    "pis": "乘客信息",
    "light": "照明",
    "fire": "烟火安全",
    "bogie": "走行部",
    "signal": "信号/传感",
    "": "通用",
}
DOMAINS: list[str] = [
    "network",
    "brake",
    "traction",
    "door",
    "pantograph",
    "battery",
    "aux",
    "hvac",
    "pis",
    "light",
    "fire",
    "bogie",
    "signal",
    "",
]


def build_docs_from_asset(m) -> list[Doc]:
    """从 L1 AssetModel 派生检索语料（全真实，无手编），并打 domain 分区标签。

    分区口径（与 13 系统域单一真源一致）：
    - 故障   ← 所属 subsystem
    - 需求/功能 ← 其 fault_keys 主域的子系统
    - 场景   ← 首个注入故障域
    - 报文   ← 发送设备（DBC 节点）
    - 信号   ← 所属报文的发送设备
    """
    docs: list[Doc] = []

    # 故障：desc + detect + inject + recovery（分区 = 子系统域）
    for f in m.faults_by_key.values():
        dom = subsystem_domain(f.subsystem)
        docs.append(
            Doc(
                doc_id=f"fault:{f.key}",
                kind="fault",
                text=(
                    f"{f.name} {f.desc} 检测:{f.detect} 注入:{f.inject} "
                    f"恢复:{f.recovery} 等级:{f.level} 处置:{f.action} SIL:{f.sil}"
                ),
                meta={
                    "fid": f.fid,
                    "key": f.key,
                    "level": f.level,
                    "action": f.action,
                    "subsystem": f.subsystem,
                    "domain": dom,
                },
            )
        )

    # 需求 verifies（分区 = 覆盖它的功能主域；无覆盖归 ""）
    def _fault_domain(key: str) -> str:
        f = m.faults_by_key.get(key)
        return subsystem_domain(f.subsystem) if f else ""

    _REQ_DOMAIN: dict[str, str] = {}
    for fn in m.functions.values():
        dom = _fault_domain(fn.fault_keys[0]) if fn.fault_keys else ""
        for rid in fn.requirements:
            _REQ_DOMAIN.setdefault(rid, dom)
    for req_id, reqs in m.requirements.items():
        docs.append(
            Doc(
                doc_id=f"req:{req_id}",
                kind="requirement",
                text=f"{req_id} " + " ".join(r.verifies for r in reqs),
                meta={"req_id": req_id, "rows": len(reqs), "domain": _REQ_DOMAIN.get(req_id, "")},
            )
        )

    # 被测功能（分区 = 其 fault_keys 主域）

    for fn in m.functions.values():
        dom = _fault_domain(fn.fault_keys[0]) if fn.fault_keys else ""
        docs.append(
            Doc(
                doc_id=f"function:{fn.fid}",
                kind="function",
                text=f"{fn.name} {fn.description}",
                meta={"fid": fn.fid, "requirements": list(fn.requirements), "domain": dom},
            )
        )

    # 场景（分区 = 注入故障主域；多域场景归第一注入域）
    for s in m.scenarios.values():
        steps_txt = []
        for st in s.steps:
            if st.action == "inject":
                steps_txt.append(
                    f"在{st.at}s注入{st.fault}(节点{st.node})期望{st.expect}"
                )
            else:
                steps_txt.append(f"在{st.at}s恢复{st.fault}")
        dom = _fault_domain(next(iter(s.fault_keys), "")) if s.fault_keys else ""
        docs.append(
            Doc(
                doc_id=f"scenario:{s.file}",
                kind="scenario",
                text=f"{s.name} {' '.join(steps_txt)}",
                meta={"file": s.file, "domain": dom},
            )
        )

    # 报文/信号（分区 = 发送设备所属系统域；让检索能命中"心跳/门/空调/轴温"等）
    for msg in m.messages.values():
        dom = device_domain(msg.node)
        docs.append(
            Doc(
                doc_id=f"message:{msg.name}",
                kind="message",
                text=(
                    f"报文 {msg.name} 发送节点{msg.node} 周期{msg.cycle_ms}ms "
                    f"类型{msg.send_type} 信号:" + ",".join(msg.signal_names)
                ),
                meta={"name": msg.name, "cycle_ms": msg.cycle_ms, "node": msg.node, "domain": dom},
            )
        )
    for sig in m.signals.values():
        unit = f"单位{sig.unit}" if sig.unit else ""
        choices = " 取值:" + ",".join(f"{k}={v}" for k, v in sig.choices.items()) if sig.choices else ""
        msg = m.messages.get(sig.message)
        dom = device_domain(msg.node) if msg else ""
        docs.append(
            Doc(
                doc_id=f"signal:{sig.name}",
                kind="signal",
                text=f"信号 {sig.name} 属于报文{sig.message} {unit} 范围{sig.minimum}~{sig.maximum}{choices}",
                meta={"name": sig.name, "message": sig.message, "domain": dom},
            )
        )

    return docs
