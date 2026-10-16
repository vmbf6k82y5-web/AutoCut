"""shots provider：pyscenedetect（360p 分析）/ fixed 兜底。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..contracts import Shot
from ..utils import find_ffmpeg, run_cmd

logger = logging.getLogger("autocut.stages.shot_providers")


class FixedIntervalProvider:
    """固定间隔兜底镜头切分。"""

    name = "fixed"
    interval: float = 4.0

    def detect(self, video: Path, workdir: Path, config) -> list[Shot]:
        from ..utils import find_ffprobe
        from .probe import probe_video

        probe = probe_video(video, find_ffprobe(config.general.ffmpeg_path))
        dur = probe["duration"]
        shots = []
        i = 0
        t = 0.0
        while t < dur:
            end = min(t + self.interval, dur)
            shots.append(Shot(index=i, start=t, end=end))
            t = end
            i += 1
        return shots


class PySceneDetectProvider:
    """PySceneDetect 镜头检测，先降到 360p 分析提速。"""

    name = "pyscenedetect"

    def detect(self, video: Path, workdir: Path, config) -> list[Shot]:
        try:
            from scenedetect import ContentDetector, SceneManager, open_video
        except ImportError as e:
            logger.warning("scenedetect 不可用，回退 fixed: %s", e)
            return FixedIntervalProvider().detect(video, workdir, config)

        from ..utils import find_ffprobe
        from .probe import probe_video

        probe = probe_video(video, find_ffprobe(config.general.ffmpeg_path))
        duration = probe["duration"]

        # 降到 360p 代理以加速
        proxy = workdir / "shots_proxy_360p.mp4"
        ffmpeg = find_ffmpeg(config.general.ffmpeg_path)
        run_cmd(
            [ffmpeg, "-y", "-i", video, "-vf", "scale=-2:360", "-c:v", "libx264",
             "-preset", "ultrafast", "-crf", "30", "-an", str(proxy)],
            timeout=600,
        )
        analysis_video = proxy if proxy.exists() else video

        try:
            v = open_video(str(analysis_video))
            manager = SceneManager()
            manager.add_detector(ContentDetector(threshold=27.0))
            manager.detect_scenes(v, show_progress=False)
            scene_list = manager.get_scene_list()
        except Exception as e:  # noqa: BLE001
            logger.warning("scenedetect 检测失败，回退 fixed: %s", e)
            return FixedIntervalProvider().detect(video, workdir, config)

        shots: list[Shot] = []
        for i, (start, end) in enumerate(scene_list):
            shots.append(Shot(index=i, start=start.seconds, end=end.seconds))
        if not shots:
            # 未检出任何镜头边界 → 整段一个镜头
            shots = [Shot(index=0, start=0.0, end=duration)]
        logger.info("镜头检测: %d 个镜头", len(shots))
        return shots
