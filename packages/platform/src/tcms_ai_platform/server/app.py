"""FastAPI 应用工厂 + 资产/执行端点。

设计：
- 单例 AssetModel 在启动时加载（可注入上游路径，测试用 override）。
- 资产端点只读查询（机器自证：计数派生自加载结果）。
- 执行端点调上游 tcms 引擎真实跑场景（离线、确定性、无 LLM 依赖）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .._version import __version__
from ..core import AssetModel, load_asset_model
from ..core.sources import (
    ensure_engine_importable,
    resolve_asset_source,
)
from ..knowledge import (
    GraphSink,
    HybridRetriever,
    VectorStore,
    build_docs_from_asset,
    build_knowledge_graph,
)

# 供 uvicorn 直接 import 的默认实例（app:main 兼容）
_app_model: AssetModel | None = None
_app_upstream: Path | None = None


def _validate_steps(m: AssetModel, steps: list[dict]) -> None:
    """校验任意编排步骤序列（/run/custom、/faultlab/demo-steps 共用）。

    - 步骤非空；action ∈ inject/recover；inject 必须有 fault 且 fault ∈ 真实
      故障字典（未知 → 422 中文，防拼写错误静默通过）。
    """
    if not steps:
        raise HTTPException(422, "自定义场景至少需要一个步骤")
    for st in steps:
        at = st.get("at")
        action = st.get("action")
        fault = st.get("fault")
        if action == "inject":
            if not fault:
                raise HTTPException(422, f"at={at} 的 inject 步骤缺少 fault")
            if fault not in m.faults_by_key:
                raise HTTPException(
                    422,
                    f"未知故障键: {fault}（可用故障见 /api/faults，共 {len(m.faults_by_key)} 个）",
                )
        elif action != "recover":
            raise HTTPException(422, f"at={at} 的未知动作: {action!r}（仅支持 inject/recover）")
        elif not fault:
            raise HTTPException(422, f"at={at} 的 recover 步骤缺少 fault")


def _compose_interlock_note(m, keys: list[str], final_action: str | None) -> dict | None:
    """时序组合中的联锁联合提示（处置取决于原因的诚实标注）。

    触发：组合含 门域/牵引域 故障，且整链收尾期望 = emergency_brake。
    现实机制（KB 资产锚定）：由列车完整性丧失（integrity_loss）/ 运行中车门打开
    （door_open_moving）等 SIL4 严重安全原因引起的牵引丢失 → 紧急制动环线失电 →
    同时失去牵引并施加紧急制动；而可恢复部件故障/正常指令引起的牵引丢失仅 derate
    （traction_loss 默认处置，见其 action_note）。返回 None = 无联锁联合语义。
    """
    if final_action != "emergency_brake":
        return None
    dom = {m.faults_by_key[k].subsystem for k in keys if k in m.faults_by_key}
    # 门域 / 牵引域 参与 + EB 收尾 → 存在“严重原因 → EB 环线”的联合语境
    if not ({"车门", "牵引"} & dom):
        return None
    # 覆盖这些 SIL4 严重安全原因的现成联锁场景（真实资产，非杜撰）
    related = []
    for fk in ("integrity_loss", "door_open_moving"):
        if fk not in m.faults_by_key:
            continue
        for s in m.scenarios.values():
            if fk in s.fault_keys:
                related.append({"file": s.file, "name": s.name, "cause_fault": fk})
                break
    return {
        "msg": (
            "处置取决于原因：组合含门/牵引域故障且收尾期望紧急制动。若牵引丢失由列车完整性丧失"
            "（integrity_loss）或运行中车门打开（door_open_moving）等 SIL4 严重安全原因引起，"
            "紧急制动环线会失电，列车同时失去牵引并施加紧急制动；可恢复部件/正常指令引起的"
            "牵引丢失仅降级（traction_loss 默认处置）。"
        ),
        "scenarios": related,
    }


def _run_custom_steps(
    m: AssetModel,
    name: str,
    steps: list[dict],
    scenario_dir: Path,
) -> dict:
    """把任意编排步骤（dict 列表）在真实引擎上执行（不落盘）。

    /api/run/custom 与 /api/faultlab/demo-steps 共用的执行管线：
    校验 → 组装 YAML → parse_scenario → VirtualClock(virtual) + FaultLedger
    → ScenarioRunner.run → 与 run_yaml 同构的报告（含 assertions/ledger）。

    引擎缺失时抛 HTTPException 503（引导文案与 run_scenario 一致）。
    步骤校验失败时抛 HTTPException 422（中文，防拼写错误静默通过）。
    """
    try:
        import tcms.scenarios as sc  # noqa: PLC0415
        import tcms.timebase as _tb  # noqa: PLC0415
    except ImportError as e:  # 引擎缺失 → 明确引导（与 run_scenario 文案一致）
        raise HTTPException(
            503,
            f"TCMS 引擎不可用：自定义场景执行需要 tcms-can-test。请 pip install tcms-can-test，"
            f"或设置 TCMS_UPSTREAM_DIR 指向其目录。({e})",
        ) from None
    _validate_steps(m, steps)

    # 组装 YAML（显式 inject/recover 写法；level/impact/expect 缺省由引擎字典兜底）
    lines = [f"name: {name or 'custom'}", "steps:"]
    for st in sorted(steps, key=lambda s: float(s.get("at", 0))):
        at = st["at"]
        if st["action"] == "inject":
            lines.append(f"  - at: {at}")
            lines.append("    inject:")
            lines.append(f"      fault: {st['fault']}")
            if st.get("node"):
                lines.append(f"      node: {st['node']}")
            if st.get("level"):
                lines.append(f"      level: {st['level']}")
            if st.get("impact"):
                lines.append(f"      impact: {st['impact']}")
            if st.get("expect"):
                lines.append(f"      expect: {st['expect']}")
        else:
            lines.append(f"  - at: {at}")
            lines.append(f"    recover: {st['fault']}")
    yaml_text = "\n".join(lines)

    try:
        scenario = sc.parse_scenario(yaml_text, name=name or "custom")
        clock = _tb.VirtualClock(mode="virtual")
        from tcms.faultlife import FaultLedger, ScenarioRunner  # noqa: PLC0415

        rep = ScenarioRunner(FaultLedger(clock), scenario, clock).run()
    except Exception as e:  # 组装/执行异常 → 500 含信息
        raise HTTPException(500, f"自定义场景执行失败: {e}") from None
    # 补引擎版本（run_yaml 报告不带；供 faultlab._engine_block 填 version）
    rep = dict(rep)
    rep["engine_version"] = __import__("tcms").__version__
    return rep


class RunScenarioRequest(BaseModel):
    """单场景执行请求。

    必须定义在模块级：本文件启用 `from __future__ import annotations`，
    函数内定义的模型无法被 FastAPI 在模块全局解析前向引用，会被误判为
    query 参数（422: missing query req）。
    """

    scenario: str  # 场景文件名（含 .yaml）


class RunScenariosRequest(BaseModel):
    """批量执行请求（当前无参数，占位便于扩展）。"""

    pass


class SearchRequest(BaseModel):
    """GraphRAG 混合检索请求（模块级：同上 FastAPI 前向引用约束）。"""

    query: str
    k: int = 5


class SubgraphRequest(BaseModel):
    """图谱子图请求（seed 为 node id，如 fault:overspeed）。"""

    seed: str
    depth: int = 2


class PathRequest(BaseModel):
    """图谱可达路径请求（证据图查询：两节点间最短路，逐边带依据）。"""

    src: str
    dst: str
    max_depth: int = 8


class AgentRunRequest(BaseModel):
    """Agent 任务执行请求（模块级：FastAPI 前向引用约束）。"""

    task_id: str | None = None  # None = 全跑


class FaultLabRequest(BaseModel):
    """FaultLab 演示请求：选一个真实场景（兼容旧调用）。

    steps 可选：若提供（自定义步骤序列）则等价于 POST /api/faultlab/demo-steps
    （与 demo-steps 同一条校验/执行/重建管线，向后兼容复用）。
    """

    scenario: str | None = None  # 场景文件名（含 .yaml）；steps 提供时可为空
    steps: list[dict] | None = None  # [{at,action,fault,node,level,expect,impact}]
    name: str = "custom"  # steps 变体时自定义序列名


class DemoFromStepsRequest(BaseModel):
    """FaultLab 任意序列演示请求：从编排步骤（非已存场景）生成动画。

    模块级（FastAPI 前向引用约束，同 FaultLabRequest）。
    """

    name: str = "custom"
    steps: list[dict]  # [{at,action,fault,node,level,expect,impact}]


class AgentFreeRequest(BaseModel):
    """自由 Agent 目标请求：一句自然语言 → 自动解析为可执行任务。

    模块级（FastAPI 前向引用约束，同 RunScenarioRequest）。
    """

    goal: str  # 自然语言目标（如「验证车门故障不能发车」）


class DiagnoseRequest(BaseModel):
    """症状多跳诊断请求（模块级：FastAPI 前向引用约束）。

    输入症状/无码故障描述（如「仪表盘闪烁但无故障码」）→ kb 检索症状资产 →
    图谱沿因果边取候选链 → 诊断步骤建议（排序分 + 溯源）。证据不足时明确
    "不确定/需补充"，绝不编造故障码（红线）。

    P1-1：可选 `session_id` 启用多轮锚点记忆——服务端只存证据引用（上轮症状
    资产 + 真实候选键 + 用户现象事实），追问（"刚才/继续/那个部位"）时复用
    锚点继续走链；响应 evidence.session.anchor_used 如实标注。
    P1-2：候选不可区分时响应带 clarification（需补充的区分性观测），不硬排。
    """

    message: str  # 症状描述
    depth: int = 3  # 因果多跳深度（2~3 为推荐诊断链深）
    max_candidates: int = 8
    use_llm: bool = False  # 开启 LLM 候选内仲裁（仅重排候选；需已配置 key）
    session_id: str | None = None  # 多轮会话锚点记忆键（P1-1，可选）


class ToolAssistRequest(BaseModel):
    """受约束工具查证请求（P1-a function-calling）。

    LLM（配 key 时）可在真实只读工具面内自主查证（kb_search / symptom_diagnose /
    kb_node / list_scenarios），≤3 轮循环后给纯文本答复；工具结果全真实、
    参数经 schema/JSON 校验、回复自证使用过的工具。无 key/失败 → llm_generated=false
    的确定性引导（绝不假装调用过工具）。
    """

    message: str  # 用户问题/查证目标
    use_llm: bool = True
    max_rounds: int = 3


class ComposeSeqPick(BaseModel):
    """用户对某条未锚定子句的点选并入：clause=该句原文，key=该句域候选的真实键。"""

    clause: str
    key: str


class ComposeSeqRequest(BaseModel):
    """时序连锁原子化请求：原句 + 已点选并入的未锚定子句（可逐个点选，逐个并入）。

    message 始终是用户**原始一句话**（不重写）；每次点候选都带上前几轮 picks 累积，
    让其余未锚定子句保留在响应里继续可点，不再"选一个丢一个"。
    """

    message: str
    picks: list[ComposeSeqPick] = []


class AdvisorTurnRequest(BaseModel):
    """编排顾问对话请求（模块级：FastAPI 前向引用约束）。

    永不 422 拒绝任何 message——无法匹配内存故障时进入多轮对话
    （KB 检索澄清 / 候选确认 / 自定义新故障流程草稿）。
    """

    message: str
    draft_steps: list[dict] | None = None  # 当前前端手动编排的步骤草稿（可选）
    history: list[dict] | None = None  # [{role, content}]（可选，供上下文）


class CustomStepRequest(BaseModel):
    """自定义场景单步（模块级：FastAPI 前向引用约束）。

    与内置场景 YAML 步骤同构：at 为注入时刻；action ∈ inject/recover。
    fault 必填（inject 用）；node/level 仅 inject 有意义。
    """

    at: float
    action: str  # inject / recover
    fault: str | None = None
    node: str | None = None
    level: str | None = None
    expect: str | None = None
    impact: str | None = None


class CustomScenarioRequest(BaseModel):
    """自定义场景请求：一组手动编排的步骤（真实引擎执行，不落盘）。"""

    name: str = "custom"
    steps: list[CustomStepRequest]


class ComposeRequest(BaseModel):
    """组合器请求：一句话点名多个故障 → 原子化组合计划（可选直接真实执行）。

    run=True 时计划直接走 /api/run/custom 同一执行管线（真实引擎断言）。
    history：多轮上下文（前几轮用户输入），供"再补一个 XX/再加上刚才那个"式续编。
    """

    goal: str
    run: bool = False
    history: list[str] = []


class SettingsUpdateRequest(BaseModel):
    """设置保存请求（前端「设置/引导」页写入；key 只落本机文件）。"""

    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None  # 允许空串 = 清除
    asset_dir: str | None = None  # 空串 = 清空(回到自动)
    onboarding_done: bool | None = None
    theme: str | None = None  # 前端主题偏好 dark/light/""（透传持久化，仅供前端）


class LlmModelsRequest(BaseModel):
    """拉取模型列表请求（前端引导页「测试连接并获取模型」）。

    base_url / api_key 可选：显式传入时仅用于本次探测（不落库、不进响应）；
    缺省则按 env → 本地 settings → 默认 解析。key 永不随响应返回。
    """

    base_url: str | None = None
    api_key: str | None = None


def create_app(asset_model: AssetModel | None = None, upstream: str | Path | None = None) -> FastAPI:
    """应用工厂。

    资产源解析（新人友好，无需配置）：
    - upstream 显式传入 → 用它（测试/开发）
    - 否则按环境解析：TCMS_UPSTREAM_DIR → 兄弟目录 → 平台内置快照
    """
    global _app_model, _app_upstream

    # 场景目录（真实引擎从这里读场景 YAML；bundled 模式用平台快照）
    scenario_dir: Path
    if asset_model is None:
        if upstream is not None:
            asset_model = load_asset_model(upstream)
            scenario_dir = Path(upstream) / "scenarios"
            # 显式上游（开发/测试）：把其根加入 sys.path 使 tcms 引擎可 import
            import sys as _sys

            root = Path(upstream)
            if str(root) not in _sys.path:
                _sys.path.insert(0, str(root))
        else:
            source = resolve_asset_source()
            from ..core.loader import load_from_source

            asset_model = load_from_source(source)
            ensure_engine_importable(source)
            scenario_dir = source.scenarios_dir
    else:
        # 显式传入 asset_model（测试）：场景目录取上游根，无则退回内置
        src_root = Path(str(asset_model.source_upstream))
        candidate = src_root / "scenarios"
        scenario_dir = candidate if candidate.is_dir() else Path(__file__).resolve().parents[2] / "_assets" / "scenarios"
    _app_model = asset_model
    _app_upstream = scenario_dir

    # 引擎可用性探测（启动时一次；供 /api/system/status 与前端引导）
    def _probe_engine() -> dict:
        try:
            import importlib.util

            if importlib.util.find_spec("tcms") is None:
                return {"ok": False, "reason": "not_installed"}
            import tcms  # noqa: F401

            return {"ok": True, "version": getattr(tcms, "__version__", "?")}
        except ImportError as e:
            return {"ok": False, "reason": f"import_failed:{e}"}

    _engine_status = _probe_engine()

    # 知识底座（P2）：图谱 + 向量 + 混合检索（同一 app 实例内单例）
    graph = build_knowledge_graph(asset_model)
    # 向量通道 embedder：默认字符级哈希（离线零网络）；TCMS_EMBEDDER=api 且配
    # key 时启用真语义 /embeddings（失败自动降级哈希，见 llm_backend.make_kb_embedder）
    from ..agent.llm_backend import make_kb_embedder

    store = VectorStore(embedder=make_kb_embedder())
    store.add_many(build_docs_from_asset(asset_model))
    # 领域知识注入（P6）：真实列车领域知识(驾驶模式/联锁/阈值/标准/危害/概念)扩图谱
    from ..domain import enrich_graph as _enrich

    _enrich_report = _enrich(graph, store)
    retriever = HybridRetriever(store, graph)
    sink = GraphSink(graph)
    _run_counter = {"n": 0}
    # P1-1 诊断会话锚点记忆（进程内；每次诊断调用 cleanup 过期会话）
    from ..agent.diagnose_memory import AnchorMemory as _AnchorMemory

    _diag_memory = _AnchorMemory()

    # Agent Harness：后端可插拔——有 LLM key(env / 本地设置 / 凭据文件)
    # 用 LLM 决策(失败自动落回 Mock)，否则 Mock 确定性（离线可复现）。
    # 注意：设置页可在运行期改 key/provider → 用闭包每次现取（懒构建）。
    from ..agent import (
        AgentHarness,
        LLMAgentBackend,
        MockAgentBackend,
        default_tasks,
        llm_available,
    )

    def _current_backend_mode() -> str:
        return "llm" if llm_available() else "mock"

    def _make_harness() -> AgentHarness:
        if _current_backend_mode() == "llm":
            return AgentHarness(
                asset_model, retriever, _app_upstream, backend=LLMAgentBackend()
            )
        return AgentHarness(
            asset_model, retriever, _app_upstream, backend=MockAgentBackend()
        )

    app = FastAPI(
        title="TCMS × AI 测试平台",
        version=__version__,
        description="列车控制软件测试平台（L1 资产模型 + 真实引擎执行）",
    )

    # ---- 元信息 ----

    @app.get("/api/health")
    def health() -> dict:
        """健康 + 双段版本：platform 自身版本 + 上游 tcms 引擎版本（若可导入）。"""
        engine_version = None
        try:
            import tcms  # noqa: F401

            engine_version = getattr(tcms, "__version__", None)
        except Exception:  # noqa: BLE001 - 引擎缺失不影响 health
            engine_version = None
        return {
            "status": "ok",
            "version": __version__,  # 平台自身版本（0.2.0）
            "asset_version": asset_model.version,  # 资产模型版本
            "engine_version": engine_version,  # 上游 tcms 引擎版本（可能为 None）
        }

    @app.get("/api/stats")
    def stats() -> dict:
        return asset_model.stats()

    @app.get("/api/source")
    def source() -> dict:
        return {"upstream": asset_model.source_upstream, "load": asset_model.load_stats}

    @app.get("/api/system/status")
    def system_status() -> dict:
        """环境状态（供前端引导）：引擎 / LLM key / 资产源 / 可用能力。"""
        from ..agent import llm_available

        has_key = llm_available()
        eng = _probe_engine()
        return {
            "engine": eng,
            "llm_key": has_key,
            "agent_backend": _current_backend_mode(),  # mock(离线) / llm(已配 key)
            "asset_mode": asset_model.source_upstream,
            "capabilities": {
                "browse_assets": True,
                "knowledge_graph": True,
                "symptom_diagnosis": True,  # 症状多跳诊断（无码症状 → 图谱因果链）
                "run_scenario": eng["ok"],
                "agent": eng["ok"],
                "llm_generation": has_key,  # 真 LLM 写测试（可选增强）
            },
            "fix_hints": {
                "engine": (
                    []
                    if eng["ok"]
                    else [
                        "pip install -e \".[upstream]\"  # 从 GitHub 安装 tcms-can-test 引擎",
                        "或设置环境变量 TCMS_UPSTREAM_DIR 指向 tcms-can-test 目录后重启",
                    ]
                ),
                "llm": (
                    []
                    if has_key
                    else [
                        "当前 Agent 使用离线 Mock 后端，无需 key 即可演示全流程。",
                        "如需真 LLM 生成/规划，到「设置」页配置 API（阿里云/DeepSeek/OpenAI 兼容），或设 DASH_API_KEY（见 .env.example）",
                    ]
                ),
            },
        }

    # ---- 设置（外部可配置接口：新手引导页读写本地 settings，key 永不外泄）----

    @app.get("/api/settings")
    def settings_get() -> dict:
        """读取非敏感设置（含 provider 预设与当前状态，绝不含 api_key）。"""
        from ..core import settings as _settings

        view = _settings.public_view()
        view["providers"] = _settings.PROVIDER_PRESETS
        return view

    @app.post("/api/settings")
    def settings_update(req: SettingsUpdateRequest) -> dict:
        """保存设置（写入 ~/.tcms-ai-platform/settings.json；key 只落本机文件）。"""
        from ..core import settings as _settings

        patch: dict = {}
        if req.llm_provider is not None:
            patch.setdefault("llm", {})["provider"] = req.llm_provider
        if req.llm_base_url is not None:
            patch.setdefault("llm", {})["base_url"] = req.llm_base_url.strip()
        if req.llm_model is not None:
            patch.setdefault("llm", {})["model"] = req.llm_model.strip()
        if req.llm_api_key is not None:
            patch.setdefault("llm", {})["api_key"] = req.llm_api_key.strip()
        if req.asset_dir is not None:
            patch["asset_dir"] = req.asset_dir.strip()
        if req.onboarding_done is not None:
            patch["onboarding_done"] = bool(req.onboarding_done)
        if req.theme is not None:
            v = (req.theme or "").strip().lower()
            patch["theme"] = v if v in ("dark", "light") else ""
        if not patch:
            raise HTTPException(400, "无有效设置字段")
        try:
            _settings.save(patch)
        except RuntimeError as e:
            raise HTTPException(500, str(e)) from e
        return _settings.public_view()

    @app.post("/api/settings/clear-api-key")
    def settings_clear_api_key() -> dict:
        """清除已保存的 API key（不留本机文件）。"""
        from ..core import settings as _settings

        try:
            _settings.save({"llm": {"api_key": ""}})
        except RuntimeError as e:
            raise HTTPException(500, str(e)) from e
        return _settings.public_view()

    @app.post("/api/llm/models")
    def llm_models(req: LlmModelsRequest) -> dict:
        """测试 LLM 连通性并拉取可用模型列表（OpenAI 兼容 GET /models）。

        前端引导/设置页用它完成「填 key → 测试连接 → 从真实列表选模型」，
        消除"手写模型名可能不存在"的试错。base_url/api_key 可选显式传入
        （仅本次探测不落库）；解析链与 LLMAgentBackend 一致。key 绝不出现在响应。
        """
        from ..agent.llm_backend import fetch_models, llm_available

        try:
            if not (req.api_key or llm_available()):
                return {"ok": False, "error": "未配置 API key —— 请先填写 key 再测试连接", "models": []}
            models = fetch_models(base_url=req.base_url, api_key=req.api_key or None)
            return {"ok": True, "models": models, "error": None}
        except Exception as e:  # noqa: BLE001 - 探测失败 → 诚实文案（引导页提示可改手动输入）
            return {"ok": False, "error": str(e), "models": []}

    # ---- 知识底座（P2）----

    @app.post("/api/kb/search")
    def kb_search(req: SearchRequest) -> dict:
        """GraphRAG 混合检索（P1-1：向量 + BM25 词法，RRF 融合）→ 图谱邻接证据。"""
        return retriever.retrieve_hybrid(req.query, k=req.k)

    @app.get("/api/kb/overview")
    def kb_overview(limit: int = 3) -> dict:
        """默认“基础关联图谱”骨架：13 系统 + 11 功能 + 每功能代表故障 + 关联边。

        前端进入图谱页（未搜索/未选种子）时直接展示本视图：
        节点 = system:SYS-* 全部 + function:F-* 全部 + 各功能 fault_keys 前 limit 个真实故障；
        边 = 这些节点之间既有的 belongs_to / triggers / covers / injects 等真实关系。
        计数全部派生自 enrich 后的图与资产模型（机器自证）。
        """
        keep: set[str] = set()
        for n in graph.nodes.values():
            if n.kind in ("system", "function"):
                keep.add(n.id)
        for fn in asset_model.functions.values():
            for fk in fn.fault_keys[:max(1, limit)]:
                nid = f"fault:{fk}"
                if nid in graph.nodes:
                    keep.add(nid)
        nodes = [
            {"id": n.id, "kind": n.kind, "label": n.label}
            for n in graph.nodes.values()
            if n.id in keep
        ]
        edges = []
        seen = set()
        for e in graph.edges:
            if e.src in keep and e.dst in keep and e.src != e.dst:
                key = (e.src, e.dst, e.kind)
                if key not in seen:
                    seen.add(key)
                    item = {"src": e.src, "dst": e.dst, "kind": e.kind}
                    if e.basis:
                        item["basis"] = e.basis
                    edges.append(item)
        return {
            "mode": "overview",
            "seed": "overview",  # 与 /api/kb/subgraph 同构（无真实 seed 节点，前端据此进入“骨架视图”）
            "depth": 0,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

    @app.post("/api/kb/path")
    def kb_path(req: PathRequest) -> dict:
        """证据图可达查询：两节点间最短路（逐边带 kind/basis/note）。

        如 symptom:dashboard_flicker → fault:aux_24v_charger_fail 的可达证据链。
        不存在/超深 → found=false + 空 edges（诚实不编造路径）。
        """
        if req.src not in graph.nodes or req.dst not in graph.nodes:
            return {"src": req.src, "dst": req.dst, "found": False, "hops": 0, "edges": []}
        path = graph.shortest_path(req.src, req.dst, max_depth=max(1, min(req.max_depth, 12)))
        return {
            "src": req.src,
            "dst": req.dst,
            "found": path is not None,
            "hops": len(path) if path else 0,
            "edges": path or [],
        }

    @app.post("/api/kb/subgraph")
    def kb_subgraph(req: SubgraphRequest) -> dict:
        """以某实体为中心的子图（图谱工作台数据源）。"""
        return retriever.subgraph(req.seed, req.depth)

    @app.get("/api/kb/stats")
    def kb_stats() -> dict:
        return {
            "graph": graph.stats(),
            "vector": store.stats(),
            "domain_enrichment": _enrich_report["files"],
            "symptom_causal": _enrich_report.get("symptom_causal", {}),
        }

    @app.get("/api/kb/nodes")
    def kb_nodes(kind: str | None = None, q: str | None = None, limit: int | None = None) -> list[dict]:
        """节点浏览/搜索（前端下拉、图谱定位用）。kind ∈ graph.NODE_TYPES。

        limit 缺省时按前端浏览上限 200 截断（UI 性能用）；显式传大值可拿全量
        （计数/审计口径，避免"端点静默截断"误导数量断言）。
        """
        cap = 200 if limit is None else limit
        out = []
        for n in graph.nodes.values():
            if kind and n.kind != kind:
                continue
            if q and q.lower() not in n.label.lower() and q.lower() not in n.id.lower():
                continue
            out.append({"id": n.id, "kind": n.kind, "label": n.label})
            if len(out) >= cap:
                break
        return out

    @app.get("/api/kb/node/{node_id}")
    def kb_node(node_id: str) -> dict:
        """单个节点 + 其直接邻接（图谱漫游）。"""
        if node_id not in graph.nodes:
            raise HTTPException(404, f"节点不存在: {node_id}")
        n = graph.nodes[node_id]
        return {
            "id": n.id,
            "kind": n.kind,
            "label": n.label,
            "props": n.props,
            "neighbors": [
                {"id": nb, "label": graph.nodes[nb].label, "kind": graph.nodes[nb].kind}
                for nb, _ in graph.neighbors(node_id)
            ],
        }

    # ---- 资产：协议 ----

    @app.get("/api/messages")
    def list_messages() -> list[dict]:
        return [
            {
                "name": m.name,
                "frame_id": hex(m.frame_id),
                "node": m.node,
                "cycle_ms": m.cycle_ms,
                "send_type": m.send_type,
                "signals": list(m.signal_names),
            }
            for m in asset_model.messages.values()
        ]

    @app.get("/api/messages/{name}")
    def get_message(name: str) -> dict:
        try:
            m = asset_model.message(name)
        except KeyError:
            raise HTTPException(404, f"报文不存在: {name}") from None
        return {
            "name": m.name,
            "frame_id": hex(m.frame_id),
            "node": m.node,
            "length": m.length,
            "cycle_ms": m.cycle_ms,
            "send_type": m.send_type,
            "signals": [
                {
                    "name": s.name,
                    "bit_length": s.bit_length,
                    "scale": s.scale,
                    "offset": s.offset,
                    "range": [s.minimum, s.maximum],
                    "unit": s.unit,
                    # 枚举表序列化为有序 [{value,label}]（JSON 键恒为字符串，
                    # 用 list 避免 int/str 键歧义）
                    "choices": [
                        {"value": k, "label": v}
                        for k, v in sorted(s.choices.items())
                    ],
                    "receivers": list(s.receivers),
                }
                for s in (asset_model.signals[n] for n in m.signal_names)
            ],
        }

    @app.get("/api/signals")
    def list_signals() -> list[dict]:
        return [
            {
                "name": s.name,
                "message": s.message,
                "unit": s.unit,
                "choices": [
                    {"value": k, "label": v} for k, v in sorted(s.choices.items())
                ],
            }
            for s in asset_model.signals.values()
        ]

    # ---- 资产：列车结构 ----

    @app.get("/api/devices")
    def list_devices() -> list[dict]:
        return [
            {
                "name": d.name,
                "role": d.role,
                "messages": list(d.messages),
                "faults": list(d.faults),
            }
            for d in asset_model.devices.values()
        ]

    # ---- 资产：故障/场景 ----

    @app.get("/api/faults")
    def list_faults() -> list[dict]:
        return [
            {
                "fid": f.fid,
                "key": f.key,
                "name": f.name,
                "subsystem": f.subsystem,
                "layer": f.layer,
                "level": f.level,
                "action": f.action,
                "sil": f.sil,
                "action_note": f.action_note,
            }
            for f in asset_model.faults_by_key.values()
        ]

    @app.get("/api/faults/{key}")
    def get_fault(key: str) -> dict:
        try:
            f = asset_model.fault(key)
        except KeyError:
            raise HTTPException(404, f"故障不存在: {key}") from None
        return {
            "fid": f.fid,
            "key": f.key,
            "name": f.name,
            "subsystem": f.subsystem,
            "layer": f.layer,
            "level": f.level,
            "action": f.action,
            "sil": f.sil,
            "desc": f.desc,
            "detect": f.detect,
            "inject": f.inject,
            "recovery": f.recovery,
            "action_note": f.action_note,
        }

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict]:
        return [
            {
                "file": s.file,
                "name": s.name,
                "desc": s.desc,
                "steps": len(s.steps),
                "fault_keys": sorted(s.fault_keys),
                "nodes": sorted(s.nodes),
            }
            for s in asset_model.scenarios.values()
        ]

    @app.get("/api/scenarios/{file}")
    def get_scenario(file: str) -> dict:
        try:
            s = asset_model.scenario(file)
        except KeyError:
            raise HTTPException(404, f"场景不存在: {file}") from None
        return {
            "file": s.file,
            "name": s.name,
            "steps": [
                {
                    "at": st.at,
                    "action": st.action,
                    "node": st.node,
                    "fault": st.fault,
                    "level": st.level,
                    "expect": st.expect,
                    "impact": st.impact,
                }
                for st in s.steps
            ],
        }

    # ---- FaultLab：故障演示（真实场景 + 事件时间线 + 通道曲线）----

    @app.get("/api/faultlab/scenarios")
    def faultlab_scenarios() -> list[dict]:
        """可演示的场景清单（全部真实资产场景）。"""
        return [
            {
                "file": s.file,
                "name": s.name,
                "desc": s.desc,
                "steps": len(s.steps),
                "fault_keys": sorted(s.fault_keys),
                "duration_hint": max((st.at for st in s.steps), default=0) + 4,
            }
            for s in asset_model.scenarios.values()
        ]

    def _faultlab_demo_from(
        name: str,
        steps: list[dict] | None,
        scenario_file: str | None,
    ) -> dict:
        """FaultLab 演示公共管线（真实场景文件 或 自定义步骤序列）。

        引擎可用时先真实执行（场景文件走 run_yaml；自定义步骤走
        _run_custom_steps 同一执行管线），用真实断言作处置来源；
        引擎不可用时退化为故障字典 action（诚实标注，engine_asserted=false）——
        自定义序列主要用于「看动画」，无引擎仍能出（前端场景执行/编排结果
        一键跳转 FaultLab 演示），但 honesty 标注不接入引擎。
        """
        from ..faultlab import build_curve, build_demo, build_demo_from_steps

        # 校验必须在引擎 try 之外：422/404 属契约错误，不得被引擎降级吞掉
        if steps is not None:
            _validate_steps(asset_model, steps)

        run_result: dict | None = None
        engine_ok = _probe_engine()["ok"]
        if engine_ok:
            try:
                if steps is not None:
                    run_result = _run_custom_steps(asset_model, name, steps, _app_upstream)  # type: ignore[arg-type]
                else:
                    import tcms.scenarios as sc  # noqa: PLC0415

                    run_result = sc.run_yaml(str(_app_upstream / scenario_file))
                    if run_result is not None:
                        run_result = dict(run_result)
                        run_result["engine_version"] = __import__("tcms").__version__
            except Exception:  # noqa: BLE001 - 引擎失败退化为字典来源（诚实标注）
                run_result = None

        if steps is not None:
            demo = build_demo_from_steps(asset_model, name, steps, run_result)
        else:
            demo = build_demo(asset_model, scenario_file, run_result)  # type: ignore[arg-type]
        curve = build_curve(asset_model, demo)
        return {
            "demo": demo,
            "curve": curve,
            "engine_asserted": run_result is not None,
            "honesty_note": demo["honesty"],
        }

    @app.post("/api/faultlab/demo")
    def faultlab_demo(req: FaultLabRequest) -> dict:
        """重建演示时间线（事件 + 曲线一次返回）。

        向后兼容：只传 scenario → 真实资产场景（引擎可用时真实执行，用真实
        断言作处置来源；不可用退化为故障字典 action，诚实标注）。
        传 steps（可选，复用旧端点）→ 走 demo-steps 逻辑（任意序列动画）。
        """
        if req.steps is not None:
            return _faultlab_demo_from(name=req.name, steps=req.steps, scenario_file=None)
        if not req.scenario:
            raise HTTPException(422, "FaultLab 请求需提供 scenario 或 steps")
        try:
            asset_model.scenario(req.scenario)
        except KeyError:
            raise HTTPException(404, f"场景不存在: {req.scenario}") from None
        return _faultlab_demo_from(name=req.name, steps=None, scenario_file=req.scenario)

    @app.post("/api/faultlab/demo-steps")
    def faultlab_demo_steps(req: DemoFromStepsRequest) -> dict:
        """从任意故障序列（非已存场景）生成演示动画。

        - 校验 steps 中每个 fault ∈ 真实故障字典（未知 → 422 中文）。
        - 引擎可用 → 先真实执行该序列（与 /api/run/custom 同一执行管线，
          复用 _run_custom_steps），用真实断言作为处置来源；
          引擎缺失 → 不 503：仍出动画（该端点主要用途是"看动画"），
          engine_asserted=false 且 demo.engine.notes 诚实标注未接引擎执行。
        - 返回与 /api/faultlab/demo 同构：{demo, curve, engine_asserted, honesty_note}；
          demo.scenario = "custom/<name>"，demo.engine/pipeline 全字段同真实场景。
        """
        _validate_steps(asset_model, req.steps)
        return _faultlab_demo_from(name=req.name, steps=req.steps, scenario_file=None)

    # ---- 资产：需求 / 功能 ----

    @app.get("/api/requirements")
    def list_requirements() -> list[dict]:
        out = []
        for req_id, reqs in asset_model.requirements.items():
            out.append(
                {
                    "req_id": req_id,
                    "rows": [
                        {"module": r.module, "test_file": r.test_file, "verifies": r.verifies}
                        for r in reqs
                    ],
                }
            )
        return out

    @app.get("/api/functions")
    def list_functions() -> list[dict]:
        return [
            {
                "fid": f.fid,
                "name": f.name,
                "description": f.description,
                "messages": list(f.messages),
                "signals": list(f.signals),
                "fault_keys": list(f.fault_keys),
                "requirements": list(f.requirements),
            }
            for f in asset_model.functions.values()
        ]

    # ---- 执行：真实场景 ----

    @app.post("/api/run/scenario")
    def run_scenario(req: RunScenarioRequest) -> dict:
        """在真实上游 tcms 引擎上执行一个场景（离线确定性）。"""
        try:
            asset_model.scenario(req.scenario)  # 校验存在
        except KeyError:
            raise HTTPException(404, f"场景不存在: {req.scenario}") from None

        # 引擎已在 create_app 中 ensure_engine_importable（活上游入 path 或已安装）
        try:
            import tcms.scenarios as sc  # noqa: PLC0415
        except ImportError as e:  # 引擎缺失 → 明确引导（新人可读）
            raise HTTPException(
                503,
                f"TCMS 引擎不可用：场景执行需要 tcms-can-test。请 pip install tcms-can-test，"
                f"或设置 TCMS_UPSTREAM_DIR 指向其目录。({e})",
            ) from None

        try:
            rep = sc.run_yaml(str(_app_upstream / req.scenario))
        except Exception as e:  # 上游引擎异常 → 500 含信息
            raise HTTPException(500, f"场景执行失败: {e}") from None
        # 沉淀闭环：执行结果写回知识库（组织记忆）
        _run_counter["n"] += 1
        sink.record_run(
            f"run-{_run_counter['n']:03d}",
            req.scenario,
            {"passed": rep.get("passed"), "failed": rep.get("failed"), "all_passed": rep.get("all_passed")},
        )
        return {
            "scenario": rep.get("scenario"),
            "steps": rep.get("steps"),
            "assertions": rep.get("assertions"),
            "passed": rep.get("passed"),
            "failed": rep.get("failed"),
            "all_passed": rep.get("all_passed"),
            "engine_version": __import__("tcms").__version__,
            "run_id": f"run-{_run_counter['n']:03d}",
        }

    @app.post("/api/run/scenarios")
    def run_scenarios(req: RunScenariosRequest) -> dict:
        """批量执行全部 13 场景（真实引擎，逐场景独立台账）。"""
        try:
            import tcms.scenarios as sc  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover - 引擎缺失引导
            raise HTTPException(
                503, f"TCMS 引擎不可用：请 pip install tcms-can-test 或设置 TCMS_UPSTREAM_DIR。({e})"
            ) from None

        results = []
        total_pass = total_fail = 0
        for file in asset_model.scenarios:
            rep = sc.run_yaml(str(_app_upstream / file))
            total_pass += rep.get("passed", 0)
            total_fail += rep.get("failed", 0)
            results.append(
                {
                    "scenario": file,
                    "name": asset_model.scenario(file).name,
                    "passed": rep.get("passed"),
                    "failed": rep.get("failed"),
                    "all_passed": rep.get("all_passed"),
                }
            )
        return {
            "engine_version": __import__("tcms").__version__,
            "scenario_count": len(results),
            "total_pass": total_pass,
            "total_fail": total_fail,
            "all_passed": total_fail == 0,
            "results": results,
        }

    # ---- Agent Harness（P4）----

    @app.get("/api/agent/tasks")
    def agent_tasks() -> list[dict]:
        """任务库（锚定真实故障字典）。"""
        return [
            {
                "task_id": t.task_id,
                "title": t.title,
                "goal": t.goal,
                "target_fault": t.target_fault,
                "expected_action": t.expected_action,
            }
            for t in default_tasks(asset_model)
        ]

    @app.post("/api/agent/run")
    def agent_run(req: AgentRunRequest) -> dict:
        """跑 Agent 任务（真实引擎执行 + 轨迹 + 评分）。"""
        tasks = default_tasks(asset_model)
        if req.task_id:
            tasks = [t for t in tasks if t.task_id == req.task_id]
            if not tasks:
                raise HTTPException(404, f"任务不存在: {req.task_id}")
        # 每次现取后端（设置页改 key/provider 后无需重启即生效）
        return _make_harness().run_tasks(tasks)

    @app.post("/api/agent/free")
    def agent_free(req: AgentFreeRequest) -> dict:
        """自由 Agent 目标：自然语言 → 解析(规则+LLM仲裁) → 真实执行。

        像 DSH Harness 一样自由：不给任务 id，只给一句话目标。
        返回解析结果（命中故障/期望处置/置信度）+ 与 /api/agent/run 同构的执行报告。

        未命中真实故障时**不裸 422 死路**：复用顾问的 KB 检索澄清，返回
        HTTP 200 + { no_match: true, suggested_faults, followup_question }，
        前端据此引导用户点选候选故障继续 —— 让 AI 参与理解（而非只报错）。
        """
        from ..agent.advisor import _rag_fault_candidates
        from ..agent.freeform import NoFaultMatch, parse_free_goal
        from ..agent.llm_backend import llm_available as _llm_ok
        from ..agent.toolassist import rule_enum_runnable

        try:
            parsed = parse_free_goal(
                asset_model, req.goal, seq=1, use_llm=_llm_ok()
            )
        except NoFaultMatch as e:
            # 规则零命中 → ① 若是“仅告警/降级但仍可运行”类盘点问题：先用规则直接枚举真实故障作答；
            # ② 否则走 KB 检索澄清（“你可能指这些”），给候选而非硬 422
            enum = rule_enum_runnable(asset_model, req.goal)
            rag_cands, evidence = _rag_fault_candidates(asset_model, retriever, req.goal)
            resp: dict = {
                "goal": req.goal,
                "no_match": True,
                "detail": str(e),
                "suggested_faults": rag_cands[:5],
                "rag_evidence": evidence,
                "followup_question": "上面哪个最接近你想验证的？回复/点选故障名即可继续。",
            }
            if enum:
                shown = enum.get("data", {}).get("shown") or []
                resp["kb_answer"] = enum["reply"]
                resp["kb_items"] = enum["data"]
                resp["suggested_faults"] = (
                    [
                        {
                            "key": s["key"],
                            "name": s["name"],
                            "level": s.get("level") or "",
                            "action": s.get("action") or "",
                            "confidence": 0,
                            "matched_on": "rule:action∈{warning,derate}",
                        }
                        for s in shown
                    ]
                    or rag_cands[:5]
                )
                resp["followup_question"] = "这些是字典里『告警/降级但仍可运行』的真实故障——想深挖哪一个？点选后继续。"
            elif not rag_cands:
                # ③ 宽泛问法推理（域词×故障句式 → 该域真实故障+场景定向推荐），离线可答
                from ..knowledge.vague import analyze_vague

                vg = None
                try:
                    vg = analyze_vague(asset_model, req.goal)
                except Exception:  # noqa: BLE001
                    vg = None
                if vg:
                    resp["kb_answer"] = vg["reply"]
                    resp["kb_items"] = {
                        "kind": vg["kind"],
                        "domain": vg["domain_zh"],
                        "count": vg["count"],
                        "scenarios": vg["scenarios"],
                    }
                    resp["suggested_faults"] = [
                        {
                            "key": f["key"],
                            "name": f["name"],
                            "level": f["level"],
                            "action": f["action"],
                            "confidence": 0,
                            "matched_on": "rule:vague-domain",
                        }
                        for f in vg["faults"]
                    ]
                    resp["followup_question"] = "上面是按『域词 × 故障句式』推给你的真实故障——点选即可让 Agent 去查证。"
                else:
                    # ④ 兜底：目标确有 TCMS 信号时，用混合检索的 fault 命中给候选
                    from ..knowledge.retriever import _DOMAIN_TERMS as _TCMS_DOMAIN_TERMS

                    gl = (req.goal or "").lower()
                    has_tcms = any(
                        any(t.lower() in gl for t in terms) for terms in _TCMS_DOMAIN_TERMS.values()
                    ) or any(k.lower() in gl for k in asset_model.faults_by_key)
                    extra: list[dict] = []
                    if has_tcms:
                        for h in (retriever.retrieve_hybrid(req.goal, k=10).get("hits") or []):
                            if h.get("kind") != "fault":
                                continue
                            doc_id = str(h.get("doc_id") or "")
                            key = doc_id.split(":", 1)[1] if doc_id.startswith("fault:") else doc_id
                            name = (str(h.get("text", "")).split(" ", 1)[0] or key)[:40]
                            extra.append(
                                {
                                    "key": key,
                                    "name": name,
                                    "level": "",
                                    "action": "",
                                    "confidence": round(float(h.get("score", 0)), 3),
                                    "matched_on": "hybrid",
                                }
                            )
                            if len(extra) >= 5:
                                break
                    if extra:
                        resp["suggested_faults"] = extra
                        resp["followup_question"] = "没锚定到唯一故障，但知识库找到这些可能相关的真实故障——点选继续查证。"
            return resp
        task = parsed.to_task(req.goal, seq=1)
        resp = _make_harness().run_tasks([task])
        return {
            "goal": req.goal,
            "parsed": {
                "fault": parsed.fault,
                "fault_name": parsed.fault_name,
                "expected": parsed.expected,
                "expected_zh": parsed.expected_zh,
                "confidence": parsed.confidence,
                "resolver": parsed.resolver,
                "matched_on": parsed.matched_on,
            },
            "matched_task_id": task.task_id,  # T-FREE-1（自由任务由解析动态生成）
            **resp,
        }

    @app.post("/api/agent/toolassist")
    def agent_toolassist(req: ToolAssistRequest) -> dict:
        """受约束工具查证（P1-a）：LLM 在真实只读工具面内自主查证（≤3 轮）。

        工具：kb_search / symptom_diagnose / kb_node / list_scenarios —— 结果全真实，
        参数经校验、未开放工具一律拦截、回复自证 used_tools；无 key/失败 → 确定性引导
        （llm_generated=false），绝不假装调用过工具。
        """
        from ..agent.toolassist import TOOLS_AVAILABLE, assist

        res = assist(
            asset_model,
            graph,
            retriever,
            req.message,
            max_rounds=req.max_rounds,
            use_llm=req.use_llm,
        )
        res["tools_available"] = list(TOOLS_AVAILABLE)
        return res

    @app.post("/api/agent/diagnose")
    def agent_diagnose(req: DiagnoseRequest) -> dict:
        """症状多跳诊断（C 步）：无码症状描述 → 图谱因果链候选 + 诊断步骤建议。

        输入「仪表盘闪烁但无故障码」这类症状文本：
            1. kb 检索症状资产（RAG）→ 定位 symptom 节点；
            2. 图谱沿 indicates/causes 因果边取 depth≤3 候选链（逐跳带依据）；
            3. 输出候选故障（真实字典键）+ 排序分（非概率）+ 验证动作 + 场景复现建议。
        诚实纪律：无命中 → no_match=true + 需补充引导；derived 候选明确标注
        仅示意；所有故障键来自 203 条真实字典，不编造故障码。

        P1-1：带 session_id 时读写锚点记忆（evidence.session.anchor_used 标注是否
        沿上一轮症状锚点继续；只存证据引用，不存摘要）。P1-2：候选不可区分时
        响应带 clarification 追问。
        """
        from ..agent.diagnose_memory import build_anchor as _build_anchor
        from ..agent.diagnoser import diagnose_symptom
        from ..agent.llm_backend import llm_available as _llm_ok

        depth = max(2, min(req.depth, 3))  # 诊断链深限定 2~3（规格）
        use_llm = bool(req.use_llm) and _llm_ok()  # 显式开启 + key 就绪才真调 LLM
        _diag_memory.cleanup()
        anchor = _diag_memory.get(req.session_id)
        res = diagnose_symptom(
            asset_model,
            graph,
            retriever,
            req.message,
            depth=depth,
            max_candidates=req.max_candidates,
            use_llm=use_llm,  # 仅候选内仲裁；失败自动落回规则排序
            session_anchor=anchor,
        )
        res["session_id"] = req.session_id
        res["session_anchor_used"] = bool(res.get("session_anchor_used"))
        if req.session_id and (req.message or "").strip():
            _diag_memory.put(req.session_id, _build_anchor(res, req.message))
        return res

    @app.post("/api/agent/compose")
    def agent_compose(req: AdvisorTurnRequest) -> dict:
        """一句话 → 原子资产组合 → 真实执行（Q3 组合器闭环入口）。

        输入任意编排语句（如「编排一个场景：先车门故障再叠加超速最后恢复」）：
        1. 经 advisor 意图识别 → 若意图为 compose_scenario（识别出 ≥1 真实故障）→
           生成错峰注入/恢复步骤草稿；
        2. 直接提交 /api/run/custom 真实引擎执行（含 sink 沉淀）；
        3. 返回组合步骤 + 执行报告（前端可展示步骤并跳转 FaultLab 动画）。

        意图不明时返回 advisor 澄清回复（不 422）。依赖引擎，缺失时 503 引导。
        """
        from ..agent.advisor import advisor_turn
        from ..agent.llm_backend import llm_available as _llm_ok

        turn = advisor_turn(
            asset_model,
            retriever,
            req.message,
            draft_steps=req.draft_steps,
            history=req.history,
            use_llm=_llm_ok(),
        )
        if turn.intent != "compose_scenario" or not turn.suggested_steps:
            # 不是组合意图（或需澄清）→ 返回顾问回复，前端引导
            return {
                "goal": req.message,
                "composed": False,
                "intent": turn.intent,
                "reply": turn.reply,
                "fault_matches": turn.fault_matches,
                "needs_clarification": turn.needs_clarification,
                **({"followup_question": turn.followup_question} if turn.followup_question else {}),
                **({"rag_evidence": turn.rag_evidence} if turn.rag_evidence else {}),
            }
        steps = [dict(s) for s in turn.suggested_steps]
        # Q7 三栏溯源：每个组合故障 → {字典真实字段 / 隶属系统 / 覆盖场景}
        provenance: list[dict] = []
        seen_fk: set[str] = set()
        for st in steps:
            fk = st.get("fault")
            if not fk or fk in seen_fk:
                continue
            seen_fk.add(fk)
            fd = asset_model.fault(fk) if fk in asset_model.faults_by_key else None
            if fd is None:
                continue
            fnode = f"fault:{fk}"
            sys_name = ""
            scen_list: list[str] = []
            for e in graph.edges:
                if e.src == fnode and e.kind == "belongs_to" and e.dst.startswith("system:"):
                    n = graph.nodes.get(e.dst)
                    if n:
                        sys_name = n.label
                elif e.dst == fnode and e.kind == "injects" and e.src.startswith("scenario:"):
                    scen_list.append(e.src.split(":", 1)[1])
            provenance.append(
                {
                    "fault": fk,
                    "name": fd.name,
                    # 栏① 真实资产（故障字典逐字段）
                    "asset": {
                        "fid": fd.fid,
                        "level": fd.level,
                        "action": fd.action,
                        "sil": fd.sil,
                        "desc": fd.desc,
                        "detect": fd.detect,
                        "inject": fd.inject,
                    },
                    # 栏② 图谱事实（隶属系统 + 覆盖场景）
                    "graph_facts": {"system": sys_name or "未归类", "scenarios": sorted(scen_list)},
                    # 栏③ Agent 建议 = 步骤里的期望处置与注入时刻
                    "agent_action": st.get("expect") or fd.action,
                }
            )
        rep = _run_custom_steps(
            asset_model,
            req.message[:40] or "compose",
            steps,
            _app_upstream,  # type: ignore[arg-type]
        )
        _run_counter["n"] += 1
        sink.record_run(
            f"run-{_run_counter['n']:03d}",
            "compose",
            {"passed": rep.get("passed"), "failed": rep.get("failed"), "all_passed": rep.get("all_passed")},
        )
        return {
            "goal": req.message,
            "composed": True,
            "intent": "compose_scenario",
            "fault_matches": turn.fault_matches,
            "steps": steps,
            "provenance": provenance,  # Q7 三栏溯源：源资产 / 图谱事实 / Agent 建议
            "run": {
                "scenario": rep.get("scenario"),
                "passed": rep.get("passed"),
                "failed": rep.get("failed"),
                "all_passed": rep.get("all_passed"),
                "assertions": rep.get("assertions"),
                "engine_version": __import__("tcms").__version__,
            },
        }

    @app.post("/api/agent/compose_seq")
    def agent_compose_seq(req: ComposeSeqRequest) -> dict:
        """时序连锁原子化（Q3 v2）：先A后B随后C最后D → 逐原子故障错峰注入 + 真实执行。
        - 按时序连接词/标点逐子句锚定真实故障（不是只取整句第一个）；
        - 锚不上的句子不进计划，返回 unresolved + 该域候选（用户逐句点选后并入）；
        - picks：已点选的 {clause,key} 按原句位置并入 keys（只认该子句域候选真实键），
          其余未锚定子句继续留在 unresolved 里可点——不再"选中一个、另一个消失"；
        - "最后紧急制动"等收尾期望句 → final_action（整链期望，不是故障）；
        - 步骤语义：错峰注入=连锁叠加（非"好了再下一个"），全部注入后统一恢复。
        """
        from ..agent.advisor import _compose_steps
        from ..agent.composer import ComposeError, plan_compose_seq

        try:
            seq = plan_compose_seq(
                asset_model,
                req.message,
                picks=[{"clause": p.clause, "key": p.key} for p in req.picks],
            )
        except ComposeError as e:
            return {
                "goal": req.message,
                "composed": False,
                "intent": "compose_seq",
                "reply": str(e),
                "fault_matches": [],
                "needs_clarification": True,
            }

        keys = seq["keys"]
        steps = _compose_steps(asset_model, keys)
        fault_matches = [
            {
                "key": k,
                "name": asset_model.faults_by_key[k].name,
                "level": asset_model.faults_by_key[k].level,
                "action": asset_model.faults_by_key[k].action,
            }
            for k in keys
        ]
        # 三栏溯源（与 /api/agent/compose 同口径）
        provenance: list[dict] = []
        for fk in keys:
            fd = asset_model.faults_by_key[fk]
            fnode = f"fault:{fk}"
            sys_name = ""
            scen_list: list[str] = []
            for e in graph.edges:
                if e.src == fnode and e.kind == "belongs_to" and e.dst.startswith("system:"):
                    n = graph.nodes.get(e.dst)
                    if n:
                        sys_name = n.label
                elif e.dst == fnode and e.kind == "injects" and e.src.startswith("scenario:"):
                    scen_list.append(e.src.split(":", 1)[1])
            provenance.append(
                {
                    "fault": fk,
                    "name": fd.name,
                    "asset": {
                        "fid": fd.fid,
                        "level": fd.level,
                        "action": fd.action,
                        "sil": fd.sil,
                        "desc": fd.desc,
                        "detect": fd.detect,
                        "inject": fd.inject,
                    },
                    "graph_facts": {"system": sys_name or "未归类", "scenarios": sorted(scen_list)},
                    "agent_action": fd.action,
                }
            )
        rep = _run_custom_steps(asset_model, req.message[:40] or "compose_seq", steps, _app_upstream)
        _run_counter["n"] += 1
        sink.record_run(
            f"run-{_run_counter['n']:03d}",
            "compose",
            {"passed": rep.get("passed"), "failed": rep.get("failed"), "all_passed": rep.get("all_passed")},
        )
        resp: dict = {
            "goal": req.message,
            "composed": True,
            "intent": "compose_scenario",
            "fault_matches": fault_matches,
            "faults": keys,
            "steps": steps,
            "provenance": provenance,
            "run": {
                "scenario": rep.get("scenario"),
                "passed": rep.get("passed"),
                "failed": rep.get("failed"),
                "all_passed": rep.get("all_passed"),
                "assertions": rep.get("assertions"),
                "engine_version": __import__("tcms").__version__,
            },
        }
        if seq.get("final_action"):
            resp["final_action"] = seq["final_action"]
        if seq.get("unresolved"):
            resp["unresolved"] = [
                {
                    "clause": u["clause"],
                    "domain_candidates": u.get("domain_candidates"),
                }
                for u in seq["unresolved"]
            ]
        resp["chain_note"] = (
            f"已按时序把 {len(keys)} 个真实故障做原子化错峰注入（连锁叠加，不是“好了再下一个”）；"
            + (f"整链收尾期望：{resp.get('final_action','')}。" if seq.get("final_action") else "收尾统一恢复。")
            + (
                f"其中 {seq.get('picked_count', 0)} 个由你在未锚定句中点选并入（按原句位置）。"
                if seq.get("picked_count")
                else ""
            )
            + (
                f"另有 {len(resp.get('unresolved', []))} 句还没锚定，下方点选可继续并入（选一个不丢另一个）。"
                if resp.get("unresolved")
                else ""
            )
        )
        # 联锁联合提示：牵引丢失/门域故障 + 收尾 EB 期望 → 处置取决于原因的诚实标注。
        # 现实机制：由列车完整性丧失（integrity_loss）/ 运行中门开（door_open_moving）等
        # SIL4 严重安全原因引起的牵引丢失 → EB 环线失电 → 同时失去牵引并紧急制动；
        # 可恢复部件/正常指令引起的牵引丢失仅 derate（traction_loss 默认处置）。
        interlock_note = _compose_interlock_note(asset_model, keys, seq.get("final_action"))
        if interlock_note:
            resp["interlock_note"] = interlock_note["msg"]
            resp["interlock_scenarios"] = interlock_note["scenarios"]
        return resp

    @app.post("/api/agent/advisor")
    def agent_advisor(req: AdvisorTurnRequest) -> dict:
        """编排顾问：多轮对话（输入无法匹配内存故障时**绝不 422**）。

        把用户一句自然语言（故障意图/编排请求/不完整描述/任何话）转成结构化
        顾问回复：
            - 规则明确命中 → match_fault（解释该故障 + 默认处置 + 现成场景）
            - 编排意图     → compose_scenario（suggested_steps 可直接 /api/run/custom）
            - 弱/多候选   → clarify（候选确认，followup_question 引导）
            - 规则零候选   → 不拒绝：KB 检索澄清（"你可能指这些"）或
                             out_of_domain（友好引导回 TCMS 主题）或
                             custom_proposal（自定义新故障流程草稿）
        LLM key 可用时回复文案由真 LLM 润色（llm_generated=true）；
        无 key 用规则模板（诚实标注离线）。
        """
        from ..agent.advisor import advisor_turn
        from ..agent.llm_backend import llm_available as _llm_ok

        turn = advisor_turn(
            asset_model,
            retriever,
            req.message,
            draft_steps=req.draft_steps,
            history=req.history,
            use_llm=_llm_ok(),
        )
        return {
            "message": req.message,
            "reply": turn.reply,
            "intent": turn.intent,
            "fault_matches": turn.fault_matches,
            "needs_clarification": turn.needs_clarification,
            "llm_generated": turn.llm_generated,
            "out_of_domain": turn.intent == "out_of_domain",
            **(
                {"suggested_steps": turn.suggested_steps}
                if turn.suggested_steps is not None
                else {}
            ),
            **({"rag_evidence": turn.rag_evidence} if turn.rag_evidence else {}),
            **({"followup_question": turn.followup_question} if turn.followup_question else {}),
            **({"matched_fault": turn.matched_fault} if turn.matched_fault else {}),
            **(
                {"scenario_suggestions": turn.scenario_suggestions}
                if turn.scenario_suggestions
                else {}
            ),
        }

    @app.post("/api/agent/composer")
    def agent_composer(req: ComposeRequest) -> dict:
        """Q3 原子组合器：一句话多故障意图 → 可执行计划 + 逐条溯源（源资产/系统/Agent）。

        run=True 时计划直接走 _run_custom_steps 真实引擎执行（与 /api/run/custom
        同管线），返回 passed/all_passed/assertions/engine_version。
        """
        from ..agent.composer import ComposeError, plan_compose  # noqa: PLC0415

        try:
            plan = plan_compose(asset_model, req.goal, history=req.history or None)
        except ComposeError as e:
            return {"ok": False, "reason": str(e), "plan": None}
        out = {"ok": True, **plan}
        if req.run:
            rep = _run_custom_steps(
                asset_model,
                f"compose:{req.goal[:16]}",
                plan["steps"],
                _app_upstream,  # type: ignore[arg-type]
            )
            _run_counter["n"] += 1
            sink.record_run(
                f"run-{_run_counter['n']:03d}",
                f"compose:{req.goal[:16]}",
                {"passed": rep.get("passed"), "failed": rep.get("failed"), "all_passed": rep.get("all_passed")},
            )
            out["execution"] = {
                "passed": rep.get("passed"),
                "failed": rep.get("failed"),
                "all_passed": rep.get("all_passed"),
                "assertions": len(rep.get("assertions") or []),
                "engine_version": __import__("tcms").__version__,
                "run_id": f"run-{_run_counter['n']:03d}",
            }
        return out

    @app.post("/api/run/custom")
    def run_custom(req: CustomScenarioRequest) -> dict:
        """手动编排的自定义故障场景 → 真实引擎执行（不落盘）。

        与 /api/run/scenario 同构：校验 → 组装 YAML → parse_scenario →
        VirtualClock(virtual) + FaultLedger 执行 → 同构报告。
        """
        # 复用 demo-steps/run-custom 共享执行管线（校验→组装 YAML→真实执行）
        # 注：引擎缺失时该 helper 抛 503（引导文案与 run_scenario 一致）
        rep = _run_custom_steps(
            asset_model,
            req.name or "custom",
            [st.model_dump() for st in req.steps],
            _app_upstream,  # type: ignore[arg-type]
        )
        _run_counter["n"] += 1
        sink.record_run(
            f"run-{_run_counter['n']:03d}",
            req.name or "custom",
            {"passed": rep.get("passed"), "failed": rep.get("failed"), "all_passed": rep.get("all_passed")},
        )
        return {
            "scenario": rep.get("scenario"),
            "steps": rep.get("steps"),
            "assertions": rep.get("assertions"),
            "passed": rep.get("passed"),
            "failed": rep.get("failed"),
            "all_passed": rep.get("all_passed"),
            "engine_version": __import__("tcms").__version__,
            "run_id": f"run-{_run_counter['n']:03d}",
            "custom": True,
        }

    # ---- 前端静态托管（P3）：生产构建 dist/ 挂到根路径 ----
    # 路径解析：PyInstaller 打包(frozen)时静态资源在 sys._MEIPASS/web/dist；
    # 源码运行时在仓库 web/dist。
    import sys as _sys

    if getattr(_sys, "frozen", False):
        _bundle = Path(getattr(_sys, "_MEIPASS", Path(__file__).resolve().parent))
        _web_dist = _bundle / "web" / "dist"
    else:
        _web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if _web_dist.is_dir():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        # 静态资源（/assets/...）
        app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="assets")

        # SPA fallback：非 /api 的未知路径回 index.html（前端路由）
        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            f = _web_dist / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            return FileResponse(_web_dist / "index.html")

    return app


app = create_app()


def main() -> None:
    """启动入口：python -m tcms_ai_platform.server.app / tcms-platform / 打包后的 exe。"""
    import os
    import sys as _sys
    import threading

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    # 延迟自动开浏览器（仅本地非 headless 环境；exe 内也开）
    def _open_browser() -> None:
        import time

        time.sleep(1.6)
        try:
            import webbrowser

            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:  # noqa: BLE001 - 开浏览器失败不影响服务
            pass

    if not os.environ.get("DSH_NO_BROWSER"):
        threading.Thread(target=_open_browser, daemon=True).start()

    if getattr(_sys, "frozen", False):
        # 打包态：直接跑已构建的 app 对象，避免按 import 字符串二次解析
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
    else:
        uvicorn.run("tcms_ai_platform.server.app:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
