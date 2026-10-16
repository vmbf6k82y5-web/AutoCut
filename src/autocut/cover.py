"""封面图生成：高潮节点中清晰度最高的帧 + 大字标题。"""

from __future__ import annotations

import logging
from pathlib import Path

from .contracts import Outline
from .utils import find_ffmpeg, run_cmd

logger = logging.getLogger("autocut.cover")


def _frame_sharpness(path: Path) -> float:
    """拉普拉斯方差估计清晰度。"""
    try:
        import cv2

        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:  # noqa: BLE001
        return 0.0


def pick_best_frame(video: Path, outline: Outline | None, workdir: Path, config) -> Path | None:
    """从高潮节点区间抽几帧，选清晰度最高的。"""
    ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
    # 选高潮/转折节点区间，否则视频中部
    intervals: list[tuple[float, float]] = []
    if outline and outline.nodes:
        for n in outline.nodes:
            if n.function in ("高潮", "转折"):
                intervals.append((n.time_start, n.time_end))
    if not intervals:
        intervals = [(0.0, 60.0)]
    start, end = intervals[0]
    mid = (start + end) / 2
    times = [start + 0.5, mid, end - 0.5]
    frames_dir = workdir / "cover_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[float, Path] | None = None
    for i, t in enumerate(times):
        f = frames_dir / f"cf{i}.png"
        try:
            run_cmd([ffmpeg, "-y", "-ss", f"{max(0,t):.2f}", "-i", video, "-frames:v", "1", str(f)], timeout=60)
        except Exception as e:  # noqa: BLE001
            logger.debug("封面抽帧失败 t=%.1f: %s", t, e)
            continue
        if not f.exists():
            continue
        sharp = _frame_sharpness(f)
        if best is None or sharp > best[0]:
            best = (sharp, f)
    return best[1] if best else None


def make_cover(
    video: Path,
    title: str,
    outline: Outline | None,
    workdir: Path,
    config,
    out_path: Path,
) -> Path | None:
    """生成封面：最佳帧 + 大字标题，按画幅出图。"""
    frame = pick_best_frame(video, outline, workdir, config)
    if frame is None:
        logger.warning("封面：无可用帧，跳过")
        return None
    ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
    out_w, out_h = config.video.out_size
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 标题分行（每行不超过 10 字）
    title_lines = [title[i : i + 10] for i in range(0, min(len(title), 30), 10)] or ["影视解说"]
    drawtext_filters = []
    y = int(out_h * 0.7)
    for line in title_lines:
        drawtext_filters.append(
            f"drawtext=text='{_escape_drawtext(line)}':fontcolor=white:fontsize={int(out_h/9)}:"
            f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={y}"
        )
        y += int(out_h / 8)
    vf = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}," + ",".join(drawtext_filters)
    try:
        run_cmd([ffmpeg, "-y", "-i", frame, "-vf", vf, "-frames:v", "1", str(out_path)], timeout=60)
        return out_path
    except Exception as e:  # noqa: BLE001
        logger.warning("封面合成失败: %s", e)
        # 无字体时退化为纯帧缩放
        try:
            run_cmd([ffmpeg, "-y", "-i", frame, "-vf", f"scale={out_w}:{out_h}", "-frames:v", "1", str(out_path)], timeout=60)
            return out_path
        except Exception:  # noqa: BLE001
            return None


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
