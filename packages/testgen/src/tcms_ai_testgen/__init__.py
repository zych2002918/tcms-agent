"""tcms-ai-testgen：LLM 测试用例生成器（TCMS CAN 版）。

从 DBC 信号表 / 安全需求 / 场景模板生成 pytest 用例，并对生成质量做
确定性量化评估（通过率 / 覆盖率 / LLM-as-judge 质量分）。

核心链路全部支持 **mock 离线模式**（不调外部 API），保证可复现、CI 可跑；
真实 LLM 通过 ``llm`` extra 启用（DeepSeek/OpenAI 兼容接口）。
"""

from tcms_ai_testgen._version import __version__
from tcms_ai_testgen.models import GeneratedCase, GenReport, GenRequest

__all__ = ["GeneratedCase", "GenReport", "GenRequest", "__version__"]
