"""故障生命周期台账（Fault Lifecycle Ledger）+ 故障场景 DSL。

对标真实列控系统的 **Fault Recorder / 故障管理系统**：故障不是瞬态事件，
而是完整生命周期——**注入 → 传播影响 → 告警 → 恢复 → 归档**。每个阶段
都必须留痕（时间戳 + 状态），供事后审计与安全论证（EN 50128 证据链）。

本模块两部分：

1. **FaultLedger（台账）**：多故障统一管理。
    - open(name, level)     开账（注入时刻）
    - propagate(name, impact) 传播影响（影响范围/危害描述）
    - alert(name, message)  告警（司机/维护提示）
    - recover(name)         恢复（故障消除）
    - close(name)           归档（生命周期终结）
    - query / report        审计查询与汇总
    - 与 recorder 打通：每个阶段自动写事件记录器（证据链）

2. **FaultScenario DSL（故障场景声明式）**：
    - FaultScenario 把场景描述（when/expect 时间线）解析为可执行步骤
    - ScenarioRunner 按虚拟时钟顺序执行：注入 → 传播 → 告警 → 恢复
    - expect 断言在指定时刻生效，报告 PASS/FAIL——"CAN 混沌工程"最小实现

用法（台账）：
    ledger = FaultLedger(clock=virtual_clock)
    ledger.open("overspeed", level="major")
    ledger.propagate("overspeed", impact="traction derate")
    ledger.recover("overspeed")
    ledger.close("overspeed")

用法（场景 DSL）：
    scenario = FaultScenario(clock=virtual_clock)
    scenario.when("vcu", "overspeed", at=10.0, expect="emergency_brake")
    report = ScenarioRunner(ledger, scenario, clock).run()
"""

from __future__ import annotations

# 统一故障字典（FMEA）——场景引擎处置动作的兜底数据源。
# 注意：faultdb 依赖 faultlevel（双源一致性校验），本模块依赖两者无环。
from . import faultdb as _faultdb
from . import faultlevel, recorder, timebase

# ---- 生命周期阶段 ----
STAGE_INJECTED = "injected"  # 注入
STAGE_PROPAGATED = "propagated"  # 传播影响
STAGE_ALERTED = "alerted"  # 告警
STAGE_RECOVERED = "recovered"  # 恢复
STAGE_CLOSED = "closed"  # 归档

VALID_STAGES = (STAGE_INJECTED, STAGE_PROPAGATED, STAGE_ALERTED, STAGE_RECOVERED, STAGE_CLOSED)


class FaultLifecycle:
    """单个故障的生命周期台账（五阶段，每阶段带时间戳）。"""

    def __init__(
        self, name: str, level: str = faultlevel.LEVEL_INFO, clock=None, source: str | None = None
    ):
        self.name = name
        self.level = level
        self._clock = clock or timebase.global_clock()
        self.source = source
        self._stages: list[dict] = []
        self._impact: list[str] = []
        self._closed = False

    @property
    def stages(self) -> list[dict]:
        """阶段时间线（深拷贝）。"""
        return [dict(s) for s in self._stages]

    @property
    def current_stage(self) -> str | None:
        """当前阶段（按时间线最后一个阶段）。"""
        return self._stages[-1]["stage"] if self._stages else None

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _mark(self, stage: str, detail: str | None = None) -> dict:
        if stage not in VALID_STAGES:
            raise ValueError(f"未知阶段: {stage}")
        if stage == STAGE_CLOSED and self._closed:
            raise ValueError(f"故障 {self.name} 已归档，不能再次关闭")
        entry = {"stage": stage, "ts": self._clock.now(), "detail": detail}
        self._stages.append(entry)
        if stage == STAGE_CLOSED:
            self._closed = True
        return entry

    def inject(self, detail: str | None = None) -> dict:
        return self._mark(STAGE_INJECTED, detail)

    def propagate(self, impact: str, detail: str | None = None) -> dict:
        self._impact.append(impact)
        return self._mark(STAGE_PROPAGATED, detail or impact)

    def alert(self, message: str, detail: str | None = None) -> dict:
        return self._mark(STAGE_ALERTED, detail or message)

    def recover(self, detail: str | None = None) -> dict:
        return self._mark(STAGE_RECOVERED, detail)

    def close(self, detail: str | None = None) -> dict:
        return self._mark(STAGE_CLOSED, detail)

    def to_dict(self) -> dict:
        """完整台账（JSON 可序列化）。"""
        return {
            "name": self.name,
            "level": self.level,
            "source": self.source,
            "stages": self._stages,
            "impact": list(self._impact),
            "current_stage": self.current_stage,
            "closed": self._closed,
        }


class FaultLedger:
    """多故障台账：统一管理故障生命周期 + 事件记录器联动。"""

    def __init__(self, clock=None, event_recorder: recorder.EventRecorder | None = None):
        self._clock = clock or timebase.global_clock()
        self._rec = event_recorder
        self._faults: dict[str, FaultLifecycle] = {}

    @property
    def faults(self) -> dict[str, FaultLifecycle]:
        return dict(self._faults)

    @property
    def open_faults(self) -> list[str]:
        """未归档的故障名。"""
        return [n for n, f in self._faults.items() if not f.is_closed]

    def open(
        self,
        name: str,
        level: str = faultlevel.LEVEL_INFO,
        source: str | None = None,
        detail: str | None = None,
    ) -> FaultLifecycle:
        """开账：注入故障（幂等——同名未归档返回已有台账）。"""
        if name in self._faults and not self._faults[name].is_closed:
            return self._faults[name]
        fl = FaultLifecycle(name, level=level, clock=self._clock, source=source)
        fl.inject(detail)
        self._faults[name] = fl
        self._record("open", fl)
        return fl

    def propagate(self, name: str, impact: str, detail: str | None = None) -> FaultLifecycle:
        fl = self._require_open(name)
        fl.propagate(impact, detail)
        self._record("propagate", fl, impact=impact)
        return fl

    def alert(self, name: str, message: str, detail: str | None = None) -> FaultLifecycle:
        fl = self._require_open(name)
        fl.alert(message, detail)
        self._record("alert", fl, message=message)
        return fl

    def recover(self, name: str, detail: str | None = None) -> FaultLifecycle:
        fl = self._require_open(name)
        fl.recover(detail)
        self._record("recover", fl)
        return fl

    def close(self, name: str, detail: str | None = None) -> FaultLifecycle:
        fl = self._require_open(name)
        fl.close(detail)
        self._record("close", fl)
        return fl

    def _require_open(self, name: str) -> FaultLifecycle:
        if name not in self._faults:
            raise KeyError(f"故障 {name} 未开账")
        fl = self._faults[name]
        if fl.is_closed:
            raise ValueError(f"故障 {name} 已归档，不能继续操作")
        return fl

    def _record(self, action: str, fl: FaultLifecycle, **extra) -> None:
        """把台账动作写入事件记录器（证据链）。"""
        if self._rec is None:
            return
        self._rec.record_event(
            recorder.EVENT_EBM,  # 复用安全事件通道（故障台账属安全证据）
            category="fault_lifecycle",
            message=action,
            payload={"fault": fl.name, "level": fl.level, "ts": self._clock.now(), **extra},
        )

    def query(
        self, name: str | None = None, stage: str | None = None, open_only: bool = False
    ) -> list[dict]:
        """审计查询：按故障名/阶段过滤。"""
        out = []
        for n, fl in self._faults.items():
            if name is not None and n != name:
                continue
            if open_only and fl.is_closed:
                continue
            if stage is not None and stage not in [s["stage"] for s in fl.stages]:
                continue
            out.append(fl.to_dict())
        return out

    def report(self) -> dict:
        """汇总报告：开/关账、当前开放故障、按等级分布。"""
        open_list = [self._faults[n].to_dict() for n in self.open_faults]
        by_level = {}
        for fl in self._faults.values():
            by_level[fl.level] = by_level.get(fl.level, 0) + 1
        return {
            "total": len(self._faults),
            "open": len(open_list),
            "closed": len(self._faults) - len(open_list),
            "open_faults": open_list,
            "by_level": by_level,
        }


# ---- 故障场景 DSL ----


class FaultScenario:
    """声明式故障场景：when(...) 时间线 + expect(...) 断言。

    用法：
        scenario = FaultScenario()
        scenario.when("vcu", "overspeed", at=10.0,
                      expect="emergency_brake")
        scenario.expect_clear("overspeed", at=20.0)
    """

    def __init__(self, name: str | None = None):
        self.name = name
        self._steps: list[dict] = []

    def when(
        self,
        node: str,
        fault: str,
        at: float,
        level: str = faultlevel.LEVEL_MAJOR,
        impact: str | None = None,
        expect: str | None = None,
    ) -> None:
        """在 at 时刻注入故障（可选期望处置动作）。"""
        self._steps.append(
            {
                "ts": at,
                "action": "inject",
                "node": node,
                "fault": fault,
                "level": level,
                "impact": impact,
                "expect": expect,
            }
        )

    def expect_clear(self, fault: str, at: float) -> None:
        """在 at 时刻清除故障（恢复）。"""
        self._steps.append(
            {
                "ts": at,
                "action": "recover",
                "fault": fault,
            }
        )

    @property
    def steps(self) -> list[dict]:
        return [dict(s) for s in self._steps]

    @property
    def duration(self) -> float:
        """场景总时长（最后一步时间戳）。"""
        return max((s["ts"] for s in self._steps), default=0.0)


class ScenarioRunner:
    """按虚拟时钟执行故障场景，输出 PASS/FAIL 报告。

    执行规则：
        - 步骤按时间戳排序，逐一推进虚拟时钟到该时刻再执行
        - inject：台账开账 + 传播影响 + 告警（按等级映射处置动作）
        - recover：台账恢复（并归档）
        - expect 断言：注入时记录期望处置，恢复后校验实际处置
    """

    def __init__(self, ledger: FaultLedger, scenario: FaultScenario, clock=None):
        self.ledger = ledger
        self.scenario = scenario
        self._clock = clock or timebase.global_clock()
        self._expects: list[dict] = []  # 期望断言结果
        self._actions: dict[str, str] = {}

    def run(self) -> dict:
        """执行全部步骤，返回报告。"""
        steps = sorted(self.scenario.steps, key=lambda s: s["ts"])
        for step in steps:
            self._clock.set(step["ts"])
            if step["action"] == "inject":
                self._inject(step)
            elif step["action"] == "recover":
                self._recover(step)
        return self.report()

    def _inject(self, step: dict) -> None:
        fault = step["fault"]
        self.ledger.open(fault, level=step["level"], source=step.get("node"))
        if step.get("impact"):
            self.ledger.propagate(fault, step["impact"])
        expected = step.get("expect")
        try:
            actual = self._expected_action(fault)
        except ValueError as exc:
            # 未知故障：不中断整个场景（与 _recover 兜底对称），记录失败断言
            self._expects.append(
                {
                    "fault": fault,
                    "ts": step["ts"],
                    "expected": expected or "known_fault",
                    "actual": f"unknown: {exc}",
                    "passed": False,
                }
            )
            return
        self._actions[fault] = actual
        if expected:
            self._expects.append(
                {
                    "fault": fault,
                    "ts": step["ts"],
                    "expected": expected,
                    "actual": actual,
                    "passed": actual == expected,
                }
            )

    def _expected_action(self, fault: str) -> str:
        """故障 → 期望处置动作。

        优先走 faultlevel（经典 10 故障，模式敏感）；
        字典扩展故障（FMEA 全 22 条）回退到字典的默认 action——
        保证统一故障字典中每条目都可在场景中执行（FMEA 闭环）。
        """
        try:
            return faultlevel.action_for(fault, mode="auto")
        except ValueError:
            try:
                return _faultdb.load_fault_dictionary().by_key(fault)["action"]
            except KeyError:
                raise ValueError(
                    f"故障 {fault!r} 既不在 faultlevel.FAULTS，也不在故障字典 "
                    f"(tcms/faults.yaml)——无法判定处置动作"
                ) from None

    def _recover(self, step: dict) -> None:
        fault = step["fault"]
        try:
            self.ledger.recover(fault)
            self.ledger.close(fault)
        except (KeyError, ValueError):
            self._expects.append(
                {
                    "fault": fault,
                    "ts": step["ts"],
                    "expected": "recover",
                    "actual": "not_open",
                    "passed": False,
                }
            )

    def report(self) -> dict:
        """场景执行报告。"""
        asserts = list(self._expects)
        passed = sum(1 for a in asserts if a["passed"])
        return {
            "scenario": self.scenario.name,
            "steps": len(self.scenario.steps),
            "assertions": asserts,
            "passed": passed,
            "failed": len(asserts) - passed,
            "all_passed": bool(asserts) and passed == len(asserts),
            "ledger": self.ledger.report(),
        }
