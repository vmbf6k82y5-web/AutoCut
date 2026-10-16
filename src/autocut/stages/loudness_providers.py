"""loudness provider：ffmpeg ebur128 分析高能区间。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..contracts import HighEnergyRange
from ..utils import find_ffmpeg, run_cmd

logger = logging.getLogger("autocut.stages.loudness_providers")


class EBUR128Provider:
    """ffmpeg ebur128 滤镜分析响度，找出高能区间。"""

    name = "ffmpeg_ebur128"

    def analyze(self, video: Path, workdir: Path, config) -> list[HighEnergyRange]:
        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        # ebur128 输出逐帧 momentary loudness 到 stderr
        proc = run_cmd(
            [ffmpeg, "-nostats", "-i", video, "-filter_complex", "ebur128=metadata=1", "-f", "null", "-"],
            check=False, timeout=900,
        )
        text = proc.stderr or ""
        # 解析 Summary 的 Integrated loudness 作为基准
        integrated = self._parse_integrated(text)
        # 逐段窗口响度（简化：按 1s 窗口估计）
        windows = self._windowed_loudness(video, ffmpeg, duration_hint=None)
        ranges = self._extract_high_energy(windows, integrated)
        logger.info("响度分析: 基准 %.1f LUFS, 高能区间 %d 个", integrated, len(ranges))
        return ranges

    def _parse_integrated(self, text: str) -> float:
        m = re.search(r"Integrated loudness:\s+I:\s+(-?[\d.]+) LUFS", text)
        return float(m.group(1)) if m else -23.0

    def _windowed_loudness(self, video: Path, ffmpeg, duration_hint) -> list[tuple[float, float]]:
        """按 1s 窗口测响度：用 astats 太贵，改用 ebur128 分段。简化：分段 volumedetect。"""
        # 获取时长
        from ..utils import find_ffprobe
        from .probe import probe_video

        try:
            dur = probe_video(video, find_ffprobe("")).get("duration", 0.0)
        except Exception:  # noqa: BLE001
            dur = duration_hint or 0.0
        windows: list[tuple[float, float]] = []
        step = 2.0
        t = 0.0
        while t < dur:
            end = min(t + step, dur)
            proc = run_cmd(
                [ffmpeg, "-nostats", "-ss", f"{t:.2f}", "-to", f"{end:.2f}", "-i", video,
                 "-af", "volumedetect", "-f", "null", "-"],
                check=False, timeout=60,
            )
            mean = self._parse_volumedetect(proc.stderr or "")
            windows.append((t, mean))
            t = end
        return windows

    def _parse_volumedetect(self, text: str) -> float:
        m = re.search(r"mean_volume:\s+(-?[\d.]+) dB", text)
        return float(m.group(1)) if m else -30.0

    def _extract_high_energy(self, windows: list[tuple[float, float]], integrated: float) -> list[HighEnergyRange]:
        """mean_volume 高于阈值（整体均值+6dB）的连续窗口合并为高能区间。"""
        if not windows:
            return []
        means = [m for _, m in windows]
        baseline = sum(means) / len(means)
        threshold = baseline + 6.0
        ranges: list[HighEnergyRange] = []
        cur_start: float | None = None
        cur_vals: list[float] = []
        step = 2.0
        for t, m in windows:
            if m >= threshold:
                if cur_start is None:
                    cur_start = t
                cur_vals.append(m)
            elif cur_start is not None:
                ranges.append(self._make_range(cur_start, t, cur_vals, baseline))
                cur_start, cur_vals = None, []
        if cur_start is not None:
            ranges.append(self._make_range(cur_start, windows[-1][0] + step, cur_vals, baseline))
        # 归一化 score
        if ranges:
            mx = max(r.max_loudness for r in ranges)
            for r in ranges:
                r.score = max(0.0, min(1.0, (r.max_loudness - baseline) / max(1.0, mx - baseline)))
        return ranges

    def _make_range(self, start, end, vals, baseline) -> HighEnergyRange:
        return HighEnergyRange(
            start=start, end=end,
            mean_loudness=sum(vals) / len(vals),
            max_loudness=max(vals),
            score=0.0,
        )
