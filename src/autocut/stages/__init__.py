"""阶段实现集合。各模块按里程碑逐步补全，未实现的安全跳过。"""

from __future__ import annotations

import logging

from ..pipeline import Pipeline

logger = logging.getLogger("autocut.stages")


def register_all_stages(p: Pipeline) -> None:
    """注册所有已实现阶段；缺失模块记录并跳过。"""
    _try(p, "probe", "autocut.stages.probe", "stage_probe")
    _try(p, "hardsub", "autocut.stages.subtitle", "stage_hardsub")
    _try(p, "subtitle", "autocut.stages.subtitle", "stage_subtitle")
    _try(p, "name_fix", "autocut.stages.name_fix", "stage_name_fix")
    _try(p, "shots", "autocut.stages.subtitle", "stage_shots")
    _try(p, "loudness", "autocut.stages.subtitle", "stage_loudness")
    _try(p, "outline", "autocut.stages.outline", "stage_outline")
    _try(p, "scout", "autocut.stages.scout", "stage_scout")
    _try(p, "frames", "autocut.stages.frames", "stage_frames")
    _try(p, "vision", "autocut.stages.vision", "stage_vision")
    _try(p, "write", "autocut.stages.write", "stage_write")
    _try(p, "review", "autocut.stages.review", "stage_review")
    _try(p, "tts", "autocut.stages.tts_stage", "stage_tts")
    _try(p, "align", "autocut.stages.align", "stage_align")
    _try(p, "render", "autocut.stages.render_stage", "stage_render")
    _try(p, "qc", "autocut.stages.qc", "stage_qc")
    _try(p, "metadata", "autocut.stages.metadata_stage", "stage_metadata")


def _try(p: Pipeline, name: str, module: str, func: str) -> None:
    try:
        import importlib

        mod = importlib.import_module(module)
        p.register(name, getattr(mod, func))
    except (ImportError, AttributeError) as e:
        logger.debug("阶段 %s 未就绪(%s)，跳过注册", name, e)
