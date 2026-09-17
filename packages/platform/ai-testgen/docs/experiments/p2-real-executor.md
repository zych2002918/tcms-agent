# P2 实验：真实执行器（executor_real）

> 目标：让「AI 生成的用例」跑**真实 pytest**（tcms-can-test 上游环境），
> 用真实执行结果量化质量——而不是 mock 的字符串启发式。

## 假设与设计

- **假设**：把生成用例编译成调用上游真实 API（`db.encode_message` / `simulator`
  注入 / `parser.collect` 解码断言）的 pytest 函数后，真实执行能跑通并通过，
  从而 exec_pass_rate 变成**真实验证**而非启发式。
- **控制变量**：同一批用例，只切换 executor（mock 启发式 vs 真实编译执行），
  比较两者口径差异。
- **基线对照**：真实执行结果 vs 上游自身测试套件（tcms-can-test 700+ 用例），
  证明「生成用例调用的 API/断言形态」与上游测试同源。

## 编译映射表（模板化编译器，非自由代码生成）

生成用例的自然语言（steps/expected）映射到上游**实测 API 原语**：

| 用例语义（中文关键词） | 编译成的真实 pytest |
|---|---|
| 车速/超速 + 越界/拒绝 | `with pytest.raises(EncodeError): _encode(db, "VehicleSpeed", SpeedKmh=200.1)` |
| 车速 + 上限可编码 | `data = _encode(db, "VehicleSpeed", SpeedKmh=200.0); assert len(data)==8` |
| 手柄级位 + 越界 | `_encode(db, "TractionBrakeHandle", HandlePosition=17)` → raises |
| SOC/电量 + 越界 | `_encode(db, "EnergyStatus", SocPercent=101)` → raises |
| 电池温度越界 | `_encode(db, "EnergyStatus", BatteryTemp=-41)` → raises |
| 车门故障 | `simulator.set_door_state(1,2)` → `collect` → `f["Door2State"]=="Fault"` |
| 心跳丢失 | `simulator.stop_message(proto.TCMS_HEARTBEAT)` → `count_frames==0` |
| 超速报警联动 | `simulator.set_speed(165)+send_alarm` → `collect` → `speed>160, Overspeed==1` |

**语义鸿沟实证**（真实执行器价值的直接证据）：首版车门模板断言
`f["Door2State"] == 2`（数字）→ 真实执行 **失败**，因为上游解码后 VAL_ 枚举是
字符串 `"Fault"`。修正为字符串后通过。mock 启发式永远抓不到这类错误——它
没有真实系统语义。

## 实验记录（2026-09-02 实测）

场景：真实资产派生 3+2 条边界/注入用例 → `run_real()` 上游执行。

```
# 用例批（curated-5：车速越界拒绝 / 上限可编码 / 手柄越界 / 车门故障 / 心跳丢失）
total=5 compiled=5 passed=5 failed=0
compile_rate = 1.0
exec_pass_rate = 1.0
真实 pytest: 5 passed in 2.79s
```

`test_ai_generated_p2.py` 生成于上游 `tests/`（conftest 可见），跑完即删
（keep_artifacts=True 才留存供审计）。

### 与 mock executor 口径对照

| executor | 口径 | 局限 |
|---|---|---|
| mock `executor.py` | 关键词检查（动作词/否定词） | 无法发现「枚举类型用错」这类真实语义错误 |
| real `executor_real.py` | 真实 pytest 断言 | 只能覆盖编译映射表内的语义，其余 honest uncompiled |

## 结论与边界

- **结论**：真实执行器闭环成立——从 tcms-can-test 真实资产派生需求 → LLM 风格
  用例 → 编译 → 真实 pytest → 量化通过率，链路端到端可复现。
- **边界**：当前编译面 = 边界编码 + 注入（车门/心跳/超速）4 类语义；
  其余生成用例 honest 计 uncompiled（compile_rate<1），这正是二期要做
  「扩大语义原语覆盖」的抓手。
- **可复现命令**：见 tests/test_executor_real.py（上游存在时跑真实执行；
  CI 无上游时自动 skip）。

## 下一步（P3 前置）

P3 多模型实验需要统一入口（mock / 真 LLM → 同一条真实执行管线），
executor_real 已提供该管线；P3 只需替换 LLM 客户端并固定 seed/温度。
