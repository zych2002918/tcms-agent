"""场景 YAML 加载与执行（A3）—— 声明式故障场景外部化。

把"故障场景 DSL"（faultlife.FaultScenario）从 Python 代码提升到
**YAML 配置文件**：场景与代码分离，测试/演示人员无需改代码即可
编排"何时注入什么故障、期望什么处置"。

设计：
    - `load_scenario(path)` / `load_scenarios(dir)`：解析 YAML → FaultScenario
    - `run_scenario(ledger, scenario, clock)`：执行并返回报告
    - `run_yaml(path, clock)`：一键加载 + 执行
    - YAML 语法与 FaultScenario.when()/expect_clear() 一一对应：

      ```yaml
      name: 超速降级
      steps:
        - at: 10.0          # 注入
          inject:
            node: vcu
            fault: overspeed
            level: major
            impact: 速度超限
            expect: derate
        - at: 20.0          # 恢复
          recover: overspeed
      ```
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from . import faultlevel, timebase
from .faultlife import FaultLedger, FaultScenario, ScenarioRunner

# 支持的动作（与 FaultScenario 步骤一致）
_VALID_ACTIONS = ("inject", "recover")


def _step_from_yaml(raw: dict) -> dict:
    """把 YAML 单步映射为 FaultScenario.steps 元素。

    支持两种写法：
        1. 显式动作：{at, inject: {...}} / {at, recover: fault}
        2. 事件式：{at, action: inject, fault, ...}（与 DSL 对齐）
    """
    if "at" not in raw:
        raise ValueError(f"场景步骤缺少 at 时间戳: {raw!r}")
    ts = float(raw["at"])
    if "inject" in raw:
        inj = raw["inject"]
        if not isinstance(inj, dict) or "fault" not in inj:
            raise ValueError(f"inject 步骤需 fault 字段: {raw!r}")
        return {
            "ts": ts,
            "action": "inject",
            "node": inj.get("node", "unknown"),
            "fault": inj["fault"],
            "level": inj.get("level", faultlevel.LEVEL_MAJOR),
            "impact": inj.get("impact"),
            "expect": inj.get("expect"),
        }
    if "recover" in raw:
        return {"ts": ts, "action": "recover", "fault": raw["recover"]}
    action = raw.get("action")
    if action in _VALID_ACTIONS and "fault" in raw:
        return {
            "ts": ts,
            "action": action,
            "fault": raw["fault"],
            "node": raw.get("node", "unknown"),
            "level": raw.get("level", faultlevel.LEVEL_MAJOR),
            "impact": raw.get("impact"),
            "expect": raw.get("expect"),
        }
    raise ValueError(f"无法识别的场景步骤: {raw!r}")


def parse_scenario(text: str, name: str | None = None) -> FaultScenario:
    """从 YAML 文本构造 FaultScenario。"""
    data = yaml.safe_load(text)
    if data is None:
        raise ValueError("YAML 为空")
    if not isinstance(data, dict) or "steps" not in data:
        raise ValueError("YAML 顶层需包含 steps 列表")
    scenario = FaultScenario(name or data.get("name", "scenario"))
    for raw in data["steps"]:
        step = _step_from_yaml(raw)
        if step["action"] == "inject":
            scenario.when(
                step["node"],
                step["fault"],
                at=step["ts"],
                level=step["level"],
                impact=step["impact"],
                expect=step["expect"],
            )
        else:
            scenario.expect_clear(step["fault"], at=step["ts"])
    return scenario


def load_scenario(path: str | os.PathLike) -> FaultScenario:
    """从 YAML 文件加载场景。"""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return parse_scenario(f.read(), name=p.stem)


def load_scenarios(dir_path: str | os.PathLike) -> list[FaultScenario]:
    """加载目录下全部 *.yaml 场景（按文件名排序）。"""
    d = Path(dir_path)
    if not d.is_dir():
        raise FileNotFoundError(f"场景目录不存在: {d}")
    out = []
    for f in sorted(d.glob("*.yaml")):
        out.append(load_scenario(f))
    return out


def run_yaml(path: str | os.PathLike, ledger: FaultLedger | None = None, clock=None) -> dict:
    """一键：加载 YAML 场景 → 执行 → 返回报告。

    ledger 缺省时自动创建（不含事件记录器，纯场景验证）；
    clock 缺省时自动创建 virtual 模式虚拟时钟（场景内时间由 YAML 的 at 驱动）。
    """
    scenario = load_scenario(path)
    if clock is None:
        clock = timebase.VirtualClock(mode="virtual")
    if ledger is None:
        ledger = FaultLedger(clock)
    runner = ScenarioRunner(ledger, scenario, clock)
    return runner.run()


def run_scenarios(dir_path: str | os.PathLike, clock=None) -> list[dict]:
    """批量执行目录下全部 YAML 场景，返回报告列表。"""
    reports = []
    if clock is None:
        clock = timebase.VirtualClock(mode="virtual")
    for sc in load_scenarios(dir_path):
        # 每个场景独立台账，互不污染
        ledger = FaultLedger(clock)
        reports.append(ScenarioRunner(ledger, sc, clock).run())
    return reports
