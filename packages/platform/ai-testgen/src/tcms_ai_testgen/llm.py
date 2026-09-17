"""LLM 调用层：prompt 构造 + 输出解析 + mock 离线实现。

设计要点：
- ``MockLLMClient`` 是确定性假 LLM——不联网、可复现，是 CI/自检/演示的默认；
- ``OpenAICompatClient`` 走 DeepSeek/OpenAI 兼容 chat 接口（需 ``pip install .[llm]``），
  输出仍走同一套结构化解析，保证「真 LLM 结果也可被同一质量管线评估」。
- 解析层用「fence 抽取 + JSON 校验」：LLM 常输出 ```json ... ``` 包裹，
  先剥 fence 再 pydantic 校验，解析失败计数为 failure 而非崩溃。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from tcms_ai_testgen.models import GeneratedCase, GenRequest

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json(text: object) -> Optional[dict[str, Any]]:
    """从 LLM 输出中抽取 JSON 对象：剥 fence -> 找首个 { ... } -> parse。"""
    if not isinstance(text, str) or not text:
        return None
    m = JSON_FENCE_RE.search(text)
    candidate = m.group(1) if m else text
    # 若整段不是 JSON，尝试截取首个 { 到末尾
    start = candidate.find("{")
    if start == -1:
        return None
    try:
        return json.loads(candidate[start:])
    except json.JSONDecodeError:
        # 兜底：截取到最后一个 } 再试一次
        end = candidate.rfind("}")
        if end <= start:
            return None
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None


def parse_cases(payload: object) -> tuple[list[GeneratedCase], int]:
    """把 LLM 返回的 JSON 解析为用例列表。

    返回 (成功用例, 失败条数)。payload 须为 dict 且含 cases/items/tests 之一；
    非法输入整体计为 1 次失败并返回空列表（流水线不抛异常）。
    """
    if not isinstance(payload, dict):
        return [], 1
    raw_list: list[Any] = []
    for key in ("cases", "items", "tests"):
        v = payload.get(key)
        if isinstance(v, list):
            raw_list = v
            break
    ok: list[GeneratedCase] = []
    fail = 0
    for raw in raw_list:
        if not isinstance(raw, dict):
            fail += 1
            continue
        try:
            case = GeneratedCase.model_validate(raw)
        except Exception:
            fail += 1
            continue
        if case.is_valid():
            ok.append(case)
        else:
            fail += 1
    return ok, fail


class LLMClient(ABC):
    """LLM 客户端抽象：输入请求，返回可解析的 JSON 文本。"""

    @abstractmethod
    def generate_cases(self, req: GenRequest) -> str:
        """返回 LLM 原始输出（应为 JSON 或 JSON fence 包裹文本）。"""


class MockLLMClient(LLMClient):
    """确定性 mock：根据需求/hints 拼出固定风格用例，离线可复现。

    v2 设计（docs/decisions.md D1）：
        - mock 的隐藏职能 = **DSL 一致性套件**——生成结果必须可编译、可真实
          执行，从而证明 DSL 对上游可执行语义面完备；此后真 LLM 臂的任何
          失败才能归因于模型而非 DSL。
        - 期望一律由 oracle 派生（见 oracle.py），不手抄、不编造——否则
          PASS 退化为回声 spec 的 tautology。
        - 语义族覆盖：encode_bound（信号边界）+ simulate_inject（信号注入）
          + fault_scenario（10 故障键 × oracle 期望），三族全在真实执行面内。
    """

    #: 语义族轮转（按 i 轮转；其中 fault_scenario 族再按 oracle 键轮转）
    def generate_cases(self, req: GenRequest) -> str:
        from tcms_ai_testgen.oracle import fault_keys

        keys = fault_keys()
        cases: list[dict[str, Any]] = []
        n = req.num_cases
        for i in range(1, n + 1):
            if i % 6 == 0:
                cases.append({"name": f"broken_{i}", "purpose": ""})  # 缺 expected -> 解析失败
                continue
            family = (i - 1) % 4
            if family == 0:  # encode_bound 拒绝
                cases.append(self._encode_bound_case(i, reject=True))
            elif family == 1:  # encode_bound 接受
                cases.append(self._encode_bound_case(i, reject=False))
            elif family == 2:  # simulate_inject（车门故障信号断言）
                cases.append(self._simulate_door_case(i))
            else:  # fault_scenario（按 oracle 键轮转，期望派生）
                key = keys[(i - 1) % len(keys)]
                cases.append(self._fault_case(i, key))
        return json.dumps({"cases": cases}, ensure_ascii=False)

    @staticmethod
    def _encode_bound_case(i: int, reject: bool) -> dict[str, Any]:
        """信号边界族：车速越界拒绝 / 上限接受（P2 已验证的真实语义）。"""
        if reject:
            return {
                "name": f"test_speed_boundary_reject_{i}",
                "purpose": "车速越界应被编码拒绝",
                "preconditions": "DBC 就绪",
                "steps": ["构造车速 200.1", "调用 db.encode_message", "观察是否拒绝"],
                "expected": "编码抛出 EncodeError（200.1 超物理上限 200）",
                "covers": ["1"],
                "tier": "smoke",
                "execution": {
                    "kind": "encode_bound",
                    "setup": [],
                    "expect": [{"op": "expect_encode_error", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.1}}],
                },
            }
        return {
            "name": f"test_speed_boundary_ok_{i}",
            "purpose": "车速物理上限可正常编码",
            "preconditions": "DBC 就绪",
            "steps": ["构造车速 200.0", "调用 db.encode_message", "观察是否成功"],
            "expected": "车速 200.0 编码成功且报文长度 8",
            "covers": ["1"],
            "tier": "smoke",
            "execution": {
                "kind": "encode_bound",
                "setup": [],
                "expect": [{"op": "expect_encode_ok", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.0}}],
            },
        }

    @staticmethod
    def _simulate_door_case(i: int) -> dict[str, Any]:
        """信号注入族：车门故障 → 解码断言（VAL_ 枚举文本）。"""
        return {
            "name": f"test_door_fault_signal_{i}",
            "purpose": "车门故障应解码为 Fault 且禁止发车",
            "preconditions": "simulator 运行中",
            "steps": ["注入 Door2 Fault", "采集 DoorControl", "解码断言"],
            "expected": "Door2State 解码为 'Fault'，AllDoorsClosed=0",
            "covers": ["1"],
            "tier": "safety",
            "execution": {
                "kind": "simulate_inject",
                "setup": [{"op": "set_door_state", "args": {"index": 1, "state": 2}}],
                "expect": [
                    {"op": "expect_signal", "args": {"message": "DoorControl", "signal": "Door2State", "equals": "Fault"}},
                    {"op": "expect_signal", "args": {"message": "DoorControl", "signal": "AllDoorsClosed", "equals": 0}},
                ],
            },
        }

    @staticmethod
    def _fault_case(i: int, key: str) -> dict[str, Any]:
        """故障场景族：10 键 × oracle 期望派生（mock 也不许硬编码期望）。"""
        from tcms_ai_testgen.oracle import describe, lookup

        e = lookup(key)
        assert e is not None, f"oracle 缺 {key}"
        # 信号断言：若 oracle 给了信号，生成一条 expect_signal 断言
        expect_ops: list[dict[str, Any]] = [
            {"op": "expect_action", "args": {"fault": key, "action": e.action}}
        ]
        return {
            "name": f"test_fault_{key}_{i}",
            "purpose": f"{e.name}故障应触发 {e.action} 处置",
            "preconditions": "系统运行于 auto 模式",
            "steps": [f"注入故障 {key}", "查询处置动作", "校验期望"],
            "expected": describe(key),
            "covers": ["1"],
            "tier": "safety",
            "execution": {
                "kind": "fault_scenario",
                "node": "vcu",
                "fault": key,
                "expect": expect_ops,
            },
        }


class OpenAICompatClient(LLMClient):
    """DeepSeek/OpenAI 兼容 chat 客户端（可选，需 openai>=1.0）。"""

    def __init__(
        self,
        model: str = "deepseek-chat",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.3,
        timeout: float = 60.0,
        asset_context: Optional[str] = None,
    ) -> None:
        try:
            from openai import OpenAI  # 延迟导入：离线模式不需要
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("真实 LLM 模式需要 `pip install .[llm]`") from exc
        kwargs: dict[str, Any] = {"api_key": api_key or "EMPTY"}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        #: prompt 资产事实上下文（DBC 枚举/范围 + 故障键），防模型幻觉
        self.asset_context = asset_context

    def generate_cases(self, req: GenRequest) -> str:  # pragma: no cover - 真实网络路径
        from tcms_ai_testgen.prompt import build_system_prompt, build_user_prompt

        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            timeout=self._timeout,
            messages=[
                {"role": "system", "content": build_system_prompt(self.asset_context)},
                {"role": "user", "content": build_user_prompt(req)},
            ],
        )
        return resp.choices[0].message.content or ""

    def complete(self, prompt: str, temperature: float | None = None) -> str:  # pragma: no cover
        """自由补全（反思修正/LLM-judge 用）：单 user 消息，返回文本。"""
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=self._temperature if temperature is None else temperature,
            timeout=self._timeout,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


def build_client(
    *,
    mock: bool = True,
    model: str = "deepseek-chat",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMClient:
    """工厂：默认 mock；mock=False 时走真实兼容接口。"""
    if mock:
        return MockLLMClient()
    return OpenAICompatClient(model=model, base_url=base_url, api_key=api_key)
