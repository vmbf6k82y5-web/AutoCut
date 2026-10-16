"""tts 阶段：为每个 narration 段合成配音 → 08_tts/。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..contracts import Timeline
from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from .tts_providers import synth_segment

logger = logging.getLogger("autocut.stages.tts")


def stage_tts(ctx: StageContext) -> dict:
    timeline: Timeline | None = ctx.artifacts.get("timeline")
    if timeline is None:
        for name in ("07b_timeline_reviewed.json", "07_timeline.json"):
            data = load_json_artifact(ctx.workdir, name)
            if data:
                timeline = Timeline.model_validate(data)
                break
    if timeline is None:
        raise RuntimeError("tts 阶段需要 timeline（07/07b）")

    tts_dir = ctx.workdir / "08_tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    voice = ctx.config.tts.voice
    provider = ctx.config.tts.provider or ctx.config.providers.tts

    narration_segs = [s for s in timeline.segments if s.type == "narration"]
    word_ts: dict[str, list[dict]] = {}

    def synth_one(seg) -> tuple[str, Path, list[dict], float]:
        audio, words, dur = synth_segment(
            seg.text, voice, seg.emotion, tts_dir, ctx.config, seg.id, provider
        )
        return seg.id, audio, words, dur

    # 并行合成（TTS 是 IO 密集）
    with ThreadPoolExecutor(max_workers=max(1, ctx.config.general.ffmpeg_concurrency)) as ex:
        results = list(ex.map(synth_one, narration_segs))

    for seg_id, audio, words, dur in results:
        word_ts[seg_id] = words
        # 回写 segment 的 tts_duration 与 audio_file（相对 workdir）
        for seg in timeline.segments:
            if seg.id == seg_id:
                seg.tts_duration = dur
                seg.audio_file = str(audio.relative_to(ctx.workdir))

    save_json_artifact(ctx.workdir, "08_tts_index.json", {
        "files": {sid: str(a.relative_to(ctx.workdir)) for sid, a, _, _ in results},
        "durations": {sid: d for sid, _, _, d in results},
    })
    # 词级时间戳单独存（align 后用）
    import json

    (ctx.workdir / "08_word_timestamps.json").write_text(
        json.dumps(word_ts, ensure_ascii=False), encoding="utf-8"
    )
    ctx.artifacts["word_timestamps"] = word_ts
    ctx.artifacts["timeline"] = timeline
    logger.info("tts: 合成 %d 段（provider %s, 音色 %s）", len(results), provider, voice)
    return {"synthesized": len(results)}
