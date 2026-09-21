# MCP 补强工单（取证式差距清单 + 分片计划）

> 建立：2026-09-21 ｜ **本工单只描述"把已有的最小实现补成生产级"，不是从零重写**
> 现状实现：`src/tcms_ai_platform/agent/mcp_server.py`（零第三方依赖，stdio + 最小 HTTP）
> 规范基准：**MCP 2026-07-28** —— <https://modelcontextprotocol.io/specification/2026-07-28>
> 取证工具：工作区 `tools\mcp_probe_tcms.py`（`--cmd mcp` 走 console script）

---

## 1. 结论与当前状态

| 分片 | 状态 | 内容 |
|---|---|---|
| **Slice 1** | ✅ **完工（2026-09-21）** | `run_scenario` 接真实引擎（R2 打通）· 四类失败文案可区分 · 工具补 `annotations` · `tcms-mcp` 入口 · 口径修正 |
| **Slice 2** | ✅ **完工（2026-09-21）** | `resources/list`（**分页，绝不静默截断**）+ `resources/read`（5 类 URI）· `prompts/list` + `prompts/get`（4 模板）· capabilities 声明三原语 · **枚举截断自曝** |
| **Slice 3** | 🟡 **部分完工（2026-09-21）** | ✅ 真协商（不支持则回自身版本）· ✅ **最小 Streamable HTTP**（POST /mcp + Bearer token）· ✅ `progress` 通知 · ✅ 协作式 `cancellation`（不回包）<br>⛔ 未做：SSE 流式与会话恢复 |
| **Slice 4** | ⬜ 未开始（可选） | `Tasks` 扩展对照（与检查点/续跑自评） |

**协议面**：3/3 服务端原语（tools/resources/prompts）· 2 种传输（stdio / HTTP）· **24 条契约测试** ·
包级 **320 passed + 1 skipped** · 全仓 **1632 passed + 4 skipped**（2026-09-21 干净单跑；⚠️ 勿并发跑两遍全量——
重 REST 测试会互相干扰，曾因此误报 28 例失败）。

**仍未实现（诚实边界）**：
- `elicitation`（服务端向用户索取信息）——项目已有"人工审批"节点，是天然映射，但协议层未接。
- `Tasks` 扩展（异步长任务 / 轮询 / 持久句柄）。
- 资源 `subscribe`（订阅变更）。
- HTTP 面的 SSE 流式、会话恢复、OAuth（当前仅 Bearer token）。
- 引擎执行**不可中途中断**：`cancellation` 只对**尚未开始**的请求生效（回包抑制）。

---

## 2. 取证记录

### 2.1 Slice 2/3 后复测（2026-09-21）

| 探测 | 结果 |
|---|---|
| `initialize` 协商 | 请求 `2025-06-18` → 回 `2025-06-18`（支持）；请求 `2099-01-01` → 回 **`2026-07-28`**（自身支持的最新版） |
| capabilities | `tools` + `resources` + `prompts` 三项如实声明 |
| `tools/list` | 6 个工具，5 个 `readOnlyHint=true` |
| **R2 真实执行** | `run_scenario("atp_curve_wave.yaml")` → `all_passed=true` / `assertions=1` / `engine=1.12.0` |
| `resources/list` | 全量 **371 条 / 8 页**（每页 50 + `nextCursor`）；URI 唯一性由测试断言 |
| `resources/read` | `tcms://index` · `fault/{key}` · `scenario/{file}` · `requirement/{req_id}` · `function/{fid}` 五类均可用 |
| `prompts/list` / `get` | 4 个模板；缺必填参数 → `-32602` |
| `progress` | 带 `_meta.progressToken` 时发 2 条 `notifications/progress`（0/2 → 1/2） |
| `cancellation` | `notifications/cancelled` 后该 id **不回包**（规范：MUST NOT respond） |
| HTTP | `http_jsonrpc` 纯函数：无 token 放行 / 错 token → 401 / 坏 JSON → 400 / 通知 → 202 |

### 2.2 Slice 1 前的基线（对照用）

| 探测 | 当时应答 | 判定 |
|---|---|---|
| 版本协商 | 原样回显任意 `202x` | ❌ 已修（现为真协商） |
| `resources/list` / `prompts/list` | `-32601` | ❌ 已补 |
| `run_scenario` | `isError`「需要引擎接线」 | ⚠️ 已打通 |
| 工具数口径 | docstring「5」/ README「5+1」/ 实际 6 | ⚠️ 已统一为 6 |

---

## 3. 规范对照（2026-07-28）

| 规范要求 | 现状 |
|---|---|
| 服务端特性 **Resources / Prompts / Tools** | ✅ 三者齐备 |
| 客户端特性 **Elicitation** | ⛔ 未实现（审批节点是天然映射，见 §5） |
| 附加工具 **Progress / Cancellation** / Error reporting | ✅ progress + cancellation；错误码用 JSON-RPC 标准码 |
| 扩展 **Tasks** | ⛔ 未实现（Slice 4 可选） |
| 传输 **stdio + Streamable HTTP** | 🟡 stdio ✅ / HTTP 最小实现（无 SSE） |
| 初始化 **版本协商** + capabilities | ✅ 真协商 + 三能力声明 |
| 安全：用户同意、**annotations 不可信**、访问控制 | ✅ annotations 齐备；HTTP 面 Bearer token；未做 OAuth |
| 列表分页（`nextCursor`） | ✅ resources 已实现，**不静默截断** |

---

## 4. 完工细节（供 review / 面试讲述）

**Slice 1（R2 真实执行）**：`make_runner(model, scenario_dir)` 复用 `server/app.py::/api/run/scenario`
的同一条引擎路径（`sc.run_yaml`），检查顺序 **模型 → 磁盘 → 引擎 → 执行** ——
"文件都不存在时说引擎不可用"是误导，故先查磁盘。`build_context(..., with_runner=False)`
保留诚实未接线路径（只读部署）。

**Slice 2（两个原语 + 截断自曝）**：
- 资源目录由真实资产展开（index/fault/scenario/requirement/function），**一个 req_id 只登记一条 URI**
  （RTM 一个需求可有多行映射，多行内容由 `resources/read` 一并返回）——此约束由测试断言唯一性守住。
- `toolassist.list_scenarios` 原有 `if len(out) >= 40: break` **静默截断**（真实场景 104）：现返回
  `total` / `truncated` / `next_cursor`，截断必自曝。

**Slice 3（协商与传输）**：`SUPPORTED_PROTOCOLS = ("2026-07-28", "2025-06-18", "2024-11-05")`，
不匹配即回自身最新支持版本。HTTP 逻辑抽成**纯函数** `http_jsonrpc(body, ctx, auth, token)`
（便于单测；`serve_http` 只做 stdlib `http.server` 接线），`--http/--port/--token/--readonly` 四个开关。

---

## 5. 与既有能力的映射（**面试故事**）

| MCP 规范 | 项目已有的东西 | 讲法 |
|---|---|---|
| Elicitation | 人工审批节点 | "审批不是我发明的——规范里叫 elicitation；我在协议层还没接，但语义早已实现" |
| Tasks 扩展 | LangGraph 检查点 + 断点续跑 | "长任务异步化我用检查点实现" |
| Progress | SSE 白盒事件流 | "白盒事件是 progress 的富化版本，能看到原始载荷" |
| 安全原则 | 四级权限 + 双保险门禁 | "权限分级比规范更严：高权限工具既不下发也不可调用" |
| 分页 | 104 场景 / 203 故障规模 | "枚举必须自曝截断——静默截断是最隐蔽的撒谎" |

---

## 6. 待办（下一步，按性价比排序）

1. **枚举截断自曝补齐**：`kb_filter_assets` 的 `items[:limit]` 仍是静默截断（本次只修了 `list_scenarios`）。
2. **`elicitation` 接线**：把人工审批节点映射到服务端发起请求（规范已定义，项目有真实场景）。
3. **HTTP SSE 流式**：让 progress/长任务走流式响应。
4. Slice 4：`Tasks` 扩展对照自评（求职加分）。
5. 版本口径：本轮新增对外能力，`_version.py`（当前 0.5.0）与 CHANGELOG 的 bump 待确认后一次到位。

---

## 7. 复现取证

```powershell
# 真实握手（stdio：python -m 或 console script）
python -X utf8 E:\DSHworkplace\tools\mcp_probe_tcms.py
python -X utf8 E:\DSHworkplace\tools\mcp_probe_tcms.py --cmd mcp

# HTTP 冒烟
cd E:\DSHworkplace\objects\tcms-agent
uv run tcms-mcp-http --port 8765 --token SECRET

# 契约测试（24 条）+ 包级回归 + lint
uv run pytest packages/platform/tests/test_mcp_server.py -q
uv run pytest packages/platform -q
uv run ruff check packages/platform/src packages/platform/tests
```
