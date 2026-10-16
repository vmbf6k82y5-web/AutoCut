"""probe 阶段：ffprobe 探测视频元信息 → 00_probe.json。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..pipeline import StageContext, save_json_artifact
from ..utils import ffprobe_json, find_ffprobe

logger = logging.getLogger("autocut.stages.probe")


def probe_video(video: Path, ffprobe: Path) -> dict:
    data = ffprobe_json(video, ffprobe)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    fps = 0.0
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "path": str(video.resolve()),
        "duration": float(fmt.get("duration", 0.0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": round(fps, 3),
        "video_codec": video_stream.get("codec_name", ""),
        "audio_tracks": len(audio_streams),
        "audio_codecs": [s.get("codec_name", "") for s in audio_streams],
        "subtitle_tracks": [
            {
                "index": s.get("index"),
                "codec": s.get("codec_name", ""),
                "language": (s.get("tags") or {}).get("language", ""),
            }
            for s in sub_streams
        ],
        "has_embedded_subtitle": len(sub_streams) > 0,
        "size_bytes": int(fmt.get("size", 0)),
    }


def stage_probe(ctx: StageContext) -> dict:
    ffprobe = find_ffprobe(ctx.config.general.ffmpeg_path)
    info = probe_video(ctx.video, ffprobe)
    save_json_artifact(ctx.workdir, "00_probe.json", info)
    ctx.artifacts["probe"] = info
    logger.info(
        "probe: %.1fs %dx%d@%.2f 音轨%d 字幕轨%d",
        info["duration"], info["width"], info["height"], info["fps"],
        info["audio_tracks"], len(info["subtitle_tracks"]),
    )
    return {"duration": info["duration"], "resolution": f"{info['width']}x{info['height']}"}
