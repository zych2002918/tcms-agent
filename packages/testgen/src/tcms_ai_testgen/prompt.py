"""Prompt 构造：把生成请求转成给 LLM 的结构化指令（v2：execution DSL 契约）。

v2 关键变化（docs/decisions.md D1 + red-team 终审高2）：
    - 旧 prompt 只要求 purpose/steps/expected 自由文本 → 真 LLM 永不产出
      execution → compile≈0、无法走真实执行管线（与 mock 不可比）。
    - 新 prompt 显式要求每条用例输出机器可读 `execution`（白名单原语），
      否则该用例判「生成失败」（parse_rate 诚实计入）；
    - 注入资产事实（可选）：调用方可传入 asset_loader 摘要（信号枚举文本 /
      物理范围 / 故障键），让模型基于事实生成而非幻觉；
    - few-shot 正反例：编码输入用 raw 数值、解码断言用 VAL_ 枚举文本
      （P2 实证的 raw/decoded 角色混淆）。

白名单原语（与 execution.py 一致）：
    setup:  set_speed / set_handle / set_door_state / stop_message / send_alarm
    expect: expect_signal(msg, sig, equals) / expect_no_frames(msg)
            / expect_encode_error(msg, sig, value) / expect_encode_ok(msg, sig, value)
            / expect_action(fault, action)
    kind:   encode_bound | simulate_inject | fault_scenario
"""

from __future__ import annotations

from tcms_ai_testgen.models import GenRequest

_SYSTEM_PROMPT = """你是一位资深的软件测试工程师，擅长为安全关键系统（列车 TCMS CAN）设计可执行的测试用例。
根据给定的被测对象与需求，输出 JSON，不要输出任何解释。

输出 JSON 结构（每条用例**必须**带 execution 字段；缺失或原语非法则该条判失败）：
{
  "cases": [
    {
      "name": "test_<场景语义名>",         // pytest 风格，小写下划线，全批唯一
      "purpose": "用例目的，对应哪条需求",
      "expected": "明确可判定的预期结果（自然语言，给人类看）",
      "covers": ["需求编号"],
      "execution": {
        "kind": "encode_bound | simulate_inject | fault_scenario",
        "setup": [{"op": "<setup 原语>", "args": {...}}],
        "expect": [{"op": "<expect 原语>", "args": {...}}]
      }
    }
  ]
}

白名单原语（只准用这些，禁止自由代码）：
  setup 原语:
    set_speed        args: {"kmh": 数值}
    set_handle       args: {"position": 0-16, "direction": 1前/2后}
    set_door_state   args: {"index": 0-3, "state": 0关/1开/2故障/3未知}
    stop_message     args: {"message": "TCMS_Heartbeat|VehicleSpeed|DoorControl|..."}
    send_alarm       args: {"code": 数值, "level": 0-3, "Overspeed": true, ...}
  expect 原语:
    expect_signal          args: {"message": 报文名, "signal": 信号名, "equals": 值}
    expect_no_frames       args: {"message": 报文名}
    expect_encode_error    args: {"message": 报文名, "signal": 信号名, "value": 越界物理值}
    expect_encode_ok       args: {"message": 报文名, "signal": 信号名, "value": 合法物理值}
    expect_action          args: {"fault": 故障键, "action": "none|warning|derate|emergency_brake"}

方向规则（重要，防 raw/decoded 混淆）：
  - encode_bound 的 value 一律是**物理值**（如车速 200.1 越界 / 200.0 合法）；
  - simulate_inject 的 set_door_state state 是 **raw 0-3**；
  - send_alarm 的 level 必须是 **0-3**（AlarmLevel 枚举 Info/Warning/Severe/Emergency），
    禁止负数或大于 3；
  - expect_signal 的 equals：该信号若有 VAL_ 枚举文本（如 Door2State），
    必须用**文本**（"Closed"/"Open"/"Fault"/"Unknown"），禁止用数字 0-3；
    无量纲/连续信号用数值（如 SpeedKmh==165.0、AllDoorsClosed==0/1）。
  - 只输出 ```json fence 包裹的 JSON。"""

_EXECUTION_EXAMPLES = """
参考示例：
1) 编码边界（kind=encode_bound）：
   {"name": "test_speed_over_max_rejected", "purpose": "车速越界应被编码拒绝",
    "expected": "编码抛 EncodeError", "covers": ["1"],
    "execution": {"kind": "encode_bound", "setup": [],
      "expect": [{"op": "expect_encode_error", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.1}}]}}
2) 车门故障注入（kind=simulate_inject，枚举文本断言）：
   {"name": "test_door_fault_detected", "purpose": "车门故障应被检出",
    "expected": "Door2State 为 Fault 且禁止发车", "covers": ["2"],
    "execution": {"kind": "simulate_inject",
      "setup": [{"op": "set_door_state", "args": {"index": 1, "state": 2}}],
      "expect": [
        {"op": "expect_signal", "args": {"message": "DoorControl", "signal": "Door2State", "equals": "Fault"}},
        {"op": "expect_signal", "args": {"message": "DoorControl", "signal": "AllDoorsClosed", "equals": 0}}]}}
3) 超速故障处置（kind=fault_scenario，期望由 oracle 处置表）：
   {"name": "test_overspeed_derate", "purpose": "超速应降级",
    "expected": "超速（major）处置为 derate", "covers": ["3"],
    "execution": {"kind": "fault_scenario", "node": "vcu", "fault": "overspeed",
      "expect": [{"op": "expect_action", "args": {"fault": "overspeed", "action": "derate"}}]}}
"""


def build_system_prompt(asset_context: str | None = None) -> str:
    """系统提示词。asset_context 可传资产事实摘要（信号枚举/范围/故障键）。"""
    base = _SYSTEM_PROMPT
    if asset_context:
        base += "\n\n已知资产事实（以此为准，禁止编造枚举或范围）：\n" + asset_context
    return base + _EXECUTION_EXAMPLES


def build_user_prompt(req: GenRequest) -> str:
    lines: list[str] = []
    lines.append(f"被测对象：{req.target}")
    if req.requirements:
        lines.append("需求清单：")
        lines.extend(f"- [{i}] {r}" for i, r in enumerate(req.requirements))
    if req.hints:
        lines.append("重点场景提示：")
        lines.extend(f"- {h}" for h in req.hints)
    lines.append(
        f"请生成 {req.num_cases} 条用例（tier 统一为 {req.tier}）。要求：每条都带"
        "合法 execution（白名单原语），execution 不可执行的用例会被判失败。"
    )
    return "\n".join(lines)


def build_asset_context(dbc_signals: list[dict], fault_keys: list[str]) -> str:
    """从 asset_loader / oracle 构造资产事实摘要（注入 prompt 防幻觉）。

    dbc_signals: [{message, signal, enum_texts: [..], min, max, unit}]
    fault_keys:  oracle 故障键（生成 fault_scenario 候选）
    """
    lines: list[str] = ["信号枚举与范围（expect_signal equals 用文本/数值，见方向规则）："]
    for s in dbc_signals:
        if s.get("enum_texts"):
            lines.append(
                f"- {s['message']}.{s['signal']}: 枚举文本 {s['enum_texts']} "
                f"（物理范围 {s.get('min')}..{s.get('max')} {s.get('unit', '')}）"
            )
        else:
            lines.append(
                f"- {s['message']}.{s['signal']}: 数值信号，物理范围 "
                f"{s.get('min')}..{s.get('max')} {s.get('unit', '')}"
            )
    lines.append("故障键（fault_scenario 的 fault 只准用这些）：" + ", ".join(fault_keys))
    return "\n".join(lines)
