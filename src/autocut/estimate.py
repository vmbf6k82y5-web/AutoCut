"""estimate：预估 token 与费用（只跑 probe + 字幕统计，不调用真实模型）。"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config
from .contracts import Estimate, RoleEstimate
from .model_client import _estimate_tokens
from .utils import find_ffprobe, parse_srt

logger = logging.getLogger("autocut.estimate")

#: 各角色输入/输出的经验放大系数（相对字幕字数）
_ROLE_IO_FACTOR = {
    "name_fix": (1.0, 0.1),
    "outline": (1.2, 0.15),
    "scout": (1.0, 0.2),
    "vision": (0.5, 0.3),
    "writer": (1.5, 0.8),
    "reviewer": (1.0, 0.5),
    "rewrite": (0.2, 0.2),
    "memory": (0.8, 0.4),
    "metadata": (0.3, 0.2),
    "style": (0.2, 0.3),
    "fixer": (0.0, 0.0),
}


def estimate_video(video: Path, config: Config, profile_name: str | None = None) -> Estimate:
    """只读 probe + 字幕（若有），按角色分配与单价估算。"""
    video = Path(video)
    duration = 0.0
    subtitle_chars = 0
    try:
        from .stages.probe import probe_video

        info = probe_video(video, find_ffprobe(config.general.ffmpeg_path))
        duration = info["duration"]
    except Exception as e:  # noqa: BLE001
        logger.warning("estimate probe 失败: %s", e)
        duration = 600.0

    # 找同名字幕文件统计字数
    srt = video.with_suffix(".srt")
    # 无字幕按时长估算：约每秒 4 字
    subtitle_chars = sum(len(e.text) for e in parse_srt(srt)) if srt.exists() else int(duration * 4)

    roles = config.effective_roles(profile_name)
    profile = config.effective_profile(profile_name)
    est_roles: list[RoleEstimate] = []
    total = 0.0
    base_tokens = _estimate_tokens("x" * subtitle_chars)  # 字幕 token 基数

    for role, names in roles.items():
        if not names:
            continue
        fi, fo = _ROLE_IO_FACTOR.get(role, (0.5, 0.3))
        # best 模式 vision 看视频，token 更多
        if role == "vision" and profile.vision_mode == "video":
            fi *= 3
        est_in = int(base_tokens * fi) + 500  # 系统提示词开销
        est_out = int(base_tokens * fo) + 100
        spec = config.model_by_name(names[0])
        price_in = spec.price_in if spec else 0.0
        price_out = spec.price_out if spec else 0.0
        cost = est_in / 1e6 * price_in + est_out / 1e6 * price_out
        total += cost
        est_roles.append(RoleEstimate(
            role=role, model=names[0], est_input_tokens=est_in, est_output_tokens=est_out, est_cost=cost
        ))

    within = total <= config.limits.max_cost_per_episode
    return Estimate(
        video=str(video), duration_seconds=duration, subtitle_chars=subtitle_chars,
        roles=est_roles, total_cost=round(total, 6), within_budget=within,
    )
