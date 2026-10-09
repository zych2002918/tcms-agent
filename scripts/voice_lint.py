"""warm-voice 的脚本项：节奏、符号、禁用词、数字守恒。

对应 SKILL.md「发送前自检」的第 5、6 项。人工看不准字数与节奏，交给脚本。
零依赖，只用标准库。默认只报不改。

**本文件是两份镜像的源头**：`~/.dsh/skills/warm-voice/voice_lint.py`（本机写作用）
与 `objects/tcms-agent/scripts/voice_lint.py`（仓库 CI 用，随仓走）。
两份必须逐字节相同 —— `tcms-agent/tests/test_voice_lint.py` 在本机会断言这一点。

用法：
  python -X utf8 voice_lint.py <file>                  # 体检一个文件
  python -X utf8 voice_lint.py <file> --json           # 机器可读
  python -X utf8 voice_lint.py --original A.md --revised B.md    # 数字守恒（B 不得多出 A 没有的数字）
  python -X utf8 voice_lint.py --selftest              # 仪器自证：故意写错，必须都能抓到

退出码：出现 high 命中 → 1；否则 0。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

# 命中即判 high 的硬清单（对应 SKILL.md 禁用表）
HARD_WORDS = [
    "赋能", "闭环", "生态", "一站式", "极致", "无缝", "显著提升",
    "值得注意的是", "需要说明的是", "综上所述", "不难看出", "让我们",
    "多个方面", "极大地", "深入地", "全方位",
]
# 命中判 medium：可能是合法用法，但要看上下文。
# 「不只是…而是」与「不是…而是」由同一条正则覆盖（`不(?:仅|只)?是`）——
# 拆成两条会让同一句被报两次（真踩过）。
SOFT_PATTERNS = [
    (r"不(?:仅|只)?是.{1,20}而是", "「不是…而是…」修辞式对举"),
    (r"首先.{0,40}其次.{0,40}(最后|最终)", "三连排比"),
    (r"作为(一个)?(AI|人工智能|语言模型)", "AI 自我声明"),
]
CONNECTIVES = ["此外", "另外", "同时", "而且", "并且"]
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2b00-\u2bff\ufe0f]"
)
STATUS_MARKS = "✅❌⬜🟩🟥⚠"
CURLY = "“”‘’"
NUMBER = re.compile(r"\d+(?:[.,]\d+)*\s*%?")
FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
TABLE_LINE = re.compile(r"^[ \t]*\|.*$", re.M)
LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t].*$", re.M)
HTML_CODE_BLOCK = re.compile(r"<(style|script|pre)\b.*?</\1\s*>", re.S | re.I)
HTML_TAG = re.compile(r"<[^>]*>", re.S)
HEADING = re.compile(r"^[ \t]*#{1,6} ")
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])|\n")


class Hit:
    def __init__(self, level: str, rule: str, detail: str, line: int = 0) -> None:
        self.level, self.rule, self.detail, self.line = level, rule, detail, line
        self.text = f"{level}\t{rule}\t{detail}" + (f"\tL{line}" if line else "")


def mask_code(text: str) -> str:
    """把代码块与行内代码换成等长空格：符号检查不该被代码里的东西污染，行号也不能变。"""
    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    return INLINE_CODE.sub(blank, FENCE.sub(blank, text))


def mask_tables(text: str) -> str:
    """把 Markdown 表格行换成空行：表格里每行都以「|」开头，不是句子，
    不遮掉的话「R0/R1/R2/R3」这种表格会被当成连续同首词句误报。"""
    return TABLE_LINE.sub(lambda m: " " * len(m.group(0)), text)


def mask_lists(text: str) -> str:
    """把 Markdown 列表项换成空格：列表是并列结构，几行同首词是设计而非 AI 味。
    （真踩过：`docs/ARCHITECTURE.md` 的「可回放 / 可续跑 / 可审计」被当成连续同首词句报 high。）
    只影响节奏判定，禁用词与句式仍在正文里查。"""
    return LIST_ITEM.sub(lambda m: " " * len(m.group(0)), text)


def mask_html(text: str) -> str:
    """把 HTML 的 <style>/<script>/<pre> 段与标签换成空格：网页也是对外文案，
    但标签不是句子、<pre> 里是代码。（真踩过两次：`site/index.html` 里每行 `<div>`
    都被当成「连续 3 句以「<」开头」；同一页的 <pre> 命令块被当成「连续 3 句以「u」开头」。）"""
    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    return HTML_TAG.sub(blank, HTML_CODE_BLOCK.sub(blank, text))


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def paragraphs(text: str) -> list[tuple[int, str]]:
    out, buf, start = [], [], 1
    for i, ln in enumerate(text.splitlines(), start=1):
        if ln.strip():
            if not buf:
                start = i
            buf.append(ln)
        elif buf:
            out.append((start, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, "\n".join(buf)))
    return out


def sentences(par: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(par) if s and s.strip()]


def first_char(s: str) -> str:
    s = re.sub(r"^[\s>*\-+#|\[\]()0-9.、]+", "", s)
    return s[:1]


def check_text(text: str) -> list[Hit]:
    hits: list[Hit] = []
    body = mask_html(mask_code(text))           # 禁用词/句式在正文里查（含表格）
    prose = mask_lists(mask_tables(body))       # 符号与节奏只在散文里查（表格/列表遮掉）

    for w in HARD_WORDS:
        for m in re.finditer(re.escape(w), body):
            hits.append(Hit("high", "禁用词", f"命中「{w}」", line_of(body, m.start())))
    for pat, why in SOFT_PATTERNS:
        for m in re.finditer(pat, body, re.S):
            hits.append(Hit("medium", "句式", why, line_of(body, m.start())))

    n_dash = prose.count("\u2014")
    if n_dash:
        hits.append(Hit("info", "破折号", f"出现 {n_dash} 处 U+2014；中文可用，但要确认不是拿它塞补充说明", 0))
    cur = [c for c in prose if c in CURLY]
    if cur:
        hits.append(Hit("medium", "符号", f"弯引号 {len(cur)} 个（AI 常见指纹）", 0))
    marks = sum(prose.count(c) for c in STATUS_MARKS)
    if marks:
        hits.append(Hit("medium", "符号", f"状态符号 {marks} 个，别拿它当段落结构", 0))
    emo = EMOJI.findall(prose)
    if emo:
        hits.append(Hit("medium", "符号", f"emoji {len(emo)} 个，别拿它当标点", 0))

    for start, par in paragraphs(prose):
        if all(HEADING.match(ln) for ln in par.splitlines() if ln.strip()):
            continue  # 纯标题段不参与节奏判定
        sents = sentences(par)
        # 节奏 A：同一段里连续三句同首词
        run = 1
        for a, b in zip(sents, sents[1:]):
            run = run + 1 if first_char(a) and first_char(a) == first_char(b) else 1
            if run >= 3:
                hits.append(Hit("high", "节奏", f"连续 3 句以「{first_char(b)}」开头", start))
                break
        # 节奏 B：段内连接词
        for c in CONNECTIVES:
            n = par.count(c)
            if n > 1:
                hits.append(Hit("medium", "节奏", f"段内「{c}」出现 {n} 次", start))
        # 节奏 C：句长无波动
        lens = [len(s) for s in sents]
        if len(lens) >= 4 and max(lens) - min(lens) <= 5:
            hits.append(
                Hit("medium", "节奏", f"{len(lens)} 句长度都落在 {min(lens)}-{max(lens)} 字，缺波动", start)
            )
    return hits


def numbers(text: str) -> Counter[str]:
    return Counter(n.replace(" ", "") for n in NUMBER.findall(mask_code(text)))


def selftest() -> int:
    """仪器自证：故意写坏，脚本必须都抓得到。抓不到就是尺子坏了。"""
    cases = [
        ("赋能全链路闭环", "high", "禁用词"),
        ("系统极大地提升了效率。", "high", "禁用词"),
        ("值得注意的是，备份完成。", "high", "禁用词"),
        ("甲走了。甲回来了。甲又走了。", "high", "节奏"),
        ("这不只是一个框架，而是一套方法论。", "medium", "句式"),
        ("他说“没问题”。", "medium", "符号"),
        ("这不是终点——只是开始。", "info", "破折号"),
    ]
    bad = 0
    for text, level, rule in cases:
        got = check_text(text)
        ok = any(h.level == level and (rule is None or h.rule == rule) for h in got)
        print(f"{'OK ' if ok else 'FAIL'} 该抓的 [{level}/{rule}] {text}")
        bad += 0 if ok else 1

    # 反向：不该抓的别抓（表格行 / 列表项 / 纯标题段 / 代码块 / HTML 标签）
    quiet = [
        ("| **R0 只读** | 自由调用 |\n| **R1 沙箱写** | 只写沙箱 |\n| **R2 真实执行** | 子进程 |", "表格行不算连续同首词句"),
        ("- 可回放（history）\n- 可续跑（崩溃恢复）\n- 可审计（状态即证据）", "列表项并列首词不算 AI 味"),
        ("<div><b>960</b></div>\n<div><b>203</b></div>\n<div><b>104</b></div>", "HTML 标签不是句子"),
        ("<pre><code>uv sync\nuv run tcms-agent run \"x\"\nuv run tcms-platform</code></pre>", "<pre> 里是代码，不是句子"),
        ("## 这是什么\n\n本仓由三个原独立仓库整合而成。", "纯标题段不参与节奏判定"),
        ("```\n✅ ⬜ —— “引号”\n```", "代码块里的符号不算"),
    ]
    for text, why in quiet:
        got = [h for h in check_text(text) if h.level in {"high", "medium"}]
        ok = not got
        print(f"{'OK ' if ok else 'FAIL'} 不该抓的 [{why}]" + ("" if ok else f" → {[h.text for h in got]}"))
        bad += 0 if ok else 1
    a, b = "全仓 1731 passed，覆盖 97.96%。", "全仓 1735 passed，覆盖 97.96%，新增 4 条。"
    extra = numbers(b) - numbers(a)
    ok = set(extra) == {"1735", "4"}
    print(f"{'OK ' if ok else 'FAIL'} [数字守恒] 多出的数字 = {sorted(extra)}")
    bad += 0 if ok else 1
    print("仪器正常" if not bad else f"仪器有问题：{bad} 项没抓到")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="warm-voice 脚本项：节奏 / 符号 / 禁用词 / 数字守恒")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--original")
    ap.add_argument("--revised")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.file:
        ap.error("要么给一个 file，要么用 --selftest")

    text = open(args.file, encoding="utf-8", errors="replace").read()
    hits = check_text(text)

    if args.original and args.revised:
        a = numbers(open(args.original, encoding="utf-8", errors="replace").read())
        b = numbers(open(args.revised, encoding="utf-8", errors="replace").read())
        for n in sorted(set(b) - set(a)):
            hits.append(Hit("high", "数字守恒", f"改稿里的 {n} 在原稿里没有", 0))

    if args.json:
        print(json.dumps([h.__dict__ for h in hits], ensure_ascii=False, indent=2))
    else:
        print(f"{args.file}: {len(hits)} 条命中")
        for h in sorted(hits, key=lambda x: {"high": 0, "medium": 1, "info": 2}[x.level]):
            print("  " + h.text)
    return 1 if any(h.level == "high" for h in hits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
