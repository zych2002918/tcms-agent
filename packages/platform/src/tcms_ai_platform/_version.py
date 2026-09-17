"""版本单一真源（镜像上游纪律：展示数字必须机器可读）。"""

# 0.5.0：症状多跳诊断迭代（A/B/C 三步）—— symptoms.yaml 12 症状 + causal_edges.yaml
#   54 条因果边（indicates 41 / causes 13；real_mechanism 41 / derived 13）+ 多跳遍历
#   + /api/agent/diagnose（无码症状 → 候选链 → 诊断建议，不编造故障码）
# 0.4.0：Q2-P-A 第二增量收口（上游引擎 v1.12.0）—— 203 FMEA / 104 场景 / 多网段 / 四向追溯
#   22 报文/116 信号/203 FMEA(13 系统域)/104 场景/52 SR/11 功能/13 系统分类树
# 0.2.0：Q1 版本号恢复演进（29 commits 未 bump）+ 总览/health 拆分平台与引擎双段版本
__version__ = "0.5.0"
