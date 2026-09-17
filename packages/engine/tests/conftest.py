"""pytest 共享夹具：虚拟 CAN 总线（经工厂创建）、DBC、TCMS 仿真器。

另提供"失败现场自动导出"（领域审计 P1-8 落地）：
    - `crash_site` fixture：测试内注册 EventRecorder / FaultLedger，
      用例失败时由 pytest_runtest_makereport 自动导出时间线 JSON/CSV
      与故障台账到 reports/failures/<测试节点>/，供事后定位。
    - 未注册任何现场时仍写失败摘要（异常 + 阶段信息）。
"""

import json
from pathlib import Path

import pytest

CHANNEL = "tcms-test-bus"

REPORTS_ROOT = Path(__file__).resolve().parent.parent / "reports"
FAILURES_ROOT = REPORTS_ROOT / "failures"


class CrashSite:
    """用例失败现场的收集器：注册待导出的证据对象。

    用法（测试内）：
        def test_x(crash_site):
            rec = EventRecorder()
            crash_site.register("recorder", rec)
            ...  # 若失败，recorder 时间线自动导出
    """

    def __init__(self):
        self._objects: dict[str, object] = {}

    def register(self, name: str, obj) -> None:
        self._objects[name] = obj

    def objects(self) -> dict[str, object]:
        return dict(self._objects)


@pytest.fixture()
def crash_site() -> CrashSite:
    return CrashSite()


def _safe_nodeid(nodeid: str) -> str:
    """nodeid → 安全文件名（去 :: 与 /）。"""
    return nodeid.replace("::", "__").replace("/", "_").replace("\\", "_")


def _export_failure(nodeid: str, site: CrashSite, outcome: str, longrepr: str) -> Path:
    """导出失败现场到 reports/failures/<nodeid>/，返回目录。"""
    out_dir = FAILURES_ROOT / _safe_nodeid(nodeid)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 失败摘要
    (out_dir / "summary.txt").write_text(
        f"test: {nodeid}\noutcome: {outcome}\n\n{longrepr}",
        encoding="utf-8",
    )
    # 注册的证据对象：recorder → json+csv；其他可序列化对象 → json
    from tcms import recorder as rec_mod

    for name, obj in site.objects().items():
        try:
            if isinstance(obj, rec_mod.EventRecorder):
                obj.export_json(str(out_dir / f"{name}.json"))
                obj.export_csv(str(out_dir / f"{name}.csv"))
            elif hasattr(obj, "to_dict"):
                (out_dir / f"{name}.json").write_text(
                    json.dumps(obj.to_dict(), ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            else:
                (out_dir / f"{name}.txt").write_text(repr(obj), encoding="utf-8")
        except Exception as exc:  # 导出失败不影响测试结果
            (out_dir / f"{name}.error.txt").write_text(repr(exc), encoding="utf-8")
    return out_dir


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(item, call):
    """用例失败/报错时自动导出失败现场（若测试注册了 crash_site）。"""
    if call.when != "call":
        return
    if call.excinfo is None:
        return
    site = item.funcargs.get("crash_site")
    if site is None:
        return
    longrepr = str(call.excinfo.getrepr())
    try:
        _export_failure(item.nodeid, site, "failed", longrepr)
    except Exception:
        pass  # 失败现场导出是 best-effort，绝不掩盖原始失败


# ---- 共享总线 / DBC / 仿真器 fixture（全测试套件基础） ----


@pytest.fixture(scope="session")
def db():
    from tcms.protocol import load_database

    return load_database()


@pytest.fixture(scope="session")
def bus():
    from tcms.bus import make_bus

    # CI 环境默认 virtual；插卡环境用 TCMS_BUS_* 环境变量切换真实接口
    b = make_bus(channel=CHANNEL, receive_own_messages=True)
    yield b
    b.shutdown()


@pytest.fixture()
def drain(bus):
    """清空总线上的残留报文，保证用例隔离。"""
    while bus.recv(timeout=0.01) is not None:
        pass
    yield


@pytest.fixture()
def simulator(bus, db, drain):
    """每个用例独立的 TCMS 仿真器实例。"""
    from tcms.simulator import TCMSNodeSimulator

    sim = TCMSNodeSimulator(bus, db)
    sim.start()
    yield sim
    sim.stop()
