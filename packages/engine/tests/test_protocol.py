"""DBC 协议定义静态验证：报文结构、周期属性、信号值域、枚举表。"""

from tcms.protocol import (
    ALARM_EVENT,
    BRAKE_SYSTEM,
    DOOR_CONTROL,
    ENERGY_STATUS,
    PANTOGRAPH_STATUS,
    TCMS_HEARTBEAT,
    TRACTION_BRAKE_HANDLE,
    VEHICLE_SPEED,
)

EXPECTED_IDS = {
    TCMS_HEARTBEAT,
    VEHICLE_SPEED,
    TRACTION_BRAKE_HANDLE,
    DOOR_CONTROL,
    ALARM_EVENT,
    PANTOGRAPH_STATUS,
    BRAKE_SYSTEM,
    ENERGY_STATUS,
}


def test_db_loads_core_protocol_messages(db):
    """DBC 必须包含 8 个核心列车控制报文（扩展库在其之上）。"""
    ids = {m.frame_id for m in db.messages}
    assert EXPECTED_IDS <= ids, f"缺少核心报文: {EXPECTED_IDS - ids}"
    assert len(db.messages) > len(EXPECTED_IDS), (
        f"扩展协议库未生效（仅 {len(db.messages)} 帧）——见 tcms.dbc 13 系统域扩展"
    )


def test_all_expected_ids_present(db):
    """核心协议常量必须全部真实存在于 DBC（派生校验，非手抄计数）。"""
    assert EXPECTED_IDS <= {m.frame_id for m in db.messages}


def test_message_ids_unique(db):
    """报文 ID 不允许重复。"""
    ids = [m.frame_id for m in db.messages]
    assert len(ids) == len(set(ids))


def test_all_frames_are_standard(db):
    """所有报文使用 11 位标准帧 ID（<=0x7FF）。"""
    for m in db.messages:
        assert m.frame_id <= 0x7FF, f"{m.name} id 超出标准帧范围"


def test_all_dlc_is_8(db):
    """所有报文数据长度统一为 8 字节。"""
    for m in db.messages:
        assert m.length == 8, f"{m.name} DLC 应为 8"


def test_cycle_times_match_spec(db):
    """周期报文周期符合设计规范：手柄 50ms，心跳/车速/车门/制动 100ms，受电弓/能源 500ms。"""
    spec = {
        TCMS_HEARTBEAT: 100,
        VEHICLE_SPEED: 100,
        TRACTION_BRAKE_HANDLE: 50,
        DOOR_CONTROL: 100,
        PANTOGRAPH_STATUS: 500,
        BRAKE_SYSTEM: 100,
        ENERGY_STATUS: 500,
    }
    for mid, cycle in spec.items():
        m = db.get_message_by_frame_id(mid)
        assert m.cycle_time == cycle, f"{m.name} 周期应为 {cycle}ms"


def test_alarm_event_is_event_triggered(db):
    """报警报文为事件触发型：无固定周期，发送类型为 event。"""
    m = db.get_message_by_frame_id(ALARM_EVENT)
    assert m.cycle_time is None
    assert m.send_type == "event"


def test_speed_signal_physical_range(db):
    """车速信号：scale 0.1，物理范围 0-200 km/h。"""
    m = db.get_message_by_frame_id(VEHICLE_SPEED)
    sig = m.get_signal_by_name("SpeedKmh")
    assert sig.scale == 0.1
    assert sig.minimum == 0
    assert sig.maximum == 200


def test_speed_signal_unit(db):
    """车速信号单位应为 km/h。"""
    m = db.get_message_by_frame_id(VEHICLE_SPEED)
    assert m.get_signal_by_name("SpeedKmh").unit == "km/h"


def test_handle_position_max_16(db):
    """手柄级位信号上限为 16 级。"""
    m = db.get_message_by_frame_id(TRACTION_BRAKE_HANDLE)
    sig = m.get_signal_by_name("HandlePosition")
    assert sig.maximum == 16


def test_energy_signals_ranges(db):
    """能源报文信号值域：SOC 0-100%，电压 0-1000V，温度 -40-120℃。"""
    m = db.get_message_by_frame_id(ENERGY_STATUS)
    assert m.get_signal_by_name("SocPercent").maximum == 100
    assert m.get_signal_by_name("BatteryVoltage").maximum == 1000
    sig = m.get_signal_by_name("BatteryTemp")
    assert sig.minimum == -40
    assert sig.maximum == 120


def test_enumerations_defined(db):
    """关键状态信号应定义枚举值表（VAL_）。"""
    m = db.get_message_by_frame_id(TCMS_HEARTBEAT)
    assert m.get_signal_by_name("NodeStatus").choices  # PowerOff/Standby/Active/Fault
    m = db.get_message_by_frame_id(DOOR_CONTROL)
    assert m.get_signal_by_name("Door1State").choices  # Closed/Open/Fault/Unknown


def test_node_list_defined(db):
    """节点表应包含 TCMS/VCU/BMS/BOGIE/BCU。"""
    nodes = {n.name for n in db.nodes}
    assert {"TCMS", "VCU", "BMS", "BOGIE", "BCU"} <= nodes
