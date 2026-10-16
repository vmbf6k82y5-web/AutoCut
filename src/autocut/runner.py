"""runner：CLI 全流程入口。run_video / rerun_job。断点续跑 + manifest + 日志文件。"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config
from .logging_utils import setup_logging
from .manifest import write_manifest
from .pipeline import STAGE_ORDER, StageContext, build_default_pipeline
from .utils import safe_work_name

logger = logging.getLogger("autocut.runner")


def _make_workdir(video: Path, config: Config) -> Path:
    workdir = config.general.work_dir / safe_work_name(video)
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def run_video(
    video: Path,
    config: Config,
    *,
    profile_name: str | None = None,
    role_overrides: dict[str, str] | None = None,
    from_stage: str | None = None,
    series_dir: Path | None = None,
    job_id: str = "",
) -> Path:
    """跑一个视频的完整流水线，返回成片路径。"""
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(f"视频不存在: {video}")
    workdir = _make_workdir(video, config)
    # 挂 run.log
    setup_logging(config.general.log_level, workdir / "run.log")

    ctx = StageContext(
        video, workdir, config,
        profile_name=profile_name, role_overrides=role_overrides,
        job_id=job_id, series_dir=series_dir,
    )

    # 能力校验（拒绝运行）
    errors = config.validate_role_capabilities(ctx.roles, ctx.profile)
    if errors:
        for e in errors:
            logger.error("配置校验: %s", e)
        raise RuntimeError("模型能力校验失败：\n" + "\n".join(errors))

    pipeline = build_default_pipeline()
    pipeline.run(ctx, from_stage=from_stage)

    # manifest
    final = ctx.artifacts.get("final_video")
    write_manifest(ctx, extra={"final": str(final) if final else ""})

    summary = ctx.caller.tracker.summary() if ctx._caller else {}
    logger.info(
        "完成: %s，费用 $%.4f，token 入 %d 出 %d",
        final or "(未产出)", summary.get("total_cost", 0),
        summary.get("total_input_tokens", 0), summary.get("total_output_tokens", 0),
    )
    if final is None:
        raise RuntimeError("流水线未产出成片")
    return Path(str(final))


def rerun_job(
    job: str,
    from_stage: str,
    config: Config,
    *,
    profile_name: str | None = None,
    role_overrides: dict[str, str] | None = None,
) -> Path:
    """从某阶段重跑一个已存在的 job（按工作目录名或 job_id 找）。"""
    work_root = config.general.work_dir
    # 找工作目录
    workdir = work_root / job
    if not workdir.exists():
        # 按 job_id 前缀匹配
        matches = [d for d in work_root.iterdir() if d.is_dir() and job in d.name]
        if not matches:
            raise FileNotFoundError(f"找不到任务 {job}（{work_root} 下无匹配目录）")
        workdir = matches[0]
    # 从 probe 产物恢复原视频路径
    import json

    probe_file = workdir / "00_probe.json"
    if not probe_file.exists():
        raise RuntimeError(f"{job} 缺少 00_probe.json，无法定位原视频")
    video = Path(json.loads(probe_file.read_text(encoding="utf-8"))["path"])
    if from_stage not in STAGE_ORDER:
        raise ValueError(f"未知阶段 {from_stage}（可选: {STAGE_ORDER}）")
    return run_video(
        video, config, profile_name=profile_name, role_overrides=role_overrides,
        from_stage=from_stage,
    )
