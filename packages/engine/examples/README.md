# examples/ —— 可直接运行的真实场景演示

本目录提供**不依赖测试框架**的可执行示例：一个真实格式的 CAN 总线日志
（Vector CANalyzer .asc）与一段带证据断言的回放链脚本。

## 内容

| 文件 | 说明 |
|---|---|
| `demo_trip.asc` | 一趟含 3 类故障的模拟列车运行日志（146 帧，hex ID） |
| `make_demo_asc.py` | 重新生成 `demo_trip.asc`（剧情见文件头注释） |
| `replay_demo.py` | 回放链演示：日志 → 业务逻辑 → 证据断言（回归门禁） |
| `consumer_api.py` | **第二消费者示例**：仅经 `tcms` 顶层公共 API 完成 总线→仿真→场景→回放（平台化契约实证） |

## 剧情（demo_trip.asc 时间轴）

1. **t≈2.2s** VCU 心跳中断 400ms → 看门狗检出 `vcu` 故障
2. **t=5.0s** 车速 185 km/h 超 EBI(160) → ATP 监督 `ebi` → 紧急制动触发
3. 紧急制动停车后缓解（RELEASED），列车重新启动
4. **t≈7.2s** 60 km/h 行车中车门误开 → 门-车联锁违规 → 紧急制动再次触发

## 运行

```bash
# 只跑回放并打印报告
python examples/replay_demo.py --report

# 带证据断言（5 步全过返回 0，任何一步失败返回非 0）
python examples/replay_demo.py

# 重新生成日志（如想改剧情）
python examples/make_demo_asc.py

# 第二消费者示例：站在外部使用者视角验证公共 API 面（4 项自证）
python examples/consumer_api.py
```

## 为什么这样设计

- **.asc 是行业标准格式**：Vector CANalyzer / CANoe 导出的日志即此格式，
  现场抓包可直接喂给 `tcms.replay.ReplayChain.from_asc()` 做真实数据回归。
- **演示即证据**：`replay_demo.py` 的断言同时是 CI 回归门禁
  （`tests/test_examples.py` 子进程复跑），示例不会被改坏。
- **公共 API 面有实证**：`consumer_api.py` 只 import `tcms` 顶层
  （`load_database / make_bus / load_fault_dictionary / scenarios.run_yaml`），
  证明 `pip install tcms-can-test` 后外部使用者可站在公共契约上写自己的
  第一个用例（平台化判据，CI demo-smoke 复跑）。
- 数据字节布局与 `tcms/replay.py` 的解析约定一一对应（见
  `make_demo_asc.py` 头注释），改剧情时保持布局一致即可。
