"""核心层：L1 资产模型 + 加载器（真实上游资产 → 列车视角 schema）。"""

from .loader import AssetLoadError, load_asset_model, load_default
from .models import (
    AssetModel,
    DeviceDef,
    FaultDef,
    FunctionDef,
    MessageDef,
    RequirementDef,
    ScenarioDef,
    ScenarioStep,
    SignalDef,
)

__all__ = [
    "AssetLoadError",
    "load_asset_model",
    "load_default",
    "AssetModel",
    "DeviceDef",
    "FaultDef",
    "FunctionDef",
    "MessageDef",
    "RequirementDef",
    "ScenarioDef",
    "ScenarioStep",
    "SignalDef",
]
