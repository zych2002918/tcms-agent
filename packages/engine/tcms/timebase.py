"""虚拟时间基（Virtual Time Base）—— TCMS 仿真统一时间源。

对标真实 HIL（Hardware-in-the-Loop）测试台架的核心基础设施：
**时间一致性**——被测系统所有模块共享同一个时间源，时钟漂移/抖动
可控可复现。本模块把散落在各业务模块的 `time.monotonic()` 调用统一
到单一时间基：

    - VirtualClock：可注入/可推进的虚拟时钟。默认与真实单调时钟同步
      （`mode="monotonic"`），切换到 `mode="virtual"` 后由测试显式
      `advance()`/`set()` 控制——**确定性回放与毫秒级加速仿真**。
    - global_clock()：获取全局时间基（单例）。
    - monotonic()：与 time.monotonic 签名一致的全局函数——业务代码
      无痛替换 `time.monotonic()` 为 `tcms.timebase.monotonic()`，
      测试里 `virtual_clock.advance(0.1)` 即可推进全链路时间。

用法（业务代码替换）：
    import timebase
    ts = timebase.monotonic()          # 虚拟或真实，取决于全局模式

用法（测试确定性）：
    clock = VirtualClock(mode="virtual")
    clock.advance(0.1)                 # 推进 100ms
    clock.set(5.0)                     # 跳变到 5s
    assert clock.now() == 5.0
"""

from __future__ import annotations

import time

MODE_MONOTONIC = "monotonic"  # 真实单调时钟（默认，生产行为不变）
MODE_VIRTUAL = "virtual"  # 虚拟时钟（测试/回放确定性）


class VirtualClock:
    """统一时间源：monotonic（真实）或 virtual（可推进）。"""

    def __init__(self, mode: str = MODE_MONOTONIC, start: float = 0.0, base: float | None = None):
        if mode not in (MODE_MONOTONIC, MODE_VIRTUAL):
            raise ValueError(f"未知时间模式: {mode}")
        self.mode = mode
        self._start = start
        # base：虚拟时间 0 对应的真实单调时刻（仅 monotonic 模式使用）
        self._base = base if base is not None else time.monotonic()
        self._virtual_now = start

    # ---- 读取 ----

    def now(self) -> float:
        """当前时间（秒）。"""
        if self.mode == MODE_MONOTONIC:
            return self._start + (time.monotonic() - self._base)
        return self._virtual_now

    # monotonic 兼容别名
    def monotonic(self) -> float:
        return self.now()

    # ---- 虚拟模式控制 ----

    def set_mode(self, mode: str, start: float | None = None) -> None:
        """切换时间模式。切到 virtual 时可指定起始时间。"""
        if mode not in (MODE_MONOTONIC, MODE_VIRTUAL):
            raise ValueError(f"未知时间模式: {mode}")
        if mode == MODE_VIRTUAL:
            if start is not None:
                self._virtual_now = start
            self.mode = mode
        else:
            self._base = time.monotonic()
            self._start = 0.0
            self.mode = mode

    def advance(self, dt: float) -> None:
        """虚拟模式：推进时间（dt 必须 ≥ 0）。"""
        if self.mode != MODE_VIRTUAL:
            raise RuntimeError("advance() 仅 virtual 模式可用（先 set_mode('virtual')）")
        if dt < 0:
            raise ValueError(f"advance 增量必须 ≥ 0，got {dt}")
        self._virtual_now += dt

    def set(self, t: float) -> None:
        """虚拟模式：跳变到指定时间。"""
        if self.mode != MODE_VIRTUAL:
            raise RuntimeError("set() 仅 virtual 模式可用（先 set_mode('virtual')）")
        if t < 0:
            raise ValueError(f"时间不能为负，got {t}")
        self._virtual_now = t

    def step(self) -> None:
        """虚拟模式：单步推进（配合循环测试）。"""
        self.advance(1.0)

    # ---- 便捷 ----

    def reset(self, start: float = 0.0) -> None:
        """虚拟模式：重置到起始时间。"""
        self._virtual_now = start


# ---- 全局时间基（单例） ----

_global: VirtualClock | None = None


def global_clock() -> VirtualClock:
    """获取全局时间基（惰性创建，monotonic 模式）。"""
    global _global
    if _global is None:
        _global = VirtualClock()
    return _global


def monotonic() -> float:
    """全局时间基的单调时钟接口（业务代码无痛替换 time.monotonic）。"""
    return global_clock().now()


def install(clock: VirtualClock) -> VirtualClock:
    """安装自定义全局时间基（测试替换），返回该时钟。"""
    global _global
    _global = clock
    return clock


def reset_global() -> None:
    """重置全局时间基为默认 monotonic（测试清理）。"""
    global _global
    _global = None
