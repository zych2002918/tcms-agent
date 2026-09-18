"""LLM 决策后端（AgentBackend 实现，OpenAI 兼容）。

让 Agent 的「规划/选场景/策略」由真 LLM 决策（而非确定性规则）。设计：
- 无 key / 请求失败 / 解析失败 → **自动落回 MockAgentBackend**（诚实降级，
  保证离线与断网也可复现——Harness 红线）
- key 来源（环境变量，任意一个）：DASH_API_KEY / DEEPSEEK_API_KEY /
  OPENAI_API_KEY；base_url 可用 LLM_BASE_URL 覆盖（默认阿里百炼兼容端点）
- 请求超时与重试受控（测试/演示不卡死）

用法：
    backend = LLMAgentBackend(fallback=MockAgentBackend())
    harness = AgentHarness(..., backend=backend)
"""

from __future__ import annotations

import json
import os
import time

import httpx

from .harness import AgentBackend, MockAgentBackend, Plan
from .tasks import TaskDef

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "deepseek-v3.2"
TIMEOUT_S = 25
MAX_RETRY = 1


def _is_env_proxy_error(e: BaseException) -> bool:
    """判断异常是否来自"环境代理配置本身不可用"。

    真实案例（本机实测）：`NO_PROXY=localhost,127.0.0.1,::1,[::1]` 里的 `[::1]`
    会让 httpx 在**建 URL 阶段**就抛 `InvalidURL: Invalid port: ':1]'`——
    于是每一次请求都失败，上层诚实降级成"LLM 不可用"，
    用户看到的是一次莫名其妙的规则臂运行（模型明明配了、key 也在）。
    这不是本程序能修的环境问题，但**不该让它把功能整体废掉**。
    """
    if isinstance(e, httpx.InvalidURL):
        return True
    s = f"{type(e).__name__}: {e}"
    return "InvalidURL" in s or "Invalid port" in s


def _request(
    method: str,
    url: str,
    *,
    json: dict | None = None,
    headers: dict | None = None,
    timeout: float = TIMEOUT_S,
) -> tuple[httpx.Response | None, str]:
    """发一次 HTTP 请求；环境代理配置坏掉时**绕过代理重试一次**。

    返回 `(response, note)`：`note` 非空表示"为了跑通做过什么"——
    这种事必须能一路传到界面上（否则用户只会看到"LLM 不可用"，无从下手）。
    `response` 为 None 表示彻底失败（异常已吞掉，由调用方如实降级）。
    """
    try:
        return httpx.request(method, url, json=json, headers=headers, timeout=timeout), ""
    except Exception as e:  # noqa: BLE001
        if not _is_env_proxy_error(e):
            return None, f"{type(e).__name__}: {e}"
        note = f"环境代理配置不可用（{e}），已绕过代理重试"
        try:
            with httpx.Client(trust_env=False, timeout=timeout) as c:
                return c.request(method, url, json=json, headers=headers), note
        except Exception as e2:  # noqa: BLE001
            return None, f"{note}；绕过代理后仍失败：{type(e2).__name__}: {e2}"


def _api_key() -> str | None:
    """Key 解析优先级：环境变量 → 本地 settings(~/.tcms-ai-platform/settings.json,
    前端引导页写入) → DSH 凭据文件(~/.dsh/.credentials.yaml refs.ALIYUN_API_KEY)。
    Key 永不写入仓库/日志；测试可用 DSH_CREDENTIALS_FILE 指向临时文件隔离。"""
    for k in ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        v = os.environ.get(k)
        if v:
            return v
    try:
        from ..core.settings import llm_api_key  # 本地设置层(前端可写)

        v = llm_api_key()
        if v:
            return v
    except Exception:  # noqa: BLE001 - 设置层故障不阻塞
        pass
    try:
        import yaml

        cred = os.environ.get("DSH_CREDENTIALS_FILE") or os.path.expanduser("~/.dsh/.credentials.yaml")
        if os.path.isfile(cred):
            d = yaml.safe_load(open(cred, encoding="utf-8"))
            refs = d.get("refs", {}) or {}
            key = refs.get("ALIYUN_API_KEY")
            if isinstance(key, str) and key.strip():
                return key.strip()
    except Exception:  # noqa: BLE001 - 读凭据失败不阻塞（落回 mock）
        pass
    return None


def llm_available() -> bool:
    return bool(_api_key())


def resolve_llm_config(
    base_url: str | None = None, model: str | None = None
) -> tuple[str, str]:
    """统一解析 base_url / model：显式参数 → 环境变量 → 本地 settings → 默认。

    与 LLMAgentBackend.__init__ 的解析链保持一致（单一事实源），供
    模型列表探测等只读操作复用，避免两处漂移。
    """
    try:
        from ..core.settings import llm_config

        _cfg = llm_config()
        _s_base = (_cfg.get("base_url") or "").strip()
        _s_model = (_cfg.get("model") or "").strip()
    except Exception:  # noqa: BLE001
        _s_base, _s_model = "", ""
    base = base_url or os.environ.get("LLM_BASE_URL") or _s_base or DEFAULT_BASE
    mdl = model or os.environ.get("LLM_MODEL") or _s_model or DEFAULT_MODEL
    return base, mdl


def fetch_models(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = TIMEOUT_S,
) -> list[dict]:
    """调 OpenAI 兼容 {base_url}/models 拉取可用模型列表（只读探测）。

    返回 [{id, owned_by?, created?}, ...]；调用失败抛异常由上层转 502/诚实文案。
    key 解析：显式参数 → _api_key()（env → settings → DSH 凭据）。
    base_url 解析与 LLMAgentBackend 一致（显式 → env → settings → 默认）。
    """
    base, _ = resolve_llm_config(base_url, None)
    key = api_key or _api_key()
    if not key:
        raise RuntimeError("未配置 API key：无法拉取模型列表")
    url = base.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r, note = _request("GET", url, headers=headers, timeout=timeout)
    if r is None:
        raise RuntimeError(f"模型列表请求失败：{note}")
    if r.status_code != 200:
        raise RuntimeError(f"模型列表请求失败 HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    items = data.get("data") or []
    out = []
    for it in items:
        mid = it.get("id")
        if mid:
            out.append(
                {
                    "id": str(mid),
                    "owned_by": it.get("owned_by"),
                    "created": it.get("created"),
                }
            )
    if not out:
        raise RuntimeError("端点未返回任何模型（可能不支持 GET /models，可改手动输入）")
    return _sort_models(out)


def _sort_models(models: list[dict]) -> list[dict]:
    """对模型列表做体验排序：对话/推理模型在前，嵌入/重排等非对话类垫底。

    大厂兼容端点（如阿里百炼）会混入 text-embedding / text-rerank / 第三方
    长尾，全部平铺会让「选模型」无从下手。规则：
      1. id/owned_by 含 embedding|rerank|text-vec → 归非对话类（垫底）
      2. 其余按是否含推理关键词(reasoner/thinking/r1/max 等)优先在前
      3. 稳定排序（同组保持端点返回顺序，不破坏厂商版本排列）
    """
    def _is_embedding(m: dict) -> bool:
        s = f"{m.get('id','')} {m.get('owned_by','')}".lower()
        return any(k in s for k in ("embedding", "rerank", "text-vec", "text-vector"))

    def _is_reasoner(m: dict) -> bool:
        s = m.get("id", "").lower()
        return any(k in s for k in ("reasoner", "thinking", "-r1", "max", "pro", "turbo"))

    chat = [m for m in models if not _is_embedding(m)]
    non_chat = [m for m in models if _is_embedding(m)]
    chat.sort(key=_is_reasoner, reverse=True)  # 稳定：推理类前移，组内保序
    return chat + non_chat


def ping_model(
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = TIMEOUT_S,
) -> dict:
    """连通性自检：真发一次最小对话请求，把**真实**结果回给调用方。

    为什么要有它：`llm_available()` 只回答"有没有配 key"，不回答"这个模型真的能用吗"。
    两者差别很大——本机就踩过：key 配了、模型名也写了，但环境代理配置坏掉，
    每次请求在建 URL 阶段就失败，于是平台**一直静默走规则臂**，
    而界面上仍显示"LLM 已配置"。用户没有任何办法发现这件事。

    返回 {ok, model, base_url, latency_ms, error, note}：
    - `error` 是**服务端原样的错误**（含 HTTP 状态与响应片段），不美化；
    - `note` 说明"为了跑通做过什么"（例如绕过坏代理）。
    """
    base, mdl = resolve_llm_config(base_url, model)
    key = api_key or _api_key()
    if not key:
        return {
            "ok": False,
            "model": mdl,
            "base_url": base,
            "latency_ms": None,
            "error": "未配置 API key（环境变量 / 本机设置 / DSH 凭据文件都没有）",
            "note": "",
        }
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": mdl,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()
    r, note = _request("POST", url, json=payload, headers=headers, timeout=timeout)
    ms = int((time.time() - t0) * 1000)
    if r is None:
        return {"ok": False, "model": mdl, "base_url": base, "latency_ms": ms, "error": note, "note": note}
    if r.status_code != 200:
        return {
            "ok": False,
            "model": mdl,
            "base_url": base,
            "latency_ms": ms,
            "error": f"HTTP {r.status_code}: {r.text[:300]}",
            "note": note,
        }
    return {"ok": True, "model": mdl, "base_url": base, "latency_ms": ms, "error": None, "note": note}


def _embedding_model_id() -> str:
    """embedding 模型 id：env EMBEDDING_MODEL → 本机设置 llm.embedding_model。"""
    v = (os.environ.get("EMBEDDING_MODEL") or "").strip()
    if v:
        return v
    try:
        from ..core.settings import llm_config

        v = (llm_config().get("embedding_model") or "").strip()
    except Exception:  # noqa: BLE001 - 设置层故障不阻塞
        v = ""
    return v


def make_kb_embedder():
    """构建知识库向量 embedder（P0-2，诚实降级）：

    - 默认（TCMS_EMBEDDER 未设/非 api）→ HashedEmbedder：与旧版逐字节一致，
      零网络、离线全绿；
    - TCMS_EMBEDDER=api 且已有 key → ApiEmbedder(base_url 与对话同源,
      model 用 EMBEDDING_MODEL/设置，缺省由 /models 自动探测)；无 key 或
      探测/请求失败 → 自动落回 HashedEmbedder。

    base_url/key 解析与对话 LLM 同一链（env → 设置 → 默认），单一事实源。
    """
    from ..knowledge.vector import ApiEmbedder, HashedEmbedder

    if (os.environ.get("TCMS_EMBEDDER") or "").strip().lower() not in ("api", "1", "on", "true"):
        return HashedEmbedder()
    base, _ = resolve_llm_config()
    key = _api_key()
    if not key or not base:
        return HashedEmbedder()
    mdl = _embedding_model_id()
    return ApiEmbedder(base_url=base, api_key=key, model=mdl or None, fallback=HashedEmbedder())


class LLMAgentBackend(AgentBackend):
    """OpenAI 兼容 LLM 决策后端（失败自动落回 Mock）。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        fallback: AgentBackend | None = None,
    ) -> None:
        # 解析 base_url/model：显式参数 → 环境变量 → 本地 settings(前端引导页) → 默认
        try:
            from ..core.settings import llm_config

            _cfg = llm_config()
            _s_base = (_cfg.get("base_url") or "").strip()
            _s_model = (_cfg.get("model") or "").strip()
        except Exception:  # noqa: BLE001
            _s_base, _s_model = "", ""
        self.base_url = (
            base_url
            or os.environ.get("LLM_BASE_URL")
            or _s_base
            or DEFAULT_BASE
        )
        self.model = model or os.environ.get("LLM_MODEL") or _s_model or DEFAULT_MODEL
        self.fallback = fallback or MockAgentBackend()
        self.used_llm = False  # 本次是否真的用了 LLM（供 trace/自证）
        #: 最近一次请求的"环境说明"（如：绕过坏代理）。空串表示一切正常。
        #: 它会被拼进决策策略里，因此会出现在白盒轨迹与界面上——
        #: 用户不该为了搞清"为什么走了规则臂"去翻服务端日志。
        self.last_note = ""

    # ---- 工具 ----

    def _chat(self, system: str, user: str) -> str | None:
        key = _api_key()
        if not key:
            return None
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 600,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        last_err: Exception | None = None
        for _ in range(MAX_RETRY + 1):
            r, note = _request("POST", url, json=payload, headers=headers)
            if note:
                self.last_note = note  # 一路传到轨迹/界面上，别让用户猜
                print(f"[llm-backend] {note}")
            if r is None:
                last_err = RuntimeError(note)
                continue
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"]
            last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            time.sleep(0.5)
        if last_err:
            print(f"[llm-backend] chat failed, fallback to mock: {last_err}")
        return None

    def _chat_tools(
        self,
        system: str,
        user: str,
        tools: list[dict],
        extra_messages: list[dict] | None = None,
    ) -> tuple[str | None, list[dict]]:
        """OpenAI 兼容 function-calling 单轮调用（P1-a：受约束工具选择）。

        tools = [{type:"function", function:{name, description, parameters}}]。
        返回 (content, tool_calls)；tool_calls=[{id,name,arguments(str)}]。
        无 key/请求失败/解析失败 → (None, [])（上层诚实降级，绝不硬编）。
        """
        key = _api_key()
        if not key:
            return None, []
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if extra_messages:
            messages.extend(extra_messages)
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.2,
            "max_tokens": 900,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        r, note = _request("POST", url, json=payload, headers=headers)
        if note:
            self.last_note = note
            print(f"[llm-backend] {note}")
        if r is None or r.status_code != 200:
            print(
                f"[llm-backend] chat_tools "
                f"{'请求失败' if r is None else f'HTTP {r.status_code}'}: {note or (r.text[:200] if r else '')}"
            )
            return None, []
        try:
            msg = r.json()["choices"][0]["message"] or {}
        except Exception as e:  # noqa: BLE001 - 非 JSON 响应=诚实降级
            print(f"[llm-backend] chat_tools 响应解析失败: {e}")
            return None, []
        content = msg.get("content")
        calls: list[dict] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            calls.append(
                {
                    "id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": fn.get("arguments") or "{}",
                }
            )
        return (str(content) if content is not None else None), calls

    @staticmethod
    def _parse_scenario_choice(text: str, scenarios: list[dict]) -> str | None:
        """从 LLM 文本提取所选场景文件名（容忍 JSON/散句）。"""
        # 先试 JSON {scenario: ...}
        try:
            start = text.find("{")
            if start >= 0:
                obj = json.loads(text[start : text.rfind("}") + 1])
                cand = obj.get("scenario") or obj.get("file")
                if cand and any(s["file"] == cand for s in scenarios):
                    return cand
        except Exception:  # noqa: BLE001
            pass
        # 散句：找出现在文本里的场景文件名
        for s in scenarios:
            if s["file"] in text:
                return s["file"]
        return None

    # ---- AgentBackend ----

    def plan(self, task: TaskDef, evidence: list[dict], scenarios: list[dict]) -> Plan:
        # 候选 = 覆盖目标故障的场景
        covering = [s for s in scenarios if task.target_fault in s.get("fault_keys", [])]
        if not covering:
            return self.fallback.plan(task, evidence, scenarios)
        # 无 key → 直接 mock
        if not _api_key():
            return self.fallback.plan(task, evidence, scenarios)

        evidence_txt = "\n".join(
            f"- {h.get('doc_id')}: {h.get('text', '')[:200]}" for h in evidence[:5]
        ) or "（无）"
        scen_txt = "\n".join(f"- {s['file']}（覆盖 {s.get('fault_keys')}，涉及 {s.get('nodes')}）" for s in covering)
        system = (
            "你是 TCMS 列车控制软件测试的规划 Agent。你的任务是：为给定测试目标，"
            "从候选场景中选一个最合适的真实执行场景，并给出一句话策略。"
            "只输出 JSON：{\"scenario\": \"<文件名>\", \"strategy\": \"<一句话理由>\"}。"
            "scenario 必须是候选列表里的文件名。"
        )
        user = (
            f"目标：验证故障 {task.target_fault} 必须触发处置 {task.expected_action}。\n\n"
            f"候选场景：\n{scen_txt}\n\n检索到的证据：\n{evidence_txt}"
        )
        text = self._chat(system, user)
        if text is None:
            # 诚实降级
            p = self.fallback.plan(task, evidence, scenarios)
            return Plan(
                task_id=p.task_id,
                fault=p.fault,
                expected_action=p.expected_action,
                chosen_scenario=p.chosen_scenario,
                strategy=p.strategy + " [LLM 不可用，已落回确定性]",
            )
        chosen = self._parse_scenario_choice(text, covering)
        if chosen is None:
            p = self.fallback.plan(task, evidence, scenarios)
            return Plan(
                task_id=p.task_id,
                fault=p.fault,
                expected_action=p.expected_action,
                chosen_scenario=p.chosen_scenario,
                strategy=f"LLM 输出未解析({text[:80]})，落回确定性",
            )
        self.used_llm = True
        # 提取 strategy(尽力)
        strategy = "LLM 决策"
        try:
            start = text.find("{")
            obj = json.loads(text[start : text.rfind("}") + 1])
            strategy = obj.get("strategy", "LLM 决策")
        except Exception:  # noqa: BLE001
            pass
        suffix = f"（{self.last_note}）" if self.last_note else ""
        return Plan(
            task_id=task.task_id,
            fault=task.target_fault,
            expected_action=task.expected_action,
            chosen_scenario=chosen,
            strategy=f"[LLM {self.model}] {strategy}{suffix}",
        )
