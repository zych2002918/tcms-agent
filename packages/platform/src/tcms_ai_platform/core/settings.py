"""用户设置层 —— 外部可配置接口(本地持久化,永不进 git)。

让「新手 / 现场人员」无需改代码、无需碰环境变量即可配置:
- LLM / API:provider / base_url / model / api_key(key 只写本机文件,绝不入库)
- 资产源:自定义实际资产目录(用户自己的 tcms-can-test:可加故障/场景/用例)
- 其他:端口 / 新手引导完成标记

存储位置:
    ~/.tcms-ai-platform/settings.json     (Windows: C:\\Users\\<name>\\.tcms-ai-platform\\settings.json)
该目录/文件在 .gitignore 之外(在用户主目录,天然不入库)。

设计纪律:
- 线程安全(启动/运行期读写都经锁)
- 读失败/文件损坏 → 返回默认(不影响启动,诚实降级)
- key 等敏感字段只在内存/本机文件,绝不打日志、绝不进任何响应(响应只回 has_key: bool)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


# 设置目录解析（每次调用现取：TCMS_AI_HOME 可被测试/运行期覆盖）
def _dir() -> Path:
    return Path(os.environ.get("TCMS_AI_HOME") or Path.home() / ".tcms-ai-platform")


def _file() -> Path:
    return _dir() / "settings.json"


_LOCK = threading.RLock()  # 可重入：save 内部调 load 不死锁

# 默认设置(合并时用)
DEFAULTS: dict = {
    "llm": {"provider": "", "base_url": "", "model": "", "api_key": ""},
    "asset_dir": "",  # 用户自定义资产目录(空=内置快照/自动解析)
    "port": 8000,
    "onboarding_done": False,  # 新手引导是否完成
    "theme": "",  # 前端主题偏好(dark/light；空=跟随系统)。仅前端读取，后端透传持久化
}

# 常见 provider 预设(仅展示提示;实际连通性由 base_url/model/key 决定)
PROVIDER_PRESETS = {
    "aliyun": {
        "label": "阿里云百炼(DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "deepseek-v3.2",
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI 兼容",
        "base_url": "",
        "model": "",
    },
}


def load() -> dict:
    """读取设置(与默认合并;文件缺失/损坏返回默认)。"""
    with _LOCK:
        out = json.loads(json.dumps(DEFAULTS))  # 深拷贝默认
        try:
            if _file().is_file():
                raw = json.loads(_file().read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    _deep_merge(out, raw)
        except Exception:  # noqa: BLE001 - 读失败不阻塞
            pass
        return out


def save(patch: dict) -> dict:
    """合并保存设置(只写给定字段,保留其余)。返回保存后的完整设置。"""
    with _LOCK:
        cur = load()
        _deep_merge(cur, patch)
        try:
            _dir().mkdir(parents=True, exist_ok=True)
            _file().write_text(
                json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001 - 写失败如实上报
            raise RuntimeError(f"设置保存失败: {e}") from e
        return cur


def get(key: str, default=None):
    """读单个键。"""
    return load().get(key, default)


def _deep_merge(base: dict, patch: dict) -> None:
    """把 patch 逐字段并入 base(嵌套 dict 递归)。"""
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ---- 对外便捷读取(启动时用,单次读取不做热缓存) ----


def llm_config() -> dict:
    """LLM 配置(仅内部使用;key 绝不外泄)。"""
    s = load()
    return dict(s.get("llm") or {})


def llm_api_key() -> str | None:
    """读取本地 settings 里的 key(env/凭据文件之后兜底)。"""
    k = llm_config().get("api_key") or ""
    return k.strip() or None


def asset_dir() -> str | None:
    """用户自定义资产目录(空 = 未设置)。"""
    v = load().get("asset_dir") or ""
    return v.strip() or None


def public_view() -> dict:
    """对外(前端可读)的非敏感视图:绝不包含 api_key。"""
    s = load()
    llm = s.get("llm") or {}
    return {
        "dir": str(_dir()),
        "llm": {
            "provider": llm.get("provider", ""),
            "base_url": llm.get("base_url", ""),
            "model": llm.get("model", ""),
            "has_key": bool((llm.get("api_key") or "").strip()),
        },
        "asset_dir": s.get("asset_dir", ""),
        "port": s.get("port", 8000),
        "onboarding_done": bool(s.get("onboarding_done", False)),
        "theme": s.get("theme", "") or "",
    }
