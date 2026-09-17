"""把真实资产(asset_loader 结果) → prompt 资产事实上下文（防 LLM 幻觉）。

red-team 高2 修复的延伸：prompt v2 的 build_asset_context 需要结构化输入
（message/signal/enum/min/max/unit），本模块从 AssetBundle 直接构造——
真 LLM 生成时无需手写事实，从解析好的 DBC 自动取枚举文本与物理范围。
"""

from __future__ import annotations

from typing import Optional

from tcms_ai_testgen.asset_models import AssetBundle
from tcms_ai_testgen.oracle import fault_keys
from tcms_ai_testgen.prompt import build_asset_context


def signal_facts_from_bundle(bundle: AssetBundle, max_signals: int = 40) -> list[dict]:
    """从 AssetBundle 提取信号事实（含 VAL_ 枚举文本/物理范围/单位）。

    只挑「有枚举文本 或 与安全相关」的信号，控制 prompt 长度（max_signals）。
    """
    facts: list[dict] = []
    for dbc in bundle.dbc:
        for msg in dbc.messages:
            enum_map = dbc.value_descriptions.get(msg.frame_id, {})
            for sig in msg.signals:
                enums = enum_map.get(sig.name)
                enum_texts: list[str] = []
                if enums:
                    # 按 raw 值排序取文本
                    enum_texts = [e.label for _, e in sorted(enums.items())]
                if not enum_texts and len(facts) >= max_signals * 2:
                    continue  # 数值信号太多时优先枚举信号
                facts.append(
                    {
                        "message": msg.name,
                        "signal": sig.name,
                        "enum_texts": enum_texts,
                        "min": sig.minimum,
                        "max": sig.maximum,
                        "unit": sig.unit,
                    }
                )
                if len(facts) >= max_signals:
                    return facts
    return facts


def build_prompt_context(bundle: Optional[AssetBundle] = None) -> str:
    """构造 prompt 资产事实上下文。

    bundle 缺省时用最小内置事实（10 故障键 + 常用信号），保证无上游也能跑。
    """
    if bundle is not None and bundle.dbc:
        facts = signal_facts_from_bundle(bundle)
    else:
        facts = [
            {"message": "VehicleSpeed", "signal": "SpeedKmh", "enum_texts": [], "min": 0.0, "max": 200.0, "unit": "km/h"},
            {"message": "DoorControl", "signal": "Door2State", "enum_texts": ["Closed", "Open", "Fault", "Unknown"], "min": 0.0, "max": 3.0, "unit": ""},
            {"message": "TractionBrakeHandle", "signal": "HandlePosition", "enum_texts": [], "min": 0.0, "max": 16.0, "unit": ""},
            {"message": "EnergyStatus", "signal": "SocPercent", "enum_texts": [], "min": 0.0, "max": 100.0, "unit": "%"},
            {"message": "BrakeSystem", "signal": "EmergencyBrakeActive", "enum_texts": [], "min": 0.0, "max": 1.0, "unit": ""},
        ]
    return build_asset_context(facts, fault_keys())


__all__ = ["build_prompt_context", "signal_facts_from_bundle"]
