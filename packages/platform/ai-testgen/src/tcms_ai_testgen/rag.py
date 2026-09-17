"""RAG 检索层（P4-A）：给 LLM 记忆——上游真实测试金标索引与检索。

动机（第一性原理）：LLM 生成测试用例质量不稳（幻觉域值、断言错配、面窄），
因为它没有「见过真正通过验证的测试长什么样」。上游 tcms-can-test 有 960 个
手写、全绿、覆盖率 97.96% 的 pytest——这是**测试金标**（human expert 上界）。
RAG = 生成/修正时把最相关的金标片段喂给 LLM 当 few-shot，让它模仿已验证的
断言形态而非凭空发挥。

设计决策：
    * 金标单元 = 单个测试函数 {file, name, params, docstring, body}；
      提取用 Python 标准库 tokenize/文本切分，不依赖 AST 之外的库；
    * 索引 = 关键词倒排（docstring+body 的中英文关键词），检索 = 词频打分
      + 结构信号（故障键/信号名/报文的精确匹配加权）——离线、可复现、
      零成本；不做 embedding（向量库对 636 个文本单元属过度设计，且引入
      网络/模型依赖违背离线优先）；
    * retrieve(query) -> top_k 金标（默认 3），供 prompt 注入；
    * 索引上游失败（目录不存在等）时抛错由调用方捕获，不影响 mock 管线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: 从文本提取英文/中文关键词（信号名/故障键/动作词是索引主力）
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}|[\u4e00-\u9fff]{2,4}")


def _tokenize(text: str) -> list[str]:
    """分词：英文标识符额外按下划线拆分（door_fault -> door/fault），
    否则整词 test_door_fault_blocks 无法被 query 'door' 命中。"""
    out: list[str] = []
    for w in _WORD_RE.findall(text):
        if "_" in w and w[0].islower():
            out.append(w)
            out.extend(p for p in w.split("_") if len(p) >= 2 and not p.isdigit())
        else:
            out.append(w)
    return out


@dataclass
class GoldenCase:
    """一条金标测试（上游真实手写用例的结构化切片）。"""

    file: str  # 相对 tests/ 的文件名
    name: str
    params: str  # fixture 参数（db / bus, db, simulator ...）
    docstring: str = ""
    body: str = ""
    tokens: list[str] = field(default_factory=list)

    def snippet(self, max_chars: int = 400) -> str:
        """prompt 友好的单条片段（docstring + 前几行断言体）。"""
        parts = [f"# 金标来自 {self.file}::{self.name}"]
        if self.docstring:
            parts.append(f'"""{self.docstring}"""')
        body_lines = self.body.strip().splitlines()
        parts.append("\n".join(body_lines[:8]))
        text = "\n".join(parts)
        return text[:max_chars] + ("…" if len(text) > max_chars else "")

    def matches(self, query_tokens: set[str]) -> int:
        """与查询的重合词数（结构信号：参数含 db/simulator 加权由外部做）。"""
        return len(query_tokens & set(self.tokens))


def _extract_functions(text: str, file_name: str) -> list[GoldenCase]:
    """从测试文件文本提取函数单元（按 def 行切分，含 docstring/body）。"""
    out: list[GoldenCase] = []
    # 用 finditer 定位所有 def test_ 起点，再切到下一个 def 或文件尾
    starts = [m.start() for m in re.finditer(r"^def test_\w+", text, re.M)]
    for idx, pos in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        block = text[pos:end]
        name_m = re.match(r"def (test_\w+)", block)
        if not name_m:
            continue
        name = name_m.group(1)
        # 参数：第一个 ( 到配对的第一个 ) 行
        params = ""
        pm = re.search(r"\((.*?)\)\s*:", block, re.S)
        if pm:
            params = re.sub(r"\s+", " ", pm.group(1)).strip()
        # docstring：def 行后首个三引号
        docstring = ""
        dm = re.search(r'"""\s*(.*?)\s*"""', block, re.S)
        if dm:
            docstring = dm.group(1)
        # body：去掉 def 头行与 docstring 后的断言体（docstring 属描述）
        lines = block.splitlines()
        # 去掉第一行 def ...:（含可能的续行参数）
        body_lines = lines[1:]
        # 若 body_lines 开头是 docstring 行，去掉
        if dm:
            # 从 docstring 结束位置之后开始
            after_doc = block[dm.end():]
            body_lines = after_doc.splitlines()
        body = "\n".join(body_lines).strip()
        if not body:
            body = "\n".join(lines[1:]).strip()  # 无 docstring 时去掉 def 头即可
        tokens = _tokenize(name + " " + docstring + " " + body)
        out.append(
            GoldenCase(file=file_name, name=name, params=params,
                       docstring=docstring, body=body, tokens=tokens)
        )
    return out


class GoldenIndex:
    """上游测试金标索引（构建一次，多次检索）。注意类名不以 Test 开头，
    避免被 pytest 误收集。"""

    def __init__(self, cases: Optional[list[GoldenCase]] = None):
        self.cases: list[GoldenCase] = cases or []
        self._by_token: dict[str, list[int]] = {}
        for i, c in enumerate(self.cases):
            for t in set(c.tokens):
                self._by_token.setdefault(t, []).append(i)

    @classmethod
    def from_tests_dir(cls, tests_dir: str | Path) -> "GoldenIndex":
        d = Path(tests_dir)
        if not d.is_dir():
            raise FileNotFoundError(f"tests 目录不存在: {d}")
        cases: list[GoldenCase] = []
        for f in sorted(d.glob("test_*.py")):
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            cases.extend(_extract_functions(text, f.name))
        return cls(cases)

    @property
    def size(self) -> int:
        return len(self.cases)

    def retrieve(self, query: str, top_k: int = 3) -> list[GoldenCase]:
        """按查询检索最相关金标（词频 + 精确信号加权）。"""
        qtokens = set(_tokenize(query))
        if not qtokens:
            return []
        scored: list[tuple[int, int, GoldenCase]] = []  # (score, idx, case)
        for i, c in enumerate(self.cases):
            score = c.matches(qtokens)
            if score == 0:
                continue
            # 结构信号：参数含 simulator 的用例更「可执行参考」
            if "simulator" in c.params:
                score += 1
            scored.append((score, i, c))
        scored.sort(key=lambda x: (-x[0], -len(x[2].body)))
        return [c for _, _, c in scored[:top_k]]


__all__ = ["GoldenCase", "GoldenIndex", "_tokenize"]
