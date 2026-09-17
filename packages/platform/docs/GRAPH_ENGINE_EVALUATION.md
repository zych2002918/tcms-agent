# 图谱引擎评估与升级方案（P1-2：让“证据图”可查询、可迁移、可演进）

> 现状一句话：平台知识图谱是**内存对象**（`knowledge/graph.py`），651 节点 / 1167 边
> （enrich 后，含 12 症状 + 54 因果边，每条因果边带 `basis`/`note` 依据）。
> 它的优势是零依赖、离线、可单测、与资产同源；边界是“规模大了查询复杂了就吃力”。

## 1. 为什么要考虑图引擎（什么时候该换）

| 信号 | 现在 | 临界点 |
|---|---|---|
| 规模 | 655 节点 / 1176 边 | >5k 节点或 >50k 边，BFS/子图全内存展开明显慢 |
| 查询 | `subgraph/neighbors/shortest_path/causal_chain`（Python 遍历） | 需要 Cypher/GQL、递归路径、聚合统计、索引化邻接 |
| 并发 | 单进程内共享 | 多 worker / 多进程共享一份图，需要外部存储 |
| 沿袭 | 边带 basis/note（内存字段） | 需要“证据查询”作为一等能力（谁支持了什么结论、路径审计） |
| 持久化 | 每次启动重建 | 资产经常变但查询层想稳定/增量更新 |

诚实判断：**现阶段（教学演示 + 双侧测试资产）内存图是正确选择**；迁移到引擎是
“规模/并发/产品化”驱动的可选项，不是当前瓶颈。本文件给“何时迁、迁到哪、怎么迁”。

## 2. 候选引擎对比（2026-09 视角，按离线/嵌入优先排序）

| 引擎 | 形态 | 优点 | 代价 | 适配度 |
|---|---|---|---|---|
| **Kuzu**（embedded） | 单文件嵌入式，Cypher 子集 | 无服务、嵌入 Python 进程、ACID、图专属查询 | 需装依赖（pip/源码），新查询语言学习 | ★★★★（推荐候选） |
| **pyoxigraph** | 纯 Rust 绑定，RDF/SPARQL | 极轻、RDF 生态、可 SPARQL 查询 | SPARQL 对“属性图”语义略绕 | ★★★ |
| **Neo4j** | 服务 | 生态最成熟、Cypher、可视化 | 起服务、授权/运维、离线演示偏重 | ★★☆（现场演示时可选项） |
| **SQLite（关系）** | 文件 | 已有 sqlite 心智 | 递归查询要写 CTE、无图语义 | ★★（保守兜底） |

迁移到图引擎后建议保留的**单一真源**不变：节点/边内容仍由
`build_knowledge_graph`（资产派生）+ `enrich_graph`（领域注入）生成；引擎只是查询层。

## 3. 迁移方案（三步，可逐步验证）

**Step 1 · 无损导出（已落地）**
`knowledge/graphio.export_graph_json(g)`：全量导出节点（含 props）+ 边（含
`kind/basis/note`）与统计；导出计数必须 == `graph.stats()`（测试断言锁定）。
→ 这既是“给引擎的导入文件”，也是“给审计脚本的对拍基线”。

**Step 2 · 证据查询成为一等端点（已落地）**
- `graph.shortest_path(src, dst, max_depth, kinds)`：BFS 最短路，返回逐边
  `{src, dst, kind, basis, note}`（路径按行走方向定向，边依据原样）。
- `POST /api/kb/path`：如 `symptom:dashboard_flicker → fault:aux_24v_charger_fail`
  的可达证据链；不可达/未知节点 → `found=false` + 空 edges（诚实不编造路径）。
- 新增端点 / 结构都有 API 契约测试锁定（`tests/test_api_contracts.py`）。

**Step 3 · 迁入引擎（待规模触发时执行，已给验收口径）**
1. 用 `export_graph_json` 全量导入 Kuzu（或 pyoxigraph）：
   - 节点：`kind` 属性 + props 扁平化；主键 = node id；
   - 边：`(src)-[kind]->(dst)`，边属性 `basis/note`（依据可随路径返回）。
2. 迁移门禁（等价性测试）：导入后再导出/统计/最短路结果与原内存图一致
   （可用现有 shortest_path/export 测试作对拍基线）。
3. 查询层抽象：把 `subgraph/neighbors/shortest_path/causal_chain` 包一层
   `GraphBackend` 接口（memory/kuzu 两实现），业务代码（retriever/advisor/diagnoser）
   只依赖接口 —— 现在就能开始加接口，未来换引擎零业务改动。
4. 资产变更 → 增量同步：上游 faults/scenarios/symptoms 变化时重建或 upsert，
   与 `_assets` 快照纪律一致。

## 4. 对“不编造/可审计”红线的意义

- 每条因果边在引擎里仍然是 `basis/note` 属性 → 任何 Agent 回答、任何可视化路径都可
  回溯“这条关系是真实机制还是示意(derived)”，不因换存储而丢失诚实标注。
- 导出对拍 + 契约测试保证“换引擎不改语义”，避免迁移静默改变诊断/检索行为。

## 5. 结论与建议（决策记录）

- **当前**：保持内存图 + `graphio` 导出 + `/api/kb/path` 证据查询 + 契约/对拍测试
  （本批次已落地，机器自证）。
- **触发迁移的判据**：资产规模翻一个数量级、需要多进程共享图、或需要 Cypher 级
  复杂递归/聚合查询时，按 Step 3 迁 Kuzu（首选）并跑等价性门禁。
- 不建议现在引入图库服务依赖（离线演示/CI 成本不值得）。
