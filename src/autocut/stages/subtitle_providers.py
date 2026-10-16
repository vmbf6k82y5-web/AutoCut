"""subtitle provider 实现：embedded / file / openai_asr / local_whisper / auto。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..contracts import SubtitleLine
from ..utils import SRTEntry, find_ffmpeg, parse_srt, run_cmd, write_srt

logger = logging.getLogger("autocut.stages.subtitle_providers")


class EmbeddedSubtitleProvider:
    """抽取内嵌软字幕轨。"""

    name = "embedded"

    def get_subtitles(self, video: Path, workdir: Path, config) -> tuple[Path, dict[str, str] | None]:
        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        out = workdir / "01_subtitle.srt"
        # 抽取第一个字幕轨
        run_cmd([ffmpeg, "-y", "-i", video, "-map", "0:s:0", str(out)], timeout=300)
        if not out.exists() or not parse_srt(out):
            raise RuntimeError("内嵌字幕抽取为空")
        return out, None


class FileSubtitleProvider:
    """用户提供的 SRT 文件（可选 ffsubsync 对齐）。配置 providers.subtitle_file。"""

    name = "file"

    def get_subtitles(self, video: Path, workdir: Path, config) -> tuple[Path, dict[str, str] | None]:
        src = getattr(config.providers, "subtitle_file", "") or getattr(config, "subtitle_file", "")
        src = Path(src) if src else None
        if not src or not Path(src).exists():
            raise FileNotFoundError("file provider 需要有效的字幕文件路径")
        out = workdir / "01_subtitle.srt"
        entries = parse_srt(Path(src))
        write_srt(entries, out)
        # 可选 ffsubsync 对齐
        if getattr(config.providers, "ffsubsync", False):
            try:
                import ffsubsync  # noqa: F401

                aligned = workdir / "01_subtitle_aligned.srt"
                run_cmd(["ffsubsync", str(video), "-i", str(out), "-o", str(aligned)], timeout=600)
                if aligned.exists():
                    out = aligned
            except Exception as e:  # noqa: BLE001
                logger.warning("ffsubsync 对齐失败，用原字幕: %s", e)
        return out, None


class OpenAIASRProvider:
    """OpenAI 兼容转写（whisper-1 等，Groq 兼容）。支持说话人分离字段。"""

    name = "openai_asr"

    def get_subtitles(self, video: Path, workdir: Path, config) -> tuple[Path, dict[str, str] | None]:
        from ..model_client import transcribe_audio

        # 先抽取音轨为 mp3（减小上传体积）
        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        audio = workdir / "asr_input.mp3"
        run_cmd([ffmpeg, "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio)], timeout=600)
        result = transcribe_audio(config, audio, diarize=config.asr.diarization)
        entries = [
            SRTEntry(i + 1, seg["start"], seg["end"], seg["text"])
            for i, seg in enumerate(result["segments"])
        ]
        out = workdir / "01_subtitle.srt"
        write_srt(entries, out)
        speakers = result.get("speakers")  # {speaker_id: label}
        return out, speakers


class LocalWhisperProvider:
    """faster-whisper 本地转写（可选依赖）。"""

    name = "local_whisper"

    def get_subtitles(self, video: Path, workdir: Path, config) -> tuple[Path, dict[str, str] | None]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("local_whisper 需要安装 faster-whisper: uv pip install 'autocut-ai[whisper]'") from e
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(video), word_timestamps=False)
        entries = [SRTEntry(i + 1, s.start, s.end, s.text.strip()) for i, s in enumerate(segments)]
        out = workdir / "01_subtitle.srt"
        write_srt(entries, out)
        return out, None


class AutoSubtitleProvider:
    """auto：依次尝试 embedded → file → openai_asr → local_whisper。"""

    name = "auto"

    def get_subtitles(self, video: Path, workdir: Path, config) -> tuple[Path, dict[str, str] | None]:
        probe = None
        try:
            from ..utils import find_ffprobe
            from .probe import probe_video

            probe = probe_video(video, find_ffprobe(config.general.ffmpeg_path))
        except Exception as e:  # noqa: BLE001
            logger.debug("auto 字幕前置 probe 失败: %s", e)

        attempts: list[tuple[str, object]] = []
        if probe and probe.get("has_embedded_subtitle"):
            attempts.append(("embedded", EmbeddedSubtitleProvider()))
        # file 仅在配置了文件时尝试
        if getattr(config.providers, "subtitle_file", ""):
            attempts.append(("file", FileSubtitleProvider()))
        # openai_asr 仅在配置了 asr 端点时尝试
        if config.asr.base_url or config.asr.model:
            attempts.append(("openai_asr", OpenAIASRProvider()))
        attempts.append(("local_whisper", LocalWhisperProvider()))

        last_err: Exception | None = None
        for name, prov in attempts:
            try:
                logger.info("auto 字幕: 尝试 %s", name)
                return prov.get_subtitles(video, workdir, config)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                logger.warning("auto 字幕: %s 失败(%s)，尝试下一个", name, e)
                last_err = e
        raise RuntimeError(f"auto 字幕全部失败。最后错误: {last_err}")


def srt_to_lines(srt_path: Path, speakers: dict[str, str] | None = None) -> list[SubtitleLine]:
    """读 SRT 为 SubtitleLine 列表。"""
    return [
        SubtitleLine(index=e.index, start=e.start, end=e.end, text=e.text)
        for e in parse_srt(srt_path)
    ]
