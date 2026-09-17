# 会话复盘：症状诊断 + 体验迭代 + 工程化路线图（P0→P2）交付

> 日期口径：2026-09（同日批次，工作树未提交内容见 git status；本文档为交接/复盘快照）。

## 一、今天做了什么（交付主线）

1. **症状/无码故障多跳诊断（Iteration A/B/C，前置批）**
   - A：`domain/data/symptoms.yaml` 12 症状资产（hints 全锚 202 真实故障键，annotation real 7/mixed 5）。
   - B：`domain/data/causal_edges.yaml` 54 条因果边（indicates 41 / causes 13；real 41 / derived 13）+ 图谱 `causal_chain` ≤3 跳遍历（逐跳 basis/note）。
   - C：`agent/diagnoser.py` + `POST /api/agent/diagnose`（无码症状 → 候选链 → 建议/置信/溯源；无命中诚实“不确定/需补充”；derived 显式标注）。
2. **产品体验四件事**
   - ① 场景命名释义：104/104 场景简短中文名 + 故障序列 `desc`（44 个 wave_* 模板名改写），desc 全链建模并展示。
   - ② FaultLab 高亮可读化：事件带 `fault_name/subsystem/domain_zh`；高亮点/chip 显示具体异常并可点击看说明；注入瞬间横幅“哪里+什么”。
   - ③ 图谱默认骨架 `/api/kb/overview`（56 节点/65 边）进页即显；2D/3D 单击详情、双击跳转；3D 缩放/适配/自转恢复。
   - ④ 遗留盘点/资产健康：derived 复核、LLM 仲裁、真 3D、e2e 等列为后续（本轮起逐项推进）。
3. **面试官技术栈路线图（P0→P2，逐项门禁）**
   - P0：API JSON 契约测试；FaultLab 播放器状态机化 + vitest 基建；Aliyun key 安全接入 + 真 LLM 候选内润色实测。
   - P1：BM25 词法 + 向量 RRF 混合检索 + golden 评测集；图谱可查询证据图（shortest_path /api/kb/path + 无损导出 + 引擎评估文档）；Agent 评测复用；Playwright e2e 主流程。
   - P2：3D 平滑增强（等价平滑 3D，纯函数几何/缓动）；症状诊断卡 LLM 候选内仲裁开关；ai-testgen 真 LLM 复测。

## 二、资产清单（本批新增/变更，机器自证口径）

- 平台 pytest **164 passed** / ruff clean；上游 957 passed + 1 skip / ruff clean。
- 前端：`tsc -b` 0 错误；vite build 通过；**vitest 16/16**（`cd web && pnpm test`）；浏览器 e2e **5/5 PASS**（`cd e2e && npm run smoke`，真实 chromium）。
- KB 服务态 651 节点 / 1167 边 / 640 文档；症状 12；因果边 54（real 41/derived 13）；场景 103（name 唯一、103/103 desc）。
- golden 评测集：检索 14 条（混合 14/14、纯向量 ≥13/14）；Agent golden：诊断 8/8、自由解析 5/5。
- 新数据/模块：symptoms/causal_edges/retrieval_golden/agent_golden YAML；lexical/golden/graphio/causal/evals/diagnoser；playerState/graph3d libs；e2e/main-flow.mjs；ai-testgen/docs/reports/llm_retest_2026.json（真 LLM：qwen-plus parse .667/compile 1.0/exec 1.0）。

## 三、教训（写给未来自己/新会话）

1. **编码纪律**：PowerShell 终端读 UTF-8 中文必乱码 → 一律用 read 工具；复杂内联 python 先落临时 .py 再执行（引号冲突）。
2. **计数同步纪律**：新增/改动任何测试/资产后，必须全链同步 tests 断言 + README/CHANGELOG/ACCEPTANCE/PROJECT_ARC/ROADMAP 数量（本批多次 148→153→156→159→162→164）。
3. **key 安全**：只存 `~/.tcms-ai-platform/settings.json`（仓库外），不入库不打印，命令里出现即视为需轮换；本会话 key 已明文暴露 → 建议轮换。
4. **LLM 真实链路的“反直觉坑”**：阿里 deepseek 端点对**较长中文/JSON 提示返回空 content**（2 键正常、6 键中文为空），定位后解法 = 提示极简化（候选限前 4、纯文本清单、按出现顺序兜底解析）；LLM 只在候选内仲裁，非候选键一律丢弃，失败自动落回规则。
5. **工程取舍不撒谎**：真神经 embedding / three 真 3D 未引入（离线与依赖成本），以 BM25 混合 + 可插拔 Embedder、等价平滑 3D 落地并文档化“何时才值得换”，不掩盖为已完成。
6. **测试/运行环境坑**：web 依赖为 pnpm 布局，npm 会坏 arborist → 用 pnpm；npm/Playwright 需网络 → 尽量复用本机缓存 chromium；faultlab 相对导入层级错误会让 `domain_zh` 静默为空（`..` vs `.`）。
7. **临时文件管理**：所有 `_*.py/_probe*.py/_dump*.txt` 用后即删，避免误提交与污染。

## 四、记忆（应沉淀为可复用知识点）

- TCMS 双仓资产真源位置与同步纪律（上游 faults/scenarios 唯一真源；平台 `_assets` 镜像 + 字节级/名称级测试）。
- 症状诊断红线“候选内仲裁、derived 标注、无命中诚实 no_match、绝不编造故障码”。
- 检索评测与 Agent 评测的“golden 防回退门禁”心智可复用到任何检索/生成链路。
- 播放器“纯逻辑抽离为 lib 纯函数 + vitest”是 UI 可测性范式（playerState/graph3d 即范例）。
