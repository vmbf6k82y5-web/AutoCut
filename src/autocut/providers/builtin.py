"""内置 provider 实现（M1 先注册 mock 与占位；真实实现在后续里程碑补全）。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..contracts import HardsubInfo, HighEnergyRange, Shot
from ..utils import SRTEntry, write_srt
from . import register

logger = logging.getLogger("autocut.providers.builtin")

_REGISTERED = False


# ---------------------------------------------------------------------------
# Mock providers（测试用，全部确定性、零外部依赖）
# ---------------------------------------------------------------------------


class MockSubtitleProvider:
    name = "mock"

    def get_subtitles(self, video: Path, workdir: Path, config) -> tuple[Path, dict[str, str] | None]:
        """生成确定性假字幕：每 3 秒一句。"""
        entries: list[SRTEntry] = []
        for i in range(20):
            start = i * 3.0
            entries.append(SRTEntry(i + 1, start, start + 2.5, f"这是第{i + 1}句测试台词"))
        srt = workdir / "01_subtitle.srt"
        write_srt(entries, srt)
        speakers = {"SPEAKER_00": "张三", "SPEAKER_01": "李四"}
        return srt, speakers


class MockShotsProvider:
    name = "mock"

    def detect(self, video: Path, workdir: Path, config) -> list[Shot]:
        """固定 4 秒一个镜头。"""
        return [Shot(index=i, start=i * 4.0, end=(i + 1) * 4.0) for i in range(15)]


class MockLoudnessProvider:
    name = "mock"

    def analyze(self, video: Path, workdir: Path, config) -> list[HighEnergyRange]:
        """固定返回几个高能区间。"""
        return [
            HighEnergyRange(start=10.0, end=14.0, mean_loudness=-18.0, max_loudness=-10.0, score=0.9),
            HighEnergyRange(start=30.0, end=36.0, mean_loudness=-16.0, max_loudness=-8.0, score=1.0),
            HighEnergyRange(start=48.0, end=52.0, mean_loudness=-20.0, max_loudness=-12.0, score=0.7),
        ]


class MockHardsubProvider:
    name = "mock"

    def detect(self, video: Path, srt: Path | None, workdir: Path, config) -> HardsubInfo:
        return HardsubInfo(has_hardsub=False, confidence=0.0)


class MockTTSProvider:
    name = "mock"

    def synth(self, text: str, voice: str, emotion: str, workdir: Path, config, seg_id: str):
        """用 ffmpeg 生成与文本长度成正比的 sine 音频；词级时间戳按字均分。"""
        from ..utils import find_ffmpeg, run_cmd

        duration = max(1.0, len(text) * 0.18)  # 约每字 0.18s
        out = workdir / f"tts_{seg_id}.wav"
        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        run_cmd(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}",
                "-ar",
                "44100",
                str(out),
            ]
        )
        # 词级时间戳（按字均分）
        words = []
        n = len(text)
        for i, ch in enumerate(text):
            words.append({"text": ch, "start": duration * i / n, "end": duration * (i + 1) / n})
        return out, words


class MockRendererProvider:
    name = "mock"

    def render(self, timeline, assets, workdir: Path, config) -> Path:
        """委托给真实 ffmpeg_mp4 渲染器（mock 仅标记）。"""
        from ..render.ffmpeg_mp4 import FFmpegRenderer

        return FFmpegRenderer().render(timeline, assets, workdir, config)


def register_builtin(force: bool = False) -> None:
    """幂等注册所有内置 provider。真实实现模块就绪后替换工厂。"""
    global _REGISTERED
    if _REGISTERED and not force:
        return
    _REGISTERED = True

    def _lazy(module: str, cls: str):
        def factory():
            import importlib

            mod = importlib.import_module(module)
            return getattr(mod, cls)()

        return factory

    # subtitle
    register("subtitle", "mock", MockSubtitleProvider)
    register("subtitle", "embedded", _lazy("autocut.stages.subtitle_providers", "EmbeddedSubtitleProvider"))
    register("subtitle", "file", _lazy("autocut.stages.subtitle_providers", "FileSubtitleProvider"))
    register("subtitle", "openai_asr", _lazy("autocut.stages.subtitle_providers", "OpenAIASRProvider"))
    register("subtitle", "local_whisper", _lazy("autocut.stages.subtitle_providers", "LocalWhisperProvider"))
    register("subtitle", "auto", _lazy("autocut.stages.subtitle_providers", "AutoSubtitleProvider"))
    # shots
    register("shots", "mock", MockShotsProvider)
    register("shots", "pyscenedetect", _lazy("autocut.stages.shot_providers", "PySceneDetectProvider"))
    register("shots", "fixed", _lazy("autocut.stages.shot_providers", "FixedIntervalProvider"))
    # loudness
    register("loudness", "mock", MockLoudnessProvider)
    register("loudness", "ffmpeg_ebur128", _lazy("autocut.stages.loudness_providers", "EBUR128Provider"))
    # hardsub
    register("hardsub", "mock", MockHardsubProvider)
    register("hardsub", "opencv", _lazy("autocut.stages.hardsub_providers", "OpenCVHardsubProvider"))
    register("hardsub", "none", _lazy("autocut.stages.hardsub_providers", "NoHardsubProvider"))
    # tts
    register("tts", "mock", MockTTSProvider)
    register("tts", "edge_tts", _lazy("autocut.stages.tts_providers", "EdgeTTSProvider"))
    register("tts", "openai_tts", _lazy("autocut.stages.tts_providers", "OpenAITTSProvider"))
    register("tts", "custom_http", _lazy("autocut.stages.tts_providers", "CustomHTTPTTSProvider"))
    # renderer
    register("renderer", "mock", MockRendererProvider)
    register("renderer", "ffmpeg_mp4", _lazy("autocut.render.ffmpeg_mp4", "FFmpegRenderer"))
    register("renderer", "jianying_draft", _lazy("autocut.render.jianying_draft", "JianYingDraftRenderer"))


register_builtin()
