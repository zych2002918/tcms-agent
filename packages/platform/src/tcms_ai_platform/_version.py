"""版本单一真源（镜像上游纪律：展示数字必须机器可读）。"""

# 0.7.0：界面重做（现代深色 · 冷调青 + 顶部信息带 + 列车领域符号系统）+ 真浏览器 e2e 进 CI
#   + 领域知识层补域（按域检索原本 19% 语料不可达）+ 图谱骨架无孤立节点 / 标签不再静默否决用户选择
#   + 图标纪律与文档自证落成 CI 门禁（数字对不上就红）
# 0.6.0：MCP server 补成生产级（Slice 1–3）—— 真实执行接线 / resources+prompts 两原语 /
#   枚举截断自曝 / 版本协商 / Streamable HTTP（JSON+SSE、Mcp-Session-Id）/ progress / cancellation /
#   resources.subscribe / elicitation 人工审批（run_scenario require_approval）
# 0.5.0：症状多跳诊断迭代（A/B/C 三步）—— symptoms.yaml 12 症状 + causal_edges.yaml
#   54 条因果边（indicates 41 / causes 13；real_mechanism 41 / derived 13）+ 多跳遍历
#   + /api/agent/diagnose（无码症状 → 候选链 → 诊断建议，不编造故障码）
# 0.4.0：Q2-P-A 第二增量收口（上游引擎 v1.12.0）—— 203 FMEA / 104 场景 / 多网段 / 四向追溯
#   22 报文/116 信号/203 FMEA(13 系统域)/104 场景/52 SR/11 功能/13 系统分类树
# 0.2.0：Q1 版本号恢复演进（29 commits 未 bump）+ 总览/health 拆分平台与引擎双段版本
__version__ = "0.7.0"
