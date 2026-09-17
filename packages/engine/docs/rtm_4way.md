# RTM 四向追溯（EN 50128 思想）—— 需求 ↔ 场景/实现 ↔ 测试 ↔ 故障

> 机器生成：`scripts/gen_trace_4way.py`（改动 rtm.csv/scenarios/faults 后重跑，防手抄漂移）。
> 快照：RTM **57 行 / 52 SR** · 场景文件 **104** · 场景模块 SR **34** · 被场景追溯的故障键 **40**（去重）。

## 1. SR → 场景/模块 → 测试 → 故障

| SR | 需求（verifies） | 实现模块/场景 | 验证测试 | 覆盖故障 |
|---|---|---|---|---|
| SR-01 | 模式×原因矩阵：SIL4 任一原因触发即制动 | `tcms/ebm.py` | `tests/test_ebm.py` | —（模块行为测试） |
| SR-02 | 双通道表决：SIL2 一致才制动 | `tcms/ebm.py` | `tests/test_ebm.py` | —（模块行为测试） |
| SR-03 | 缓解闭环：零速+原因消失+有效速度 | `tcms/ebm.py` | `tests/test_ebm.py` | —（模块行为测试） |
| SR-04 | 门-车联锁：移动中开门/门故障即违规 | `tcms/interlocks.py` | `tests/test_interlocks.py` | —（模块行为测试） |
| SR-05 | EBI 阈值触发紧急制动干预 | `tcms/atp.py` | `tests/test_atp.py` | —（模块行为测试） |
| SR-06 | SBI 先于 EBI 的常用制动干预 | `tcms/atp.py` | `tests/test_atp.py` | —（模块行为测试） |
| SR-07 | 心跳丢失 N 周期判离线 + 迟滞恢复 | `tcms/watchdogs.py` | `tests/test_watchdogs.py` | —（模块行为测试） |
| SR-07 | CANopen 心跳生产者/消费者监督 | `tcms/nmt.py` | `tests/test_nmt.py` | —（模块行为测试） |
| SR-08 | TEC/REC 迁移与 Bus-Off 退避 | `tcms/errstate.py` | `tests/test_errstate.py` | —（模块行为测试） |
| SR-09 | WCRT ≤ 截止期可调度分析 | `tcms/schedulability.py` | `tests/test_schedulability.py` | —（模块行为测试） |
| SR-09 | 位级帧模型总线负载率 | `tcms/busload.py` | `tests/test_busload.py` | —（模块行为测试） |
| SR-10 | 事件记录 + 事故冻结窗口（黑匣子） | `tcms/recorder.py` | `tests/test_recorder.py` | —（模块行为测试） |
| SR-11 | 故障五阶段生命周期台账 | `tcms/faultlife.py` | `tests/test_faultlife.py` | —（模块行为测试） |
| SR-11 | YAML 场景注册表执行（故障键经 faultdb 校验） | `tcms/scenarios.py` | `tests/test_scenario_registry.py` | —（模块行为测试） |
| SR-12 | .asc 回放驱动业务逻辑响应 | `tcms/replay.py` | `tests/test_replay.py` | —（模块行为测试） |
| SR-13 | 自愈限 1 次超限转 FAULT | `tcms/ebm.py` | `tests/test_ebm.py` | —（模块行为测试） |
| SR-14 | 牵引制动互锁 | `tcms/interlocks.py` | `tests/test_interlocks.py` | —（模块行为测试） |
| SR-15 | 统一虚拟时间源确定性推进 | `tcms/timebase.py` | `tests/test_timebase.py` | —（模块行为测试） |
| SR-16 | 统一故障字典 FMEA（ID/级别/处置/SIL）+ 与 faultlevel 对齐校验 | `tcms/faultdb.py` | `tests/test_faultdb.py` | —（模块行为测试） |
| SR-16 | 203 条故障条目（键名/子系统/检测/注入手段）全字段校验 | `tcms/faults.yaml` | `tests/test_faultdb.py` | —（模块行为测试） |
| SR-17 | 需求追溯矩阵自证（SR→模块→测试文件双向覆盖） | `tests/rtm.csv` | `tests/test_rtm.py` | —（模块行为测试） |
| SR-18 | 失败现场自动导出（crash_site hook） | `tests/conftest.py` | `tests/test_failure_export.py` | —（模块行为测试） |
| SR-18 | smoke/safety 分层 marker 与 CI 门禁 | `pyproject.toml` | `tests/test_badges.py` | —（模块行为测试） |
| SR-19 | 速度信号无效/丢失时速度监督降级，不得按 0 速误判 | `scenarios/speed_signal_loss_derate.yaml` | `tests/test_scenario_registry.py` | `speed_signal_loss` |
| SR-20 | 速度 2oo3 冗余不足（单通道）时监督降级 | `scenarios/signal_redundancy_loss_derate.yaml` | `tests/test_scenario_registry.py` | `signal_redundancy_loss` |
| SR-21 | 后车门故障按未关处理，禁止发车 | `scenarios/rear_door_fault_interlock.yaml` | `tests/test_scenario_registry.py` | `rear_door_fault` |
| SR-22 | 门气源压力低时切除该侧门并降级 | `scenarios/door_air_pressure_low_derate.yaml` | `tests/test_scenario_registry.py` | `door_air_pressure_low` |
| SR-23 | 运行中检测到门开立即紧急制动 | `scenarios/door_open_moving_eb.yaml` | `tests/test_scenario_registry.py` | `door_open_moving` |
| SR-24 | 变流器故障码非 0 时封锁牵引并降级 | `scenarios/traction_converter_fault_derate.yaml` | `tests/test_scenario_registry.py` | `traction_converter_fault` |
| SR-25 | 变流器过温时降功率运行 | `scenarios/traction_converter_overheat_derate.yaml` | `tests/test_scenario_registry.py` | `traction_converter_overheat` |
| SR-26 | 直流母线欠压时封锁牵引 | `scenarios/dc_link_undervoltage_block.yaml` | `tests/test_scenario_registry.py` | `dc_link_undervoltage` |
| SR-27 | 制动缸压力泄漏时补偿制动降级 | `scenarios/brake_cylinder_leak_derate.yaml` | `tests/test_scenario_registry.py` | `brake_cylinder_leak` |
| SR-28 | 防滑系统故障告警，滑行保护降级 | `scenarios/brake_wsp_fault_warning.yaml` | `tests/test_scenario_registry.py` | `brake_wsp_fault` |
| SR-29 | 备用制动储压不足时限速运行 | `scenarios/brake_reserve_low_derate.yaml` | `tests/test_scenario_registry.py` | `brake_reserve_low` |
| SR-30 | 升弓失败无确认时降级运行 | `scenarios/pantograph_fail_raise_derate.yaml` | `tests/test_scenario_registry.py` | `pantograph_fail_raise` |
| SR-31 | 网压跌落告警，受流不良监控 | `scenarios/line_voltage_sag_warning.yaml` | `tests/test_scenario_registry.py` | `line_voltage_sag` |
| SR-32 | 绝缘报警时安全分断高压停车 | `scenarios/battery_insulation_shutdown.yaml` | `tests/test_scenario_registry.py` | `battery_insulation_fault` |
| SR-33 | 电池单体电压不均衡告警并启动均衡 | `scenarios/battery_voltage_imbalance_warning.yaml` | `tests/test_scenario_registry.py` | `battery_voltage_imbalance` |
| SR-34 | 充放电状态冲突时能量管理降级 | `scenarios/bms_charge_conflict_derate.yaml` | `tests/test_scenario_registry.py` | `bms_charge_state_conflict` |
| SR-35 | 辅助变流器故障时低压负载降级 | `scenarios/aux_converter_fault_derate.yaml` | `tests/test_scenario_registry.py` | `aux_converter_fault` |
| SR-36 | 辅助电压越限时负载降级保护 | `scenarios/aux_voltage_range_derate.yaml` | `tests/test_scenario_registry.py` | `aux_voltage_out_of_range` |
| SR-37 | 辅助变流器过载预警 | `scenarios/aux_converter_overload_warning.yaml` | `tests/test_scenario_registry.py` | `aux_converter_overload` |
| SR-38 | 辅助接触器粘连时安全分断 | `scenarios/aux_contactor_weld_shutdown.yaml` | `tests/test_scenario_registry.py` | `aux_contactor_weld` |
| SR-39 | 压缩机过流保护时制冷降级 | `scenarios/hvac_compressor_overcurrent_derate.yaml` | `tests/test_scenario_registry.py` | `hvac_compressor_overcurrent` |
| SR-40 | 滤网堵塞/加热故障/新风风门卡滞告警族 | `scenarios/hvac_comfort_degradation_warning.yaml` | `tests/test_scenario_registry.py` | `hvac_filter_clog`、`hvac_heater_fault`、`hvac_fresh_air_damper` |
| SR-41 | PIS 显示降级与紧急对讲失效告警 | `scenarios/pis_display_intercom_warning.yaml` | `tests/test_scenario_registry.py` | `pis_display_fault`、`pis_intercom_fault` |
| SR-42 | PIS 播报失步仅记录待数据同步 | `scenarios/pis_announce_desync_info.yaml` | `tests/test_scenario_registry.py` | `pis_announce_desync` |
| SR-43 | 探测器故障/灭火装置未就绪告警 | `scenarios/fire_detector_extinguisher_warning.yaml` | `tests/test_scenario_registry.py` | `fire_detector_fault`、`fire_extinguisher_fault` |
| SR-44 | 多区烟火报警立即停车疏散 | `scenarios/fire_multizone_shutdown.yaml` | `tests/test_scenario_registry.py` | `fire_multizone_alarm` |
| SR-45 | 轴温过高时限速运行至最近站 | `scenarios/bogie_axle_overheat_derate.yaml` | `tests/test_scenario_registry.py` | `bogie_axle_overheat` |
| SR-46 | 走行部监测传感器失效告警 | `scenarios/bogie_sensor_fault_warning.yaml` | `tests/test_scenario_registry.py` | `bogie_sensor_fault` |
| SR-47 | 客室照明支路故障告警 | `scenarios/light_group_fault_warning.yaml` | `tests/test_scenario_registry.py` | `light_group_fault` |
| SR-48 | 应急照明未投入时告警（疏散安全） | `scenarios/emergency_light_fail_warning.yaml` | `tests/test_scenario_registry.py` | `emergency_light_fail` |
| SR-49 | 网关网段/冗余链路丢失时降级 | `scenarios/gateway_segment_redundancy_loss.yaml` | `tests/test_scenario_registry.py` | `gateway_segment_fault`、`gateway_redundancy_loss` |
| SR-50 | 报文周期抖动告警（实时性劣化） | `scenarios/frame_period_jitter_warning.yaml` | `tests/test_scenario_registry.py` | `frame_period_jitter` |
| SR-51 | 司控台指令失效与 VCU 看门狗超时安全复位 | `scenarios/driver_console_vcu_watchdog.yaml` | `tests/test_scenario_registry.py` | `driver_console_fault`、`vcu_watchdog_timeout` |
| SR-52 | 传感器采集值超范围告警 | `scenarios/sensor_out_of_range_warning.yaml` | `tests/test_scenario_registry.py` | `sensor_out_of_range` |

## 2. 被追溯故障目录（场景模块 SR 可达）

| 故障键 | 中文名 |
|---|---|
| `speed_signal_loss` | 速度信号丢失 |
| `signal_redundancy_loss` | 速度传感器冗余丢失 |
| `rear_door_fault` | 后车门故障（按未关处理） |
| `door_air_pressure_low` | 车门气源压力不足 |
| `door_open_moving` | 运行中车门打开 |
| `traction_converter_fault` | 牵引变流器故障 |
| `traction_converter_overheat` | 牵引变流器过温 |
| `dc_link_undervoltage` | 直流母线欠压 |
| `brake_cylinder_leak` | 制动缸压力泄漏 |
| `brake_wsp_fault` | 防滑系统故障 |
| `brake_reserve_low` | 备用制动压力低 |
| `pantograph_fail_raise` | 受电弓无法升起 |
| `line_voltage_sag` | 网压跌落 |
| `battery_insulation_fault` | 电池绝缘监测报警 |
| `battery_voltage_imbalance` | 电池单体电压不均衡 |
| `bms_charge_state_conflict` | 充放电状态冲突 |
| `aux_converter_fault` | 辅助变流器故障 |
| `aux_voltage_out_of_range` | 辅助电压越限 |
| `aux_converter_overload` | 辅助变流器过载 |
| `aux_contactor_weld` | 辅助接触器粘连 |
| `hvac_compressor_overcurrent` | 空调压缩机过流保护 |
| `hvac_filter_clog` | 空调滤网堵塞 |
| `hvac_heater_fault` | 空调电加热故障 |
| `hvac_fresh_air_damper` | 新风风门卡滞 |
| `pis_display_fault` | PIS 显示屏故障 |
| `pis_intercom_fault` | 乘客紧急对讲失效 |
| `pis_announce_desync` | PIS 到站播报失步 |
| `fire_detector_fault` | 烟火探测器故障 |
| `fire_extinguisher_fault` | 灭火装置故障 |
| `fire_multizone_alarm` | 多区烟火报警 |
| `bogie_axle_overheat` | 轴温过高 |
| `bogie_sensor_fault` | 走行部监测传感器失效 |
| `light_group_fault` | 照明支路故障 |
| `emergency_light_fail` | 应急照明失效 |
| `gateway_segment_fault` | 网关网段故障 |
| `gateway_redundancy_loss` | 网关冗余链路丢失 |
| `frame_period_jitter` | 报文周期抖动 |
| `driver_console_fault` | 司控台指令失效 |
| `vcu_watchdog_timeout` | VCU 应用看门狗超时 |
| `sensor_out_of_range` | 传感器信号超范围 |
