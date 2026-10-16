"""render 阶段：调用 renderer provider 出成片。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..contracts import HardsubInfo, Timeline
from ..pipeline import StageContext, load_json_artifact
from ..providers import get

logger = logging.getLogger("autocut.stages.render")


def stage_render(ctx: StageContext) -> dict:
    timeline: Timeline | None = ctx.artifacts.get("timeline")
    if timeline is None:
        data = load_json_artifact(ctx.workdir, "09_timeline_aligned.json") or \
               load_json_artifact(ctx.workdir, "07b_timeline_reviewed.json") or \
               load_json_artifact(ctx.workdir, "07_timeline.json")
        if not data:
            raise RuntimeError("render 需要 timeline")
        timeline = Timeline.model_validate(data)

    hardsub = ctx.artifacts.get("hardsub")
    if hardsub is None:
        hd = load_json_artifact(ctx.workdir, "00b_hardsub.json")
        hardsub = HardsubInfo.model_validate(hd) if hd else HardsubInfo()

    word_ts = ctx.artifacts.get("word_timestamps")
    if word_ts is None:
        wt = ctx.workdir / "08_word_timestamps.json"
        word_ts = json.loads(wt.read_text(encoding="utf-8")) if wt.exists() else {}

    # BGM 曲库
    bgm_tracks = _scan_bgm(ctx.config.audio.bgm_library)

    assets = {
        "sources": ctx.sources,
        "hardsub": hardsub,
        "word_timestamps": word_ts,
        "bgm_tracks": bgm_tracks,
    }
    renderer = get("renderer", ctx.config.providers.renderer)
    final = renderer.render(timeline, assets, ctx.workdir, ctx.config)
    ctx.artifacts["final_video"] = final
    logger.info("render: 成片 %s", final)
    return {"final": str(final)}


def _scan_bgm(bgm_library: Path | None) -> list[dict]:
    """扫描配乐库：按子文件夹分情绪。"""
    if not bgm_library or not Path(bgm_library).exists():
        return []
    tracks: list[dict] = []
    lib = Path(bgm_library)
    exts = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    for sub in sorted(lib.iterdir()):
        if sub.is_dir():
            mood = sub.name
            for f in sorted(sub.iterdir()):
                if f.suffix.lower() in exts:
                    tracks.append({"path": f, "mood": mood})
        elif sub.suffix.lower() in exts:
            tracks.append({"path": sub, "mood": "通用"})
    return tracks
