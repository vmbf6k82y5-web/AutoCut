"""多集合集：逐集跑到 outline，链式汇总成全剧大纲，writer 写一篇长解说（多源 segment）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Config
from .contracts import Outline, OutlineNode
from .pipeline import Pipeline, StageContext, save_json_artifact
from .runner import _make_workdir

logger = logging.getLogger("autocut.compilation")


def run_compilation(directory: Path, videos: list[Path], config: Config, *,
                    profile_name: str | None, series_dir: Path) -> Path:
    """合集模式：汇总各集 outline → 全剧大纲 → 一篇长解说（segment 带 source）。"""
    # 1. 逐集跑到 outline（只跑前置阶段）
    outlines: list[tuple[Path, Outline]] = []
    for v in videos:
        workdir = _make_workdir(v, config)
        ctx = StageContext(v, workdir, config, profile_name=profile_name, series_dir=series_dir)
        pipeline = Pipeline()
        from .stages import register_all_stages

        register_all_stages(pipeline)
        # 只跑到 outline
        pipeline.run(ctx, only_stages=["probe", "hardsub", "subtitle", "name_fix", "shots", "loudness", "outline"])
        data = ctx.artifacts.get("outline")
        if data is None:
            of = workdir / "03b_outline.json"
            data = Outline.model_validate(json.loads(of.read_text(encoding="utf-8"))) if of.exists() else Outline()
        outlines.append((v, data))

    # 2. 链式汇总成全剧大纲（用 outline 角色）
    combined = _merge_outlines(outlines, config, profile_name)

    # 3. 构建多源 timeline（每集取高潮段 + narration）
    timeline = _build_compilation_timeline(outlines, combined, config)
    comp_workdir = _make_workdir(Path(directory.name + "_合集"), config)
    comp_workdir.mkdir(parents=True, exist_ok=True)
    save_json_artifact(comp_workdir, "07_timeline.json", timeline)

    # 4. 多源源表 + 渲染
    sources = {f"ep{i}": v for i, (v, _) in enumerate(outlines)}
    sources["main"] = videos[0]
    ctx = StageContext(videos[0], comp_workdir, config, profile_name=profile_name)
    ctx.sources = sources
    ctx.artifacts["timeline"] = timeline
    # tts + align + render
    from .stages.align import stage_align
    from .stages.render_stage import stage_render
    from .stages.tts_stage import stage_tts

    stage_tts(ctx)
    stage_align(ctx)
    final = stage_render(ctx)
    logger.info("合集成片: %s", final.get("final"))
    return Path(final["final"])


def _merge_outlines(outlines: list[tuple[Path, Outline]], config: Config, profile_name) -> Outline:
    """链式汇总各集大纲为全剧大纲。"""
    all_nodes: list[OutlineNode] = []
    loglines = []
    for i, (v, o) in enumerate(outlines):
        loglines.append(f"第{i + 1}集({v.stem}): {o.logline}")
        for n in o.nodes:
            all_nodes.append(OutlineNode(
                id=f"ep{i}_{n.id}", summary=f"[第{i + 1}集] {n.summary}",
                function=n.function, time_start=n.time_start, time_end=n.time_end, importance=n.importance,
            ))
    logline = " | ".join(loglines)
    # 可选：用 outline 角色精炼；失败则用拼接
    return Outline(logline=logline, characters=[], nodes=all_nodes)


def _build_compilation_timeline(outlines, combined, config: Config):
    """为合集构建跨集 timeline：每集高潮段 + narration 串场。"""
    from .contracts import Segment, Timeline

    segments: list[Segment] = []
    sid = 0
    for i, (_v, o) in enumerate(outlines):
        source = f"ep{i}"
        # 每集取最重要节点的一个高光 + 一段 narration
        key_nodes = sorted(o.nodes, key=lambda n: n.importance, reverse=True)[:1]
        for n in key_nodes:
            dur = min(6.0, n.time_end - n.time_start)
            sid += 1
            segments.append(Segment(
                id=f"seg{sid}", source=source, type="narration", outline_node_id=f"ep{i}_{n.id}",
                text=f"第{i + 1}集：{n.summary}", emotion="紧张" if n.function == "高潮" else "平叙",
                src_in=n.time_start, src_out=n.time_start + max(2.0, dur),
            ))
    return Timeline(title=combined.logline[:30] or "合集解说", style=config.video.style,
                    total_target_seconds=config.video.target_seconds, segments=segments)
