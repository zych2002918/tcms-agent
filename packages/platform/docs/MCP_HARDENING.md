# MCP 补强工单（取证式差距清单 + 分片状态）

> 建立：2026-09-21 ｜ **本工单只描述"把已有的最小实现补成生产级"，不是从零重写**
> 现状实现：`src/tcms_ai_platform/agent/mcp_server.py`（零第三方依赖，stdio + Streamable HTTP）
> 规范基准：**MCP 2026-07-28** —— <https://modelcontextprotocol.io/specification/2026-07-28>
> 取证工具：工作区 `tools\mcp_probe_tcms.py`（`--cmd mcp` 走 console script）

---

## 1. 分片状态（全部完工）

| 分片 | 状态 | 内容 |
|---|---|---|
| **Slice 1** 真实执行 | ✅ 完工 | `run_scenario` 接真实引擎（R2 打通）· 四类失败文案可区分 · `annotations` · `tcms-mcp` 入口 · 口径修正 |
| **Slice 2** 两个原语 | ✅ 完工 | `resources/list`（**nextCursor 分页，绝不静默截断**）+ `resources/read`（5 类 URI）· `prompts/list` + `prompts/get`（4 模板）· capabilities 三原语 · **枚举截断自曝**（`count=本页 / total=总数 / truncated / next_cursor`，含 `cursor` 翻页） |
| **Slice 3** 协商/传输/订阅/审批 | ✅ 完工 | 真协商 · **Streamable HTTP**（JSON + **SSE 流** + **`Mcp-Session-Id`** 会话 + `DELETE` 终止 + Bearer token）· `tcms-mcp-http` 入口 · `notifications/progress` · 协作式 `cancellation`（**含执行期间取消不回包**）· **`resources/subscribe` + `resources/updated`**（mtime 驱动）· **elicitation 人工审批** |
| **Slice 4** Tasks 扩展 | ✅ 完工（**对照自评**，非实现） | 见 [`MCP_TASKS_ASSESSMENT.md`](MCP_TASKS_ASSESSMENT.md)：语义等价物 3/5、协议层 0/5、不做的三条理由、再做的门槛（重启后仍可按 id 查到结果） |

**协议面**：3/3 服务端原语 · 2 种传输（stdio / HTTP+SSE）· **42 条契约测试** ·
平台 **340 passed + 1 skipped** · `ruff check` clean。

---

## 2. 完工取证（2026-09-21 实测）

| 项 | 证据 |
|---|---|
| **R2 真实执行** | `run_scenario("atp_curve_wave.yaml")` → `isError=false` / `all_passed=true` / `engine=1.12.0` |
| **失败分类** | 未接线 / 场景不存在 / 磁盘缺文件 / 引擎不可用 —— 四类文案互不混淆（各有测试） |
| **版本协商** | 请求 `2025-06-18` → 回 `2025-06-18`；请求 `2099-01-01` → 回 **`2026-07-28`** |
| **能力声明** | `tools` + `resources(subscribe=true)` + `prompts` |
| **资源分页** | 全量 **371 条 / 8 页**，URI 唯一性由测试断言 |
| **枚举截断自曝** | `kb_filter_assets` / `list_scenarios` 截断时给 `total` + `truncated` + `next_cursor`，翻页不重叠、翻完覆盖全集 |
| **SSE** | `Accept: text/event-stream` → 通知帧先行（2 条 progress）+ 响应帧收尾 |
| **会话** | `initialize` 下发 `Mcp-Session-Id`；未知会话 404；`DELETE` → 204，重复终止 → 404 |
| **订阅** | 订阅场景资源 → `os.utime` 变更后 `watch_tick` 命中一次、不重复；退订后不再跟踪 |
| **审批** | `require_approval=true` → 接受则执行、**拒绝则完全不执行**、无通道/未声明能力则明确报错（不假装问过） |
| **取消** | 未开始 → 不回包；**执行途中被取消 → 结果不返回**（规范 MUST NOT respond） |

---

## 3. 规范对照（2026-07-28）

| 规范要求 | 现状 |
|---|---|
| 服务端特性 **Resources / Prompts / Tools** | ✅ 三者齐备 |
| 客户端特性 **Elicitation** | ✅ 已接（`run_scenario require_approval` → `elicitation/create`） |
| 附加工具 **Progress / Cancellation** / Error reporting | ✅ progress 通知 + 协作式取消；错误码用 JSON-RPC 标准码 |
| 资源 **subscribe** | ✅ `resources/subscribe` / `unsubscribe` + mtime 驱动 `resources/updated` |
| 传输 **stdio + Streamable HTTP** | ✅ stdio + HTTP（JSON 与 SSE 两种响应形态、会话管理） |
| 初始化 **版本协商** + capabilities | ✅ 真协商 + 三能力声明 + 记录客户端能力 |
| 列表分页（`nextCursor`） | ✅ resources 已实现；工具枚举用具名 `cursor`（同一纪律） |
| 安全：用户同意、annotations 不可信、访问控制 | ✅ annotations 齐备；HTTP Bearer token；审批走 elicitation |
| 扩展 **Tasks** | ⛔ 未实现（**已评估**：见 Slice 4 自评，非疏漏） |

### 诚实边界（明确不做，不是遗漏）

- **OAuth**：当前仅 Bearer token。本项目是**单机自托管**（无多租户、无第三方授权服务器），
  上 OAuth 只会增加部署成本而不增加真实安全性；需要时先接 MCP 的授权规范。
- **SSE 断线重放 / 事件 id 续传**：流内事件不做持久化，断线即重来。
- **引擎执行不可中断**：`run_yaml` 是原子调用；取消只保证"结果不返回"，不保证"中途停下"。
- **`function` 类资源不可订阅**：curated 资产无单一磁盘真源 → 诚实拒绝而不是假装订阅。

---

## 4. 与既有能力的映射（**面试故事**）

| MCP 规范 | 项目已有的东西 | 讲法 |
|---|---|---|
| Elicitation | 人工审批节点（ADR-007） | "审批不是我发明的——规范里叫 elicitation；我把它接到了真实执行入口上" |
| Tasks 扩展 | LangGraph 检查点 + 断点续跑 | "五个语义我实现过三个，协议层没做，理由与门槛写在自评里" |
| Progress | SSE 白盒事件流 | "白盒事件是 progress 的富化版本，能看到原始载荷" |
| 安全原则 | 四级权限 + 双保险门禁 | "权限分级比规范更严：高权限工具既不下发也不可调用" |
| 分页 / 截断 | 104 场景 / 203 故障规模 | "枚举必须自曝截断——静默截断是最隐蔽的撒谎" |

---

## 5. 复现取证

```powershell
# 真实握手（stdio：python -m 或 console script）
python -X utf8 E:\DSHworkplace\tools\mcp_probe_tcms.py
python -X utf8 E:\DSHworkplace\tools\mcp_probe_tcms.py --cmd mcp

# HTTP 冒烟（JSON / SSE / 会话）
cd E:\DSHworkplace\objects\tcms-agent
uv run tcms-mcp-http --port 8765 --token SECRET

# 契约测试（42 条）+ 包级回归 + lint
uv run pytest packages/platform/tests/test_mcp_server.py -q
uv run pytest packages/platform -q
uv run ruff check packages/platform/src packages/platform/tests
```
