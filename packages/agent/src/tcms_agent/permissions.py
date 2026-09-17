"""工具权限分级（本项目的核心安全设计）。

TCMS 是安全关键域：**不能让 LLM 直接触发写操作或真实执行**。因此工具面按副作用
强度分四级，注册表只向模型暴露调用方声明允许的级别；级别越高，管控越严。

    R0 READ      只读查询（知识底座 / 资产枚举 / 图谱）—— 自由调用
    R1 SANDBOX   沙箱写（生成测试意图、编译检查）—— 写临时目录，可丢弃
    R2 EXECUTE   真实执行（跑场景 / 跑 pytest）—— 只读环境 + 超时 + 产物归档
    R3 PERSIST   持久化（写记忆、提交用例集）—— **需人工审批**（human-in-the-loop）

M1 只落地 R0。R1/R2/R3 在后续里程碑逐步开放，且每次开放都必须同时落地
「审批 / 沙箱 / 回滚」中的对应机制——只加权限不加护栏是禁止的。
"""

from __future__ import annotations

from enum import IntEnum


class Permission(IntEnum):
    """工具权限级别（数值越大副作用越强）。"""

    READ = 0
    SANDBOX = 1
    EXECUTE = 2
    PERSIST = 3

    @property
    def label(self) -> str:
        return {
            Permission.READ: "R0 只读",
            Permission.SANDBOX: "R1 沙箱写",
            Permission.EXECUTE: "R2 真实执行",
            Permission.PERSIST: "R3 持久化",
        }[self]

    @property
    def requires_approval(self) -> bool:
        """是否需要人在环路审批。"""
        return self >= Permission.PERSIST
