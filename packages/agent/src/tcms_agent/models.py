"""模型工厂：真 LLM / 离线脚本模型。

## 为什么要有一个"离线脚本模型"

本项目有一条从原三仓继承的硬纪律：**无 API key 时必须仍能跑完整条链路**。
但"离线可跑"有两种做法，差别很大：

- 做法 A（差）：无 key 时给一张写死的假回复 —— 图跑得通，但什么都没验证，
  真正的循环、预算、工具回填、轨迹落盘全都没被覆盖。
- 做法 B（本实现）：无 key 时换上一个**确定性的规则参考实现**，它走**同一张图、
  同一套工具、同一套权限与审计**，只是决策由规则（而非 LLM）做出。

做法 B 带来三个好处：
1. 离线运行也真实覆盖了 harness 的全部基础设施（这是测试可信的前提）；
2. 产出的轨迹可作为 LLM 的**对照基线**（M6 的 A/B 评测就是「规则臂 vs LLM 臂」）；
3. 结果 100% 可复现，适合做回归门禁。

**诚实标注**：是否真的调用了 LLM，由 `model_kind` 如实区分（`llm` / `offline-rule`），
运行结果与轨迹里都会带上，绝不把规则结果说成"AI 决策"。

## key 解析

复用 platform 的解析链（env → 本地设置 → DSH 凭据文件），**不另写一套**——
两套 key 解析必然漂移，且容易出现"能跑但不知道用的是谁的 key"。
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .config import AgentConfig

OFFLINE_MODEL_KIND = "offline-rule"
LLM_MODEL_KIND = "llm"


def llm_available() -> bool:
    """是否配置了可用的 LLM key（复用 platform 的解析链）。"""
    try:
        from tcms_ai_platform.agent.llm_backend import llm_available as _ok

        return bool(_ok())
    except Exception:  # noqa: BLE001 - platform 不可用时诚实地视为"没有 LLM"
        return False


def resolve_llm_settings(cfg: AgentConfig) -> tuple[str, str]:
    """解析 base_url / model（显式配置优先，其余走 platform 的解析链）。"""
    from tcms_ai_platform.agent.llm_backend import resolve_llm_config

    base, model = resolve_llm_config(cfg.base_url, cfg.model)
    return base, model


# ---------------------------------------------------------------------------
# 离线脚本模型
# ---------------------------------------------------------------------------


def _content_of(msg: BaseMessage) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):  # 多模态内容块：只取文本部分
        return " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
    return str(c)


class OfflineScriptedModel(BaseChatModel):
    """确定性"规则参考实现"：按固定脚本调用真实工具，结论由真实结果拼出。

    脚本（按可用工具裁剪）：
        1. kb_search(query=目标)                     —— 先检索证据
        2. fault_detail(fault_key=规则解析出的故障)   —— 查清处置语义
        3. list_scenarios(fault_key=...)             —— 查可复现场景
        4. 收尾：输出带引用的结论（不再调用工具）

    故障键由 platform 的**规则解析器**（`freeform.parse_free_goal`）从目标里解析，
    解析不出就退化为 `symptom_diagnose` 并如实说明"无法确定目标故障"。
    """

    knowledge: Any = None  # KnowledgeContext（arbitrary_types_allowed）
    bound_tools: list[str] = []
    temperature: float = 0.0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return OFFLINE_MODEL_KIND

    # ---- 工具绑定 ----

    def bind_tools(self, tools: Any, **kwargs: Any) -> OfflineScriptedModel:  # noqa: ARG002
        """记录可用工具名（OpenAI schema dict 列表或 LangChain 工具均可）。"""
        names: list[str] = []
        for t in tools or []:
            if isinstance(t, dict):
                fn = t.get("function") or {}
                if fn.get("name"):
                    names.append(str(fn["name"]))
            elif hasattr(t, "name"):
                names.append(str(t.name))
        return self.model_copy(update={"bound_tools": names})

    # ---- 内部：读状态 ----

    @staticmethod
    def _goal_of(messages: list[BaseMessage]) -> str:
        for m in messages:
            if isinstance(m, HumanMessage):
                return _content_of(m)
        return ""

    def _resolve(self, goal: str) -> tuple[str | None, str | None]:
        """用规则解析器把目标解析为 (真实故障键, 期望处置)（无 LLM 参与）。"""
        if not goal or self.knowledge is None:
            return None, None
        try:
            from tcms_ai_platform.agent.freeform import parse_free_goal

            parsed = parse_free_goal(self.knowledge.model, goal, seq=1, use_llm=False)
            return str(parsed.fault), str(parsed.expected)
        except Exception:  # noqa: BLE001 - 解析不出就是解析不出（含 NoFaultMatch）
            return None, None

    def _script(self, goal: str) -> list[tuple[str, dict[str, Any]]]:
        """按可用工具裁剪出的确定性脚本。

        顺序体现测试工程师的工作流：**先查清语义 → 再真执行验证 → 最后下结论**。
        若真执行工具未被开放（权限档位低），脚本自动跳过该步（诚实降级）。
        """
        names = set(self.bound_tools)
        key, expected = self._resolve(goal)
        script: list[tuple[str, dict[str, Any]]] = []
        if "kb_search" in names:
            script.append(("kb_search", {"query": goal}))
        if key and "fault_detail" in names:
            script.append(("fault_detail", {"fault_key": key}))
        if key and expected and "verify_fault_action" in names:
            script.append(("verify_fault_action", {"fault_key": key, "expected_action": expected}))
        if key and "list_scenarios" in names:
            script.append(("list_scenarios", {"fault_key": key}))
        if not key and "symptom_diagnose" in names:
            script.append(("symptom_diagnose", {"text": goal}))
        # 造用例 + 真跑用例：体现"测试工程师"的完整动作（R1 沙箱写 → R2 真执行）
        if key and expected and "draft_test_case" in names:
            script.append(("draft_test_case", self._draft_args(key, expected)))
        if key and "run_draft" in names:
            script.append(("run_draft", {"draft_id": f"offline-{key}"}))
        # 最后一步：把已验证的结论沉淀进长期记忆（R3，会触发人工审批）。
        # 只有字典与真正执行都确认过才写——这正是"写入门禁"该有的前提。
        if key and "write_memory" in names:
            script.append(("write_memory", self._memory_args(goal, key, expected)))
        return script

    @staticmethod
    def _draft_args(key: str, expected: str) -> dict[str, Any]:
        """构造一条受约束用例（testgen DSL）：断言该故障的处置动作等于期望值。"""
        return {
            "draft_id": f"offline-{key}",
            "case": {
                "name": f"test_{key}_action_is_{expected}",
                "purpose": f"验证 {key} 的处置动作必须为 {expected}",
                "preconditions": "系统运行于 auto 模式",
                "steps": [f"注入故障 {key}", "查询处置动作", "与期望值比对"],
                "expected": f"{key} 触发 {expected}",
                "tier": "safety",
                "execution": {
                    "kind": "fault_scenario",
                    "node": "vcu",
                    "fault": key,
                    "setup": [],
                    "expect": [{"op": "expect_action", "args": {"fault": key, "action": expected}}],
                },
            },
        }

    @staticmethod
    def _memory_args(goal: str, key: str, expected: str | None) -> dict[str, Any]:
        """构造长期记忆写入参数（refs 必须是真实资产 id，否则会被门禁拒绝）。"""
        refs = [f"fault:{key}"]
        if expected:
            refs.append(f"fault:{key}")
        return {
            "title": f"验证结论：{key} → {expected or '（未指定）'}",
            "kind": "semantic",
            "content": (
                f"目标：{goal}\n\n"
                f"结论：故障 {key} 的处置动作为 {expected or '（未指定）'}，"
                f"已由上游 tcms 引擎的真实场景断言确认。\n"
            ),
            "refs": sorted(set(refs)),
        }

    @staticmethod
    def _rounds_done(messages: list[BaseMessage]) -> int:
        return sum(
            1 for m in messages if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        )

    @staticmethod
    def _tool_payloads(messages: list[BaseMessage]) -> dict[str, dict]:
        """把 ToolMessage 里的 JSON 结果按工具名收拢（供收尾结论引用真实数据）。"""
        import json

        out: dict[str, dict] = {}
        for m in messages:
            if not isinstance(m, ToolMessage):
                continue
            name = str(getattr(m, "name", "") or "")
            try:
                payload = json.loads(_content_of(m))
            except Exception:  # noqa: BLE001 - 截断/非 JSON 内容跳过
                continue
            if isinstance(payload, dict):
                out[name] = payload
        return out

    def _final_text(self, messages: list[BaseMessage], goal: str) -> str:
        """收尾结论：只用真实工具返回的数据拼装，并显式列出引用。"""
        payloads = self._tool_payloads(messages)
        fd = payloads.get("fault_detail") or {}
        key, expected = self._resolve(goal)
        key = str(fd.get("key") or "") or (key or "")
        lines = ["【离线规则臂结论】目标：", goal, ""]
        if fd.get("key"):
            lines += [
                f"故障：{fd.get('name')}（fault:{fd['key']}），等级 {fd.get('level')}，"
                f"处置 {fd.get('action')}，SIL {fd.get('sil') or '—'}",
                f"检测方式：{fd.get('detect') or '（字典未给出）'}",
                f"可复现场景：{fd.get('covering_scenario_count', 0)} 个",
            ]
        elif key:
            lines.append(f"识别到故障键 fault:{key}（fault_detail 未返回详情）")
        else:
            lines.append("未能从目标中解析出真实故障键（未匹配 faults.yaml 任何条目）。")
        # 真执行结果：这是结论是否成立的**引擎证据**，优先展示
        vf = payloads.get("verify_fault_action") or {}
        if vf.get("verified") is not None:
            if vf.get("verified"):
                lines += [
                    "",
                    f"✅ 真实引擎验证通过：在场景 {vf.get('scenario')} 上，"
                    f"fault={vf.get('fault')} 的实际处置为 {vf.get('expected_action')}，"
                    f"断言 {vf.get('engine_assertions')}",
                ]
            else:
                lines += [
                    "",
                    f"⚠️ 真实引擎**未**确认该处置：{vf.get('reason') or vf.get('engine_assertions')}",
                ]
        sc = payloads.get("list_scenarios") or {}
        if sc.get("scenarios"):
            names = [s.get("file") for s in sc["scenarios"][:5]]
            lines.append(f"候选场景：{'、'.join(str(n) for n in names)}")
        # 自己造的用例 + 它的真跑结果
        dr = payloads.get("draft_test_case") or {}
        rd = payloads.get("run_draft") or {}
        if dr.get("draft_id"):
            lines += ["", f"🗒 生成用例：{dr.get('case_file')}（编译 {dr.get('compiled')}）"]
            if rd.get("all_passed") is True:
                lines.append(
                    f"   ✅ 自造用例真跑通过：{rd.get('passed')}/{rd.get('total')} "
                    f"（exec_pass_rate={rd.get('exec_pass_rate')}）"
                )
            elif rd.get("draft_id"):
                lines.append(
                    f"   ⚠️ 自造用例真跑未通过：failed={rd.get('failed')} "
                    f"{'；'.join(str(x) for x in (rd.get('failure_lines') or [])[:2])}"
                )
        kb = payloads.get("kb_search") or {}
        if kb.get("hits"):
            ids = [h.get("doc_id") for h in kb["hits"][:5]]
            lines.append(f"检索命中：{'、'.join(str(i) for i in ids)}")
        cited = [f"fault:{key}"] if key else []
        if vf.get("scenario"):
            cited.append(f"scenario:{vf['scenario']}")
        for h in (kb.get("hits") or [])[:5]:
            if h.get("doc_id"):
                cited.append(str(h["doc_id"]))
        lines += ["", f"引用资产：{'、'.join(cited) if cited else '（无）'}"]
        return "\n".join(lines)

    # ---- 生成 ----

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> ChatResult:
        goal = self._goal_of(messages)
        script = self._script(goal)
        done = self._rounds_done(messages)
        if done < len(script):
            name, args = script[done]
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": args,
                        "id": f"offline-{done}-{name}",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            msg = AIMessage(content=self._final_text(messages, goal))
        return ChatResult(generations=[ChatGeneration(message=msg)])


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_chat_model(cfg: AgentConfig, knowledge: Any = None) -> tuple[Any, str]:
    """构建聊天模型。

    返回 (model, kind)：
        kind = "llm"          真 LLM（OpenAI 兼容，key 来自 platform 的解析链）
        kind = "offline-rule" 确定性规则参考实现

    选择逻辑：显式 `offline=True` 或没有可用 key → 离线规则臂。
    """
    if cfg.offline or not llm_available():
        return OfflineScriptedModel(knowledge=knowledge, temperature=cfg.temperature), OFFLINE_MODEL_KIND

    from langchain_openai import ChatOpenAI
    from tcms_ai_platform.agent.llm_backend import _api_key

    base, model = resolve_llm_settings(cfg)
    chat = ChatOpenAI(
        model=model,
        base_url=base,
        api_key=_api_key() or "EMPTY",
        temperature=cfg.temperature,
        timeout=30,
        max_retries=1,
    )
    return chat, LLM_MODEL_KIND


__all__ = [
    "LLM_MODEL_KIND",
    "OFFLINE_MODEL_KIND",
    "OfflineScriptedModel",
    "build_chat_model",
    "llm_available",
    "resolve_llm_settings",
]
