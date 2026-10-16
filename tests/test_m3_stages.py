"""M3 测试：probe、hardsub、subtitle、shots、loudness。"""

from __future__ import annotations

import pytest

from autocut.stages.hardsub_providers import NoHardsubProvider, OpenCVHardsubProvider
from autocut.stages.loudness_providers import EBUR128Provider
from autocut.stages.probe import probe_video
from autocut.stages.shot_providers import FixedIntervalProvider, PySceneDetectProvider
from autocut.stages.subtitle_providers import EmbeddedSubtitleProvider, FileSubtitleProvider
from autocut.utils import parse_srt


class TestProbe:
    def test_probe_basic(self, assets, ffmpeg_dir):
        info = probe_video(assets / "test_60s.mp4", ffmpeg_dir / "ffprobe")
        assert info["duration"] == pytest.approx(60.0, abs=1.0)
        assert info["width"] == 1280 and info["height"] == 720
        assert info["audio_tracks"] >= 1
        assert info["fps"] == pytest.approx(30.0, abs=0.5)

    def test_probe_embedded_subtitle(self, assets, ffmpeg_dir):
        info = probe_video(assets / "test_embedded_sub.mp4", ffmpeg_dir / "ffprobe")
        assert info["has_embedded_subtitle"] is True
        assert len(info["subtitle_tracks"]) >= 1

    def test_probe_silent(self, assets, ffmpeg_dir):
        info = probe_video(assets / "test_silent.mp4", ffmpeg_dir / "ffprobe")
        assert info["audio_tracks"] == 0


class TestSubtitleProviders:
    def test_embedded(self, assets, tmp_path, mock_config):
        srt, speakers = EmbeddedSubtitleProvider().get_subtitles(
            assets / "test_embedded_sub.mp4", tmp_path, mock_config
        )
        entries = parse_srt(srt)
        assert len(entries) >= 10
        assert speakers is None  # embedded 无说话人

    def test_file(self, assets, tmp_path, mock_config, monkeypatch):
        # 通过 providers.subtitle_file 注入
        from autocut.config import ProvidersConfig

        mock_config.providers = ProvidersConfig(subtitle="file")
        object.__setattr__(mock_config.providers, "subtitle_file", str(assets / "test.srt"))
        srt, _ = FileSubtitleProvider().get_subtitles(assets / "test_60s.mp4", tmp_path, mock_config)
        entries = parse_srt(srt)
        assert len(entries) == 20
        assert "测试台词" in entries[0].text

    def test_mock_subtitle(self, tmp_path, mock_config):
        from autocut.providers import get

        srt, speakers = get("subtitle", "mock").get_subtitles(tmp_path / "v.mp4", tmp_path, mock_config)
        assert len(parse_srt(srt)) == 20
        assert speakers == {"SPEAKER_00": "张三", "SPEAKER_01": "李四"}


class TestShotProviders:
    def test_fixed(self, assets, tmp_path, mock_config):
        shots = FixedIntervalProvider().detect(assets / "test_60s.mp4", tmp_path, mock_config)
        # 60s / 4s = 15 个镜头
        assert len(shots) == 15
        assert shots[0].start == 0.0
        assert abs(shots[-1].end - 60.0) < 0.5

    def test_pyscenedetect_on_scene_changes(self, assets, tmp_path, mock_config):
        """镜头突变视频应检出多个镜头。"""
        shots = PySceneDetectProvider().detect(assets / "test_scenes.mp4", tmp_path, mock_config)
        # 4 段颜色突变，至少检出 3 个镜头
        assert len(shots) >= 3
        # 时间有序且覆盖
        assert shots[0].start < shots[-1].end

    def test_pyscenedetect_fallback_on_static(self, assets, tmp_path, mock_config):
        """纯色/静态视频检不出镜头时回退为整段。"""
        shots = PySceneDetectProvider().detect(assets / "test_60s.mp4", tmp_path, mock_config)
        assert len(shots) >= 1


class TestLoudness:
    def test_ebur128_runs(self, assets, tmp_path, mock_config):
        ranges = EBUR128Provider().analyze(assets / "test_60s.mp4", tmp_path, mock_config)
        # sine 恒定响度，可能检不出高能区间；只要结构正确即可
        assert isinstance(ranges, list)

    def test_mock_loudness(self, tmp_path, mock_config):
        from autocut.providers import get

        ranges = get("loudness", "mock").analyze(tmp_path / "v.mp4", tmp_path, mock_config)
        assert len(ranges) == 3
        assert all(0 <= r.score <= 1 for r in ranges)


class TestHardsub:
    def test_none_provider(self, assets, tmp_path, mock_config):
        info = NoHardsubProvider().detect(assets / "test_hardsub.mp4", None, tmp_path, mock_config)
        assert info.has_hardsub is False

    def test_opencv_detects_burned_sub(self, assets, tmp_path, mock_config):
        """烧录字幕的视频应被检出硬字幕。"""
        info = OpenCVHardsubProvider().detect(
            assets / "test_hardsub.mp4", assets / "test.srt", tmp_path, mock_config
        )
        assert info.has_hardsub is True
        assert info.confidence > 0.5

    def test_opencv_clean_video(self, assets, tmp_path, mock_config):
        """无硬字幕的纯色视频不应误报。"""
        info = OpenCVHardsubProvider().detect(
            assets / "test_scenes.mp4", None, tmp_path, mock_config
        )
        assert info.has_hardsub is False


class TestStageFunctions:
    """阶段包装函数端到端（真实 ffmpeg + mock 配置）。"""

    def test_probe_and_shots_stage(self, assets, tmp_path, mock_config):
        from autocut.pipeline import StageContext
        from autocut.stages.probe import stage_probe
        from autocut.stages.subtitle import stage_loudness, stage_shots

        mock_config.providers.shots = "fixed"
        mock_config.providers.loudness = "mock"
        ctx = StageContext(assets / "test_60s.mp4", tmp_path / "w", mock_config)
        ctx.workdir.mkdir(parents=True)
        stage_probe(ctx)
        assert (ctx.workdir / "00_probe.json").exists()
        out2 = stage_shots(ctx)
        assert out2["shots"] == 15
        out3 = stage_loudness(ctx)
        assert out3["high_energy_ranges"] == 3
