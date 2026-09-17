"""多层记忆（M5 落地）。

规划的四层（对应认知架构里的经典分层，不做无意义的堆砌）：

    工作记忆  working   当前 trajectory（LangGraph state + 上下文预算裁剪）      M2
    情景记忆  episodic  每次运行的完整轨迹（SQLite checkpoint + 可检索摘要）      M5
    语义记忆  semantic  真实资产 / 图谱 / 检索（复用 platform 知识底座）          M1 ✅
    程序性记忆 procedural 沉淀出的技能（测试模板 / 修复模式 / 排障策略）           M5

巩固（consolidation）：跑完一批任务后离线提炼失败模式与成功策略，写回程序性记忆。
**写入门禁**：任何进入长期记忆的内容都必须能机器校验（引用的资产键必须真实存在、
技能必须能复现一次成功运行），防止幻觉污染长期记忆。
"""

__all__: list[str] = []
