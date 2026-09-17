"""把 Anthropic HTML 指南转成精简 markdown(调研存档)。"""
import re
import html

src = "docs/research/anthropic-effective-agents.html"
s = open(src, encoding="utf-8", errors="ignore").read()
s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)

def htag(m):
    lvl = int(m.group(1))
    return "\n\n" + "#" * min(lvl, 4) + " "

s = re.sub(r"<h([1-4])[^>]*>", htag, s)
s = re.sub(r"</h[1-4]>", "\n", s)
s = re.sub(r"<li[^>]*>", "\n- ", s)
s = re.sub(r"<[^>]+>", " ", s)
s = html.unescape(s)
s = re.sub(r"[ \t]+", " ", s)
s = re.sub(r"\n\s*\n+", "\n\n", s)
open("docs/research/anthropic-building-effective-agents.md", "w", encoding="utf-8").write(s)
print("saved chars:", len(s))
