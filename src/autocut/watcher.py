"""watch 模式：监听文件夹，新视频自动处理。批量前自动估算，超预算跳过。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import Config
from .series import VIDEO_EXTS

logger = logging.getLogger("autocut.watcher")


def _is_stable(path: Path, wait: float = 2.0) -> bool:
    """文件大小稳定（复制完成）才算就绪。"""
    try:
        s1 = path.stat().st_size
        time.sleep(wait)
        s2 = path.stat().st_size
        return s1 == s2 and s1 > 0
    except OSError:
        return False


def run_watch(inbox: Path, config: Config, *, profile_name: str | None = None, interval: float = 5.0,
              once: bool = False) -> None:
    """监听 inbox 目录，新视频自动跑全流程。"""
    from .estimate import estimate_video
    from .runner import run_video

    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    done_dir = inbox / ".processed"
    done_dir.mkdir(exist_ok=True)
    logger.info("监听目录: %s（每 %.0fs 扫描）", inbox, interval)
    seen: set[str] = set()

    while True:
        for video in sorted(inbox.iterdir()):
            if video.suffix.lower() not in VIDEO_EXTS or video.name in seen:
                continue
            marker = done_dir / f"{video.name}.done"
            if marker.exists():
                continue
            if not _is_stable(video):
                continue
            seen.add(video.name)
            logger.info("发现新视频: %s", video.name)
            # 估算，超预算跳过
            try:
                est = estimate_video(video, config, profile_name)
                if not est.within_budget:
                    logger.warning("预估 $%.4f 超预算，跳过 %s", est.total_cost, video.name)
                    marker.write_text("skipped: over budget", encoding="utf-8")
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning("估算失败(%s)，仍尝试处理", e)
            try:
                final = run_video(video, config, profile_name=profile_name)
                marker.write_text(f"done: {final}", encoding="utf-8")
                logger.info("完成 %s → %s", video.name, final)
            except Exception as e:  # noqa: BLE001
                marker.write_text(f"error: {e}", encoding="utf-8")
                logger.error("处理失败 %s: %s", video.name, e)
        if once:
            break
        time.sleep(interval)
