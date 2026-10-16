"""M5 测试：tts（读音字典、情绪、停顿）+ align（原创度约束）。"""

from __future__ import annotations

from pathlib import Path

from autocut.contracts import Segment, Shot, Timeline
from autocut.pipeline import StageContext, save_json_artifact
from autocut.stages.align import _check_originality, stage_align
from autocut.stages.tts_providers import (
    apply_pronunciation,
    load_pronunciation_dict,
    synth_segment,
)
from autocut.stages.tts_stage import stage_tts


class TestPronunciation:
    def test_load_dict(self, tmp_path):
        f = tmp_path / "pron.txt"
        f.write_text("# 注释\n张三=zhāng sān\n李四=lì sì\n", encoding="utf-8")
        d = load_pronunciation_dict(f)
        assert d == {"张三": "zhāng sān", "李四": "lì sì"}

    def test_apply(self):
        assert apply_pronunciation("张三来了", {"张三": "zhāng sān"}) == "zhāng sān来了"

    def test_missing_file(self):
        assert load_pronunciation_dict(Path("/nonexistent")) == {}


class TestTTSMock:
    def test_mock_tts_duration_proportional(self, test_video, mock_config, tmp_path):
        audio, words, dur = synth_segment("这是一段测试解说词", "v", "平叙", tmp_path, mock_config, "s1", "mock")
        assert audio.exists()
        assert dur >= 1.0
        assert len(words) == len("这是一段测试解说词")

    def test_tts_stage(self, test_video, mock_config, tmp_path):
        workdir = tmp_path / "w"
        workdir.mkdir()
        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="第一句解说", src_in=0, src_out=3),
            Segment(id="s2", type="original", src_in=5, src_out=8),
            Segment(id="s3", type="narration", text="第二句解说", src_in=10, src_out=13),
        ])
        save_json_artifact(workdir, "07_timeline.json", timeline)
        ctx = StageContext(test_video, workdir, mock_config)
        out = stage_tts(ctx)
        assert out["synthesized"] == 2
        assert (workdir / "08_tts").exists()
        assert (workdir / "08_word_timestamps.json").exists()


class TestAlign:
    def _ctx(self, test_video, mock_config, tmp_path, timeline, shots):
        workdir = tmp_path / "w"
        workdir.mkdir()
        save_json_artifact(workdir, "07_timeline.json", timeline)
        save_json_artifact(workdir, "02_shots.json", [s.model_dump() for s in shots])
        save_json_artifact(workdir, "00_probe.json", {"duration": 60.0})
        return StageContext(test_video, workdir, mock_config)

    def test_shrink_long_video(self, test_video, mock_config, tmp_path):
        """V > T：src_out 往前收。"""
        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="短", src_in=0, src_out=10, tts_duration=3.0),
        ])
        shots = [Shot(index=0, start=0, end=3.2), Shot(index=1, start=3.2, end=10)]
        ctx = self._ctx(test_video, mock_config, tmp_path, timeline, shots)
        stage_align(ctx)
        seg = ctx.artifacts["timeline"].segments[0]
        assert seg.src_out < 10  # 缩短了
        assert abs(seg.src_out - 3.2) < 1.2  # 吸附到镜头边界附近

    def test_extend_short_video(self, test_video, mock_config, tmp_path):
        """V < T：src_out 往后延。"""
        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="很长的解说词需要更多的时间来表达", src_in=0, src_out=2, tts_duration=6.0),
        ])
        shots = [Shot(index=0, start=0, end=6.5), Shot(index=1, start=6.5, end=20)]
        ctx = self._ctx(test_video, mock_config, tmp_path, timeline, shots)
        stage_align(ctx)
        seg = ctx.artifacts["timeline"].segments[0]
        assert seg.src_out > 2

    def test_speed_adjust(self, test_video, mock_config, tmp_path):
        """差距无法用边界消除时 speed 落在 0.85–1.2。"""
        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="x", src_in=5, src_out=8, tts_duration=2.0),
        ])
        shots = [Shot(index=0, start=0, end=60)]  # 无中间边界
        ctx = self._ctx(test_video, mock_config, tmp_path, timeline, shots)
        stage_align(ctx)
        seg = ctx.artifacts["timeline"].segments[0]
        assert 0.85 <= seg.speed <= 1.2

    def test_no_overlap_after_extend(self, test_video, mock_config, tmp_path):
        """延长不与下一段冲突。"""
        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="延长的解说词内容", src_in=0, src_out=2, tts_duration=8.0),
            Segment(id="s2", type="narration", text="下一段", src_in=6, src_out=8, tts_duration=2.0),
        ])
        shots = [Shot(index=0, start=0, end=20)]
        ctx = self._ctx(test_video, mock_config, tmp_path, timeline, shots)
        stage_align(ctx)
        segs = ctx.artifacts["timeline"].segments
        assert segs[0].src_out <= segs[1].src_in  # 不重叠


class TestOriginality:
    def test_min_narration_ratio_violation(self):
        from autocut.config import OriginalityConfig

        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="短解说", src_in=0, src_out=2),
            Segment(id="s2", type="original", src_in=10, src_out=50),
        ])
        v = _check_originality(timeline, OriginalityConfig())
        assert any(x["code"] == "min_narration_ratio" for x in v)
        assert any(x["code"] == "max_original_segment" for x in v)
        assert any(x["code"] == "max_original_total_ratio" for x in v)

    def test_pass_when_healthy(self):
        from autocut.config import OriginalityConfig

        timeline = Timeline(title="t", segments=[
            Segment(id="s1", type="narration", text="解说词内容解说词内容", src_in=0, src_out=8),
            Segment(id="s2", type="original", src_in=10, src_out=13),
        ])
        v = _check_originality(timeline, OriginalityConfig())
        assert v == []
