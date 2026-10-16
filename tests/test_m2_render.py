"""M2 测试：手写 Timeline → ffmpeg_mp4 出 MP4。

覆盖：混音、ASS 字幕、9:16、钩子乱序、硬字幕模式、自动配乐、编码器检测。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from autocut.contracts import HardsubInfo, Segment, Timeline
from autocut.render.ass import ASSEvent, build_ass, events_from_narration, split_text_to_lines
from autocut.render.ffmpeg_mp4 import FFmpegRenderer, detect_encoder


def _probe(path: Path, ffmpeg_dir: Path) -> dict:
    proc = subprocess.run(
        [str(ffmpeg_dir / "ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def _duration(path: Path, ffmpeg_dir: Path) -> float:
    return float(_probe(path, ffmpeg_dir)["format"]["duration"])


class TestASSEvents:
    def test_split_lines(self):
        lines = split_text_to_lines("他推开门。屋里一片漆黑，什么也看不见。这时电话响了。", 10)
        assert all(len(line) <= 10 for line in lines)
        assert "".join(lines) == "他推开门。屋里一片漆黑，什么也看不见。这时电话响了。"

    def test_events_timing(self):
        words = [{"text": c, "start": i * 0.1, "end": (i + 1) * 0.1} for i, c in enumerate("一二三四五六七八九十")]
        evs = events_from_narration("一二三四五六七八九十", words, 5.0, 6.0, 5)
        assert evs[0].start >= 5.0
        assert evs[-1].end <= 6.0 + 0.5
        # 时间单调
        for a, b in zip(evs, evs[1:], strict=False):
            assert b.start >= a.start

    def test_build_ass_file(self, tmp_path):
        evs = [ASSEvent(0, 1.5, "第一句"), ASSEvent(1.5, 3.0, "第二句")]
        out = build_ass(evs, tmp_path / "s.ass", font="Sans", font_size=20, color="&H00FFFFFF",
                        outline_color="&H00000000", outline=2, margin_v=40, width=1920, height=1080)
        content = out.read_text(encoding="utf-8")
        assert "Dialogue:" in content
        assert "第一句" in content
        assert "[V4+ Styles]" in content


class TestRender:
    def _make_timeline(self, hook: bool = False) -> Timeline:
        segs = [
            Segment(id="s1", type="narration", text="注意看这个男人叫小帅", src_in=0, src_out=4, emotion="平叙"),
            Segment(id="s2", type="original", src_in=10, src_out=14),
            Segment(id="s3", type="narration", text="他发现了惊人的秘密", src_in=20, src_out=24, emotion="紧张"),
        ]
        if hook:
            # 钩子：高潮段乱序到片头（与 s2 同区间，共 2 次允许）
            segs.insert(0, Segment(id="hook", type="narration", text="一切的转折从这里开始",
                                   src_in=10, src_out=13, is_hook=True, emotion="紧张"))
        return Timeline(title="测试成片", style="悬疑", total_target_seconds=20, segments=segs)

    def _assets(self, video: Path, tts_words: dict) -> dict:
        return {
            "sources": {"main": video},
            "word_timestamps": tts_words,
            "hardsub": HardsubInfo(has_hardsub=False),
            "bgm_tracks": [],
        }

    def test_render_basic_mp4(self, assets, tmp_path, mock_config, ffmpeg_dir):
        """基础 16:9 渲染，产出可播放 MP4。"""
        video = assets / "test_60s.mp4"
        timeline = self._make_timeline()
        # mock tts 词级时间戳（每段）
        words = {s.id: [{"text": c, "start": i * 0.2, "end": (i + 1) * 0.2}
                        for i, c in enumerate(s.text)] for s in timeline.segments if s.text}
        mock_config.general.output_dir = tmp_path / "output"
        renderer = FFmpegRenderer()
        out = renderer.render(timeline, self._assets(video, words), tmp_path, mock_config)
        assert out.exists() and out.suffix == ".mp4"
        dur = _duration(out, ffmpeg_dir)
        # 3 段各 4s ≈ 12s（±1s 容差）
        assert 10.0 <= dur <= 14.0
        # 可播放：有视频流和音频流
        streams = _probe(out, ffmpeg_dir)["streams"]
        kinds = {s["codec_type"] for s in streams}
        assert "video" in kinds and "audio" in kinds

    def test_render_9_16(self, assets, tmp_path, mock_config, ffmpeg_dir):
        """9:16 画幅：模糊背景填充。"""
        mock_config.video.aspect = "9:16"
        mock_config.video.resolution = "720p"
        mock_config.general.output_dir = tmp_path / "output"
        video = assets / "test_60s.mp4"
        timeline = Timeline(title="竖屏", segments=[
            Segment(id="s1", type="narration", text="竖屏测试", src_in=0, src_out=3),
        ])
        words = {"s1": [{"text": c, "start": i * 0.3, "end": (i + 1) * 0.3} for i, c in enumerate("竖屏测试")]}
        out = FFmpegRenderer().render(timeline, self._assets(video, words), tmp_path, mock_config)
        vstream = next(s for s in _probe(out, ffmpeg_dir)["streams"] if s["codec_type"] == "video")
        w, h = vstream["width"], vstream["height"]
        assert abs(w / h - 9 / 16) < 0.02  # 竖屏比例

    def test_render_hook_out_of_order(self, assets, tmp_path, mock_config, ffmpeg_dir):
        """钩子乱序：片头先播高潮段，再顺叙。验证可渲染且时长正确。"""
        video = assets / "test_60s.mp4"
        timeline = self._make_timeline(hook=True)
        words = {s.id: [{"text": c, "start": i * 0.2, "end": (i + 1) * 0.2}
                        for i, c in enumerate(s.text)] for s in timeline.segments if s.text}
        mock_config.general.output_dir = tmp_path / "output"
        out = FFmpegRenderer().render(timeline, self._assets(video, words), tmp_path, mock_config)
        assert out.exists()
        # 4 段：3+4+4+4 = 15s 左右
        assert 13.0 <= _duration(out, ffmpeg_dir) <= 17.0

    def test_render_hardsub_modes(self, assets, tmp_path, mock_config, ffmpeg_dir):
        """硬字幕三种模式都能渲染。"""
        video = assets / "test_hardsub.mp4"
        hardsub = HardsubInfo(has_hardsub=True, region_top_ratio=0.75, confidence=0.9)
        for mode in ("move_up", "bar", "blur"):
            mock_config.subtitle_style.hardsub_mode = mode
            mock_config.general.output_dir = tmp_path / f"out_{mode}"
            timeline = Timeline(title=f"硬字幕{mode}", segments=[
                Segment(id="s1", type="narration", text="测试硬字幕处理", src_in=0, src_out=3),
            ])
            words = {"s1": [{"text": c, "start": i * 0.3, "end": (i + 1) * 0.3} for i, c in enumerate("测试硬字幕处理")]}
            a = self._assets(video, words)
            a["hardsub"] = hardsub
            out = FFmpegRenderer().render(timeline, a, tmp_path, mock_config)
            assert out.exists(), f"mode={mode} 渲染失败"

    def test_render_with_bgm(self, assets, tmp_path, mock_config, ffmpeg_dir):
        """自动配乐：bgm_library 提供曲目时混音。"""
        # 生成一段 BGM
        bgm = tmp_path / "bgm.mp3"
        subprocess.run([str(ffmpeg_dir / "ffmpeg"), "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=30", str(bgm)], check=True, capture_output=True)
        video = assets / "test_60s.mp4"
        timeline = Timeline(title="带配乐", segments=[
            Segment(id="s1", type="narration", text="配乐测试", src_in=0, src_out=4),
        ])
        words = {"s1": [{"text": c, "start": i * 0.3, "end": (i + 1) * 0.3} for i, c in enumerate("配乐测试")]}
        a = self._assets(video, words)
        a["bgm_tracks"] = [{"path": bgm, "mood": "紧张"}]
        mock_config.general.output_dir = tmp_path / "output"
        out = FFmpegRenderer().render(timeline, a, tmp_path, mock_config)
        assert out.exists()
        assert "audio" in {s["codec_type"] for s in _probe(out, ffmpeg_dir)["streams"]}

    def test_render_speed_change(self, assets, tmp_path, mock_config, ffmpeg_dir):
        """变速段：speed=1.2 缩短成片时长。"""
        video = assets / "test_60s.mp4"
        timeline = Timeline(title="变速", segments=[
            Segment(id="s1", type="original", src_in=0, src_out=6, speed=1.5),
        ])
        mock_config.general.output_dir = tmp_path / "output"
        out = FFmpegRenderer().render(timeline, self._assets(video, {}), tmp_path, mock_config)
        # 6s / 1.5 = 4s
        assert 3.0 <= _duration(out, ffmpeg_dir) <= 5.0

    def test_encoder_detect(self, ffmpeg_dir):
        enc = detect_encoder(ffmpeg_dir / "ffmpeg")
        assert enc in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
