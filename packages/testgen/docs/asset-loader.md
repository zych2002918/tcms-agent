# 资产加载器（asset_loader）设计与口径

> 二期 P1：把 tcms-can-test 的**真实资产**（DBC 信号表 + 场景库）接入
> tcms-ai-testgen，为 P2 真实生成 / P3 多模型实验提供结构化输入。

## 1. 输入资产与定位

| 资产 | 路径 | 规模（实测） |
|---|---|---|
| DBC 信号表 | `<upstream>/tcms/tcms.dbc` | 22 报文 / 116 信号 / 11 节点 / VERSION 1.0.0 |
| 场景库 | `<upstream>/scenarios/*.yaml` | 104 个场景 |

上游仓库默认定位：`<本仓库>/../tcms-can-test`（与 CHARTER 一致），可用环境变量
`TCMS_UPSTREAM_ROOT` 覆盖（见 `default_upstream_root()`）。

tcms-ai-testgen 只**读**上游资产文件，**不 import** 上游 tcms 包——两个仓库解耦，
CI 无需上游代码即可跑（单测找不到真实资产时自动 skip，不硬崩）。

## 2. 模块与入口

- `asset_models.py` —— pydantic 数据契约（`DbcFile/DbcMessage/DbcSignal/...`）
- `asset_loader.py` —— 解析器与统一入口
  - `load_dbc(path) -> DbcFile`
  - `load_scenario(path) -> ScenarioAsset`
  - `load_assets(dbc_dir, scenario_dir) -> AssetBundle`
- `examples/demo_assets.py` —— 打印资产摘要（`python examples/demo_assets.py`）

### DBC 解析范围（子集，与 tcms.dbc 实测格式对齐）

- `VERSION` / `BU_:`（节点） / `BO_`（报文）/ `SG_`（信号：start|len@1+(-)，含
  factor/offset/[min|max]/单位）/ `VAL_`（枚举文本映射）/ `BA_`（周期 ms 与发送
  类型 cyclic/event，回填到报文）
- 位序 LSB、无多路复用、标准帧 ID 十进制。支持该子集之外的行静默跳过
  （NS_/BA_DEF_/BS_/注释/空白）。
- 声明了 `BO_/SG_/VAL_` 头却格式不匹配的**顶格行**记入 `raw_line_errors`
  （不静默吞坏行，诚实统计）；缩进的属性列表项（如 NS_ 下的 VAL_）不算坏行。

### 场景 YAML 三种写法（与上游 tcms/scenarios.py 语义对齐）

1. `{at, inject: {node, fault, level, impact, expect}}` —— 嵌套
2. `{at, recover: fault}` —— 恢复
3. `{at, action: inject|recover, fault, node, level, ...}` —— 事件式（door_cascade）

loader 全部归一化：`kind ∈ {inject, recover}`，非法的 action 步骤 → 该步跳过并记入
`ScenarioAsset.skipped_steps`（文件仍算 parsed，属于「半结构化」样本）。

## 3. 诚实统计口径（对齐 docs/metrics.md）

文件级坏文件（不存在 / 空 / YAML 语法错 / 编码错）**不抛异常**：

- 记入 `AssetBundle.bad_files` 与 `AssetIndexStats`；
- `parse_rate = parsed / (parsed + bad)`，分母**含坏文件**——残缺样本让
  「解析率」这个质量口径有意义，不虚报 100%；
- 空目录 = 合法空集（不是坏文件）；目录不存在 = 计 1 个坏（避免分母失真）。

## 4. 边界与已知不做

- 只做**静态解析**（结构提取），不做 DBC 运行时解码（那是上游 python-can 的事）；
- 不解析上游 `faults.yaml` 故障字典（上游 faultdb 已做校验；P2 需要时单列）；
- 坏 DBC 文件整体算 bad（行级错误只在合法文件内记录）。

## 5. 验收（P1 门禁，实测）

```
pytest tests --cov=tcms_ai_testgen     # 64 passed, 92.16%（asset_loader 91%）
ruff check .                           # All checks passed
python scripts/selfcheck.py            # PASS
python examples/demo_assets.py         # 1 DBC (8 msg/36 sig) + 13 scenarios, parse_rate=100%
```
