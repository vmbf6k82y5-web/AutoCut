"""subtitle / hardsub / shots / loudness 阶段包装。"""

from __future__ import annotations

import logging

from ..contracts import HardsubInfo, HighEnergyRange, Shot
from ..pipeline import StageContext, save_json_artifact
from ..providers import get

logger = logging.getLogger("autocut.stages")


def stage_hardsub(ctx: StageContext) -> dict:
    prov = get("hardsub", ctx.config.providers.hardsub)
    # 此时字幕可能还没有；opencv provider 内部容错
    srt = ctx.workdir / "01_subtitle.srt"
    info: HardsubInfo = prov.detect(ctx.video, srt if srt.exists() else None, ctx.workdir, ctx.config)
    save_json_artifact(ctx.workdir, "00b_hardsub.json", info)
    ctx.artifacts["hardsub"] = info
    logger.info("硬字幕: has=%s confidence=%.2f", info.has_hardsub, info.confidence)
    return {"has_hardsub": info.has_hardsub}


def stage_subtitle(ctx: StageContext) -> dict:
    prov = get("subtitle", ctx.config.providers.subtitle)
    srt, speakers = prov.get_subtitles(ctx.video, ctx.workdir, ctx.config)
    ctx.artifacts["subtitle_srt"] = srt
    if speakers:
        save_json_artifact(ctx.workdir, "01b_speakers.json", speakers)
        ctx.artifacts["speakers"] = speakers
    from ..utils import parse_srt

    n = len(parse_srt(srt))
    logger.info("字幕: %s（%d 句）", srt.name, n)
    return {"srt": str(srt), "lines": n}


def stage_shots(ctx: StageContext) -> dict:
    prov = get("shots", ctx.config.providers.shots)
    shots: list[Shot] = prov.detect(ctx.video, ctx.workdir, ctx.config)
    save_json_artifact(ctx.workdir, "02_shots.json", [s.model_dump() for s in shots])
    ctx.artifacts["shots"] = shots
    logger.info("镜头: %d 个", len(shots))
    return {"shots": len(shots)}


def stage_loudness(ctx: StageContext) -> dict:
    prov = get("loudness", ctx.config.providers.loudness)
    ranges: list[HighEnergyRange] = prov.analyze(ctx.video, ctx.workdir, ctx.config)
    save_json_artifact(ctx.workdir, "03_loudness.json", [r.model_dump() for r in ranges])
    ctx.artifacts["loudness"] = ranges
    logger.info("高能区间: %d 个", len(ranges))
    return {"high_energy_ranges": len(ranges)}
