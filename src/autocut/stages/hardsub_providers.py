"""hardsub provider：opencv 硬字幕检测（边缘/高对比文字特征，不用 OCR）。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..contracts import HardsubInfo
from ..utils import find_ffmpeg, parse_srt, run_cmd

logger = logging.getLogger("autocut.stages.hardsub_providers")


class NoHardsubProvider:
    """跳过检测（关闭硬字幕处理）。"""

    name = "none"

    def detect(self, video: Path, srt: Path | None, workdir: Path, config) -> HardsubInfo:
        return HardsubInfo(has_hardsub=False, confidence=0.0)


class OpenCVHardsubProvider:
    """在有台词时间点抽帧，统计画面底部 25% 区域的文字边缘特征。"""

    name = "opencv"

    def detect(self, video: Path, srt: Path | None, workdir: Path, config) -> HardsubInfo:
        try:
            import cv2
            import numpy as np
        except ImportError as e:
            logger.warning("opencv 不可用，跳过硬字幕检测: %s", e)
            return HardsubInfo(has_hardsub=False, confidence=0.0)

        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        # 采样时间点：有台词的时间点
        times: list[float] = []
        if srt and srt.exists():
            for e in parse_srt(srt):
                times.append((e.start + e.end) / 2)
        if not times:
            # 无字幕则均匀采样
            times = [5, 15, 25, 35, 45, 55]
        times = times[:20]  # 最多 20 帧

        frames_dir = workdir / "hardsub_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        hits = 0
        total = 0
        region_tops: list[float] = []
        for i, t in enumerate(times):
            frame = frames_dir / f"f{i:03d}.png"
            try:
                run_cmd([ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", str(frame)], timeout=60)
            except Exception as e:  # noqa: BLE001
                logger.debug("抽帧失败 t=%.1f: %s", t, e)
                continue
            img = cv2.imread(str(frame))
            if img is None:
                continue
            h, w = img.shape[:2]
            # 底部 25% 区域
            top = int(h * 0.75)
            roi = img[top:h, 0:w]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            # 边缘检测
            edges = cv2.Canny(gray, 80, 200)
            # 文字区域的边缘密度 + 水平投影峰值
            density = float(np.count_nonzero(edges)) / edges.size
            # 高对比：局部标准差
            std = float(np.std(gray))
            is_text = density > 0.035 and std > 40
            if is_text:
                hits += 1
                region_tops.append(0.75)
            total += 1
        if total == 0:
            return HardsubInfo(has_hardsub=False, confidence=0.0)
        ratio = hits / total
        has = ratio > 0.5  # 多数帧含底部文字特征才判定
        region_top = sum(region_tops) / len(region_tops) if region_tops else 0.75
        logger.info("硬字幕检测: %d/%d 帧含文字特征 → has=%s", hits, total, has)
        return HardsubInfo(has_hardsub=has, region_top_ratio=region_top, confidence=ratio)
