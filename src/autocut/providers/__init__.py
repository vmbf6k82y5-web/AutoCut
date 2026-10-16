"""Provider 插件体系：注册表 + entry points 发现 + 内置 provider 注册。

每个环节（subtitle/shots/loudness/hardsub/tts/renderer）都是一个 provider 组。
第三方包通过 Python entry points 注册：
    [project.entry-points."autocut.providers.subtitle"]
    mysub = "mypkg:MySubtitleProvider"
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib import metadata
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import Config
    from ..contracts import (
        HardsubInfo,
        HighEnergyRange,
        Shot,
        Timeline,
    )

logger = logging.getLogger("autocut.providers")

# ---------------------------------------------------------------------------
# 接口定义（文档性质，duck-typing 也接受）
# ---------------------------------------------------------------------------


@runtime_checkable
class SubtitleProvider(Protocol):
    name: str

    def get_subtitles(self, video: Path, workdir: Path, config: Config) -> tuple[Path, dict[str, str] | None]:
        """返回 (srt 路径, 可选 speaker 映射)。"""
        ...


@runtime_checkable
class ShotsProvider(Protocol):
    name: str

    def detect(self, video: Path, workdir: Path, config: Config) -> list[Shot]: ...


@runtime_checkable
class LoudnessProvider(Protocol):
    name: str

    def analyze(self, video: Path, workdir: Path, config: Config) -> list[HighEnergyRange]: ...


@runtime_checkable
class HardsubProvider(Protocol):
    name: str

    def detect(self, video: Path, srt: Path | None, workdir: Path, config: Config) -> HardsubInfo: ...


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    def synth(
        self, text: str, voice: str, emotion: str, workdir: Path, config: Config, seg_id: str
    ) -> tuple[Path, list[dict[str, Any]]]:
        """合成音频，返回 (音频路径, 词级时间戳 [{text, start, end}])。"""
        ...


@runtime_checkable
class RendererProvider(Protocol):
    name: str

    def render(self, timeline: Timeline, assets: dict[str, Any], workdir: Path, config: Config) -> Path: ...


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_GROUP_ENTRY_POINT = {
    "subtitle": "autocut.providers.subtitle",
    "shots": "autocut.providers.shots",
    "loudness": "autocut.providers.loudness",
    "hardsub": "autocut.providers.hardsub",
    "tts": "autocut.providers.tts",
    "renderer": "autocut.providers.renderer",
}

_MODEL_PROTOCOL_EP = "autocut.model_protocols"

#: group -> {name: factory(()->provider)}
_registry: dict[str, dict[str, Callable[[], Any]]] = {g: {} for g in _GROUP_ENTRY_POINT}
_model_protocols: dict[str, Callable[[], Any]] = {}
_entry_points_loaded = False


def register(group: str, name: str, factory: Callable[[], Any]) -> None:
    """注册 provider 工厂（延迟实例化）。"""
    if group not in _registry:
        raise ValueError(f"未知 provider 组: {group}（可选: {list(_registry)}）")
    _registry[group][name] = factory
    logger.debug("注册 provider: %s/%s", group, name)


def register_model_protocol(name: str, factory: Callable[[], Any]) -> None:
    """注册模型协议客户端工厂。"""
    _model_protocols[name] = factory


def _ensure_builtins() -> None:
    """确保内置 provider 已注册（幂等，get/list 前调用）。"""
    from . import builtin, model_clients

    if not _registry.get("subtitle") or not _model_protocols:
        builtin._REGISTERED = False
        model_clients._REGISTERED = False
        register_builtin_providers()
        register_builtin_model_protocols()


def _load_entry_points() -> None:
    """扫描 entry points，加载第三方 provider。"""
    global _entry_points_loaded
    _ensure_builtins()
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    for group, ep_group in _GROUP_ENTRY_POINT.items():
        try:
            eps = metadata.entry_points()
            selected = eps.select(group=ep_group) if hasattr(eps, "select") else eps.get(ep_group, [])
            for ep in selected:
                if ep.name not in _registry[group]:
                    try:
                        obj = ep.load()
                        _registry[group][ep.name] = obj if callable(obj) else (lambda o=obj: o)
                        logger.info("通过 entry point 加载 provider: %s/%s", group, ep.name)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("entry point %s 加载失败: %s", ep.name, e)
        except Exception as e:  # noqa: BLE001
            logger.debug("entry points 扫描失败(%s): %s", ep_group, e)
    try:
        eps = metadata.entry_points()
        selected = eps.select(group=_MODEL_PROTOCOL_EP) if hasattr(eps, "select") else eps.get(_MODEL_PROTOCOL_EP, [])
        for ep in selected:
            if ep.name not in _model_protocols:
                try:
                    _model_protocols[ep.name] = ep.load()
                except Exception as e:  # noqa: BLE001
                    logger.warning("模型协议 entry point %s 加载失败: %s", ep.name, e)
    except Exception as e:  # noqa: BLE001
        logger.debug("模型协议 entry points 扫描失败: %s", e)


def get(group: str, name: str) -> Any:
    """获取 provider 实例。"""
    _load_entry_points()
    factories = _registry.get(group, {})
    if name not in factories:
        available = sorted(factories)
        raise KeyError(f"未注册的 {group} provider: {name!r}（可用: {available}）")
    return factories[name]()


def list_providers(group: str | None = None) -> dict[str, list[str]]:
    """列出所有已注册 provider。"""
    _load_entry_points()
    if group:
        return {group: sorted(_registry.get(group, {}))}
    return {g: sorted(names) for g, names in _registry.items()}


def get_model_protocol(name: str) -> Any:
    _load_entry_points()
    if name not in _model_protocols:
        raise KeyError(f"未注册的模型协议: {name!r}（可用: {sorted(_model_protocols)}）")
    return _model_protocols[name]()


def list_model_protocols() -> list[str]:
    _load_entry_points()
    return sorted(_model_protocols)


def reset_registry() -> None:
    """清空注册表并重跑内置注册（测试用）。"""
    global _entry_points_loaded
    for g in _registry:
        _registry[g].clear()
    _model_protocols.clear()
    _entry_points_loaded = False
    # 同步重置内置模块的注册标志，确保 force 重建生效
    from . import builtin, model_clients

    builtin._REGISTERED = False
    model_clients._REGISTERED = False
    register_builtin_providers()
    register_builtin_model_protocols()


# ---------------------------------------------------------------------------
# 内置 provider 注册（延迟导入，避免重依赖在未使用时加载）
# ---------------------------------------------------------------------------


def register_builtin_providers() -> None:
    """注册所有内置 provider（force 以保证 reset 后重建）。"""
    from .builtin import register_builtin

    register_builtin(force=True)


def register_builtin_model_protocols() -> None:
    from .model_clients import register_protocols

    register_protocols(force=True)
