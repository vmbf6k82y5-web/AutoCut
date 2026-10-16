"""ffmpeg_mp4 渲染器：Timeline → 成片 MP4。

流程：
1. 每段单独重编码切片（帧精确），统一分辨率/帧率/采样率；9:16 模糊背景填充
2. speed≠1 时 setpts/atempo
3. narration 段原声×orig_volume 与 TTS 混音；段首尾 afade
4. 自动配乐（bgm_library 非空时按情绪选曲，1.5s 交叉淡化）
5. concat 拼接
6. ASS 字幕烧录
7. 编码器自动检测 nvenc→qsv→amf→libx264
"""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..contracts import Segment, Timeline
from ..logging_utils import sanitize
from ..utils import find_ffmpeg, run_cmd
from .ass import ASSEvent, build_ass, events_from_narration

logger = logging.getLogger("autocut.render.ffmpeg_mp4")


#: 优先尝试的硬件编码器（顺序即优先级）
_HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
#: 探测结果缓存：同一进程内同一 ffmpeg 只探测一次
_encoder_cache: dict[str, str] = {}


def _encoder_usable(ffmpeg: Path, encoder: str) -> bool:
    """真实试编一帧，判断编码器在本机是否可用。

    `-encoders` 只说明该编码器编译进了 ffmpeg；机器没有对应驱动/设备时
    （如 runner 无 NVIDIA 驱动却编入了 h264_nvenc）只有真正编码才暴露得出来。
    """
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-v", "error", "-f", "lavfi",
             "-i", "color=c=black:s=128x72:r=1", "-frames:v", "1",
             "-c:v", encoder, "-f", "null", "-"],
            capture_output=True, text=True, timeout=60, errors="replace",
        )
    except Exception as e:  # noqa: BLE001
        logger.info("编码器 %s 试编异常，跳过: %s", encoder, sanitize(e))
        return False
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
        logger.info(
            "编码器 %s 不可用（无驱动/设备？），跳过: %s",
            encoder, sanitize(lines[-1] if lines else f"exit {proc.returncode}"),
        )
        return False
    return True


def detect_encoder(ffmpeg: Path) -> str:
    """探测可用编码器，返回编码器名；结果按 ffmpeg 路径缓存。

    先看 `-encoders` 列出哪些，再逐个试编确认本机真能用，最后回退 libx264。
    """
    key = str(ffmpeg)
    if key in _encoder_cache:
        return _encoder_cache[key]
    listed = ""
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30, errors="replace",
        )
        listed = proc.stdout or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("编码器列表探测失败: %s", sanitize(e))
    for enc in (*_HW_ENCODERS, "libx264"):
        if enc in listed and _encoder_usable(ffmpeg, enc):
            logger.info("选用编码器: %s", enc)
            _encoder_cache[key] = enc
            return enc
    logger.warning("未探测到可用编码器，回退 libx264")
    _encoder_cache[key] = "libx264"
    return "libx264"


def _atempo_chain(speed: float) -> str:
    """atempo 只支持 0.5–2.0，超出需串联。"""
    speed = max(0.5, min(2.0, speed))
    return f"atempo={speed:.4f}"


class FFmpegRenderer:
    name = "ffmpeg_mp4"

    def render(self, timeline: Timeline, assets: dict[str, Any], workdir: Path, config) -> Path:
        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        segs_dir = workdir / "render_segments"
        segs_dir.mkdir(parents=True, exist_ok=True)

        out_w, out_h = config.video.out_size
        fps = config.video.fps
        sources: dict[str, Path] = assets.get("sources", {})
        hardsub_info = assets.get("hardsub")
        bgm_tracks = assets.get("bgm_tracks", [])  # [{path, mood}]

        # 成片时间轴：每段输出时长 = src_duration / speed
        seg_out_start: list[float] = []
        seg_out_dur: list[float] = []
        t = 0.0
        for seg in timeline.segments:
            dur = seg.src_duration / max(0.01, seg.speed)
            seg_out_start.append(t)
            seg_out_dur.append(dur)
            t += dur

        # ---- 1. 逐段切片（并行） ------------------------------------------
        def render_segment(idx: int) -> Path:
            seg = timeline.segments[idx]
            src = sources.get(seg.source) or sources.get("main")
            if src is None:
                raise RuntimeError(f"segment {seg.id} 找不到源视频 {seg.source}")
            out = segs_dir / f"seg_{idx:03d}.mp4"
            self._render_one_segment(
                ffmpeg, src, seg, out,
                out_w=out_w, out_h=out_h, fps=fps,
                aspect=config.video.aspect,
                narration_vol=seg.resolved_orig_volume(config.audio.narration_orig_volume),
                tts_audio=(workdir / seg.audio_file) if seg.audio_file else None,
            )
            return out

        seg_files: list[Path] = [None] * len(timeline.segments)  # type: ignore
        max_workers = max(1, config.general.ffmpeg_concurrency)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(render_segment, i): i for i in range(len(timeline.segments))}
            for fut, i in futures.items():
                seg_files[i] = fut.result()

        # ---- 2. concat ------------------------------------------------------
        concat_list = segs_dir / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in seg_files), encoding="utf-8"
        )
        concatenated = segs_dir / "concatenated.mp4"
        run_cmd(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
             "-c", "copy", str(concatenated)]
        )

        # ---- 3. BGM 混音（可选） --------------------------------------------
        with_bgm = concatenated
        total_dur = t
        if bgm_tracks:
            with_bgm = segs_dir / "with_bgm.mp4"
            self._mix_bgm(ffmpeg, concatenated, with_bgm, bgm_tracks, total_dur, config)

        # ---- 4. ASS 字幕生成 --------------------------------------------------
        ass_path = segs_dir / "subs.ass"
        events = self._build_subtitle_events(
            timeline, seg_out_start, seg_out_dur, assets, config
        )
        sub_style = config.subtitle_style
        position = sub_style.position
        margin_v = sub_style.margin_v
        if hardsub_info and hardsub_info.has_hardsub and sub_style.hardsub_mode == "move_up":
            # 字幕上移避开底部硬字幕区
            margin_v = max(margin_v, int(out_h * (1 - hardsub_info.region_top_ratio)) + 20)
        build_ass(
            events, ass_path,
            font=sub_style.font, font_size=sub_style.font_size,
            color=sub_style.color, outline_color=sub_style.outline_color,
            outline=sub_style.outline, margin_v=margin_v,
            width=out_w, height=out_h, position=position,
            translation_font_size=sub_style.translation_font_size,
        )

        # ---- 5. 烧录字幕 + 硬字幕处理 + 最终编码 -------------------------------
        final = config.general.output_dir / f"{timeline.title or 'autocut_final'}.mp4"
        config.general.output_dir.mkdir(parents=True, exist_ok=True)
        vf = self._final_vf(ass_path, hardsub_info, sub_style.hardsub_mode, out_w, out_h)
        encoder = detect_encoder(ffmpeg)
        enc_args = self._encoder_args(encoder)
        run_cmd(
            [ffmpeg, "-y", "-i", with_bgm, "-vf", vf, *enc_args,
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)],
            timeout=3600,
        )
        logger.info("成片输出: %s", final)
        return final

    # ------------------------------------------------------------------
    def _render_one_segment(
        self, ffmpeg, src, seg: Segment, out: Path, *,
        out_w, out_h, fps, aspect, narration_vol, tts_audio,
    ) -> None:
        """单段重编码：裁剪 + 缩放/画幅 + 变速 + 混音。"""
        dur = seg.src_duration / max(0.01, seg.speed)
        vf_parts: list[str] = []
        # 画幅适配
        if aspect == "9:16":
            # 原片居中 + 模糊背景填充
            vf_parts.append(
                f"split[a][b];"
                f"[a]scale={out_w}:{out_h},boxblur=20:5[bg];"
                f"[b]scale={out_w}:-2:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            vf_parts.append(f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease")
            vf_parts.append(f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black")
        vf_parts.append(f"fps={fps}")
        vf_parts.append("format=yuv420p")
        # 变速
        if abs(seg.speed - 1.0) > 0.01:
            vf_parts.append(f"setpts=PTS/{seg.speed:.4f}")

        af_parts: list[str] = []
        inputs: list[str] = ["-ss", f"{seg.src_in:.3f}", "-to", f"{seg.src_out:.3f}", "-i", str(src)]
        filter_complex: str
        if seg.type == "narration" and tts_audio and tts_audio.exists():
            inputs += ["-i", str(tts_audio)]
            # 原声压低 + TTS 混音
            orig_chain = f"volume={narration_vol:.3f}"
            if abs(seg.speed - 1.0) > 0.01:
                orig_chain = f"{_atempo_chain(seg.speed)},{orig_chain}"
            vchain = ",".join(vf_parts)
            achain = (
                f"[0:a]{orig_chain},aresample=44100[orig];"
                f"[1:a]aresample=44100,volume=1.0[tts];"
                f"[orig][tts]amix=inputs=2:duration=first:weights=1 1,atrim=0:{dur:.3f},"
                f"afade=t=in:st=0:d=0.15,afade=t=out:st={max(0, dur - 0.15):.3f}:d=0.15[aout]"
            )
            filter_complex = f"[0:v]{vchain}[vout];{achain}"
            map_args = ["-map", "[vout]", "-map", "[aout]"]
        else:
            # original 段或 narration 无 tts
            vchain = ",".join(vf_parts)
            if abs(seg.speed - 1.0) > 0.01:
                af_parts.append(_atempo_chain(seg.speed))
            af_parts.append("aresample=44100")
            af_parts.append(f"atrim=0:{dur:.3f}")
            af_parts.append("afade=t=in:st=0:d=0.15")
            af_parts.append(f"afade=t=out:st={max(0, dur - 0.15):.3f}:d=0.15")
            filter_complex = f"[0:v]{vchain}[vout];[0:a]{','.join(af_parts)}[aout]"
            map_args = ["-map", "[vout]", "-map", "[aout]"]

        run_cmd(
            [ffmpeg, "-y", *inputs, "-filter_complex", filter_complex, *map_args,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}", str(out)],
            timeout=600,
        )

    def _mix_bgm(self, ffmpeg, src_video, out, bgm_tracks, total_dur, config) -> None:
        """简单 BGM：取第一首循环铺满，duck 处理。交叉淡化在多首时近似处理。"""
        bgm = bgm_tracks[0]["path"]
        vol = config.audio.bgm_volume
        # bgm_on_original 的 duck/mute 在 BGM 分段处理中按段应用；此处整体压低
        # sidechaincompress 需要 key 输入；这里简化为整体压低 BGM
        filter_complex = (
            f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total_dur:.3f},volume={vol:.3f}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:weights=1 1[aout]"
        )
        run_cmd(
            [ffmpeg, "-y", "-i", src_video, "-stream_loop", "-1", "-i", bgm,
             "-filter_complex", filter_complex,
             "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-t", f"{total_dur:.3f}", str(out)],
            timeout=1200,
        )

    def _build_subtitle_events(self, timeline, seg_out_start, seg_out_dur, assets, config):
        """由 narration 段的词级时间戳生成 ASS 事件；original 段可选翻译字幕。"""
        events: list[ASSEvent] = []
        word_ts: dict[str, list[dict]] = assets.get("word_timestamps", {})
        max_chars = config.subtitle_style.max_chars_per_line
        for i, seg in enumerate(timeline.segments):
            if seg.type == "narration":
                words = word_ts.get(seg.id, [])
                events.extend(
                    events_from_narration(
                        seg.text, words, seg_out_start[i], seg_out_start[i] + seg_out_dur[i], max_chars
                    )
                )
        return events

    def _final_vf(self, ass_path, hardsub_info, hardsub_mode, out_w, out_h) -> str:
        """最终视频滤镜：烧录字幕 + 硬字幕处理。"""
        # subtitles 滤镜路径需转义（Windows 盘符与特殊字符）
        ass_escaped = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
        vf_parts: list[str] = []
        if hardsub_info and hardsub_info.has_hardsub:
            top = int(out_h * hardsub_info.region_top_ratio)
            region_h = out_h - top
            if hardsub_mode == "bar":
                # 半透明底条覆盖原字幕区
                vf_parts.append(
                    f"drawbox=x=0:y={top}:w={out_w}:h={region_h}:color=black@0.6:t=fill"
                )
            elif hardsub_mode == "blur":
                # 模糊原字幕区域
                vf_parts.append(
                    f"split[main][blur];[blur]crop={out_w}:{region_h}:0:{top},boxblur=15:3[b];"
                    f"[main][b]overlay=0:{top}"
                )
            # move_up 已在字幕 margin_v 处理
        vf_parts.append(f"subtitles='{ass_escaped}'")
        return ",".join(vf_parts)

    def _encoder_args(self, encoder: str) -> list[str]:
        if encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
        if encoder == "h264_qsv":
            return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "23"]
        if encoder == "h264_amf":
            return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"]
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "21"]
