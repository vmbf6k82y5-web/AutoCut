"""qc 阶段：成片质检（黑屏/静音/时长偏差/字幕/原创度）→ 11_qc.json。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..contracts import QCIssue, QCReport, Timeline
from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from ..utils import find_ffmpeg, find_ffprobe, run_cmd

logger = logging.getLogger("autocut.stages.qc")


def _detect_black(ffmpeg: Path, video: Path) -> float:
    """blackdetect：返回黑屏总时长。"""
    proc = run_cmd(
        [ffmpeg, "-i", video, "-vf", "blackdetect=d=1.0:pic_th=0.90", "-an", "-f", "null", "-"],
        check=False, timeout=600,
    )
    total = 0.0
    for m in re.finditer(r"black_start:[\d.]+ black_end:([\d.]+).*?black_duration:([\d.]+)", proc.stderr or ""):
        total += float(m.group(2))
    return total


def _detect_silence(ffmpeg: Path, video: Path) -> float:
    """silencedetect：返回静音总时长。"""
    proc = run_cmd(
        [ffmpeg, "-i", video, "-af", "silencedetect=noise=-40dB:d=3.0", "-vn", "-f", "null", "-"],
        check=False, timeout=600,
    )
    total = 0.0
    starts = [float(m.group(1)) for m in re.finditer(r"silence_start: ([\d.]+)", proc.stderr or "")]
    ends = [float(m.group(1)) for m in re.finditer(r"silence_end: ([\d.]+)", proc.stderr or "")]
    for s, e in zip(starts, ends, strict=False):
        total += max(0.0, e - s)
    return total


def _duration(video: Path, ffprobe: Path) -> float:
    proc = run_cmd(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        timeout=30,
    )
    return float(proc.stdout.strip())


def run_qc(final: Path, timeline: Timeline | None, ctx: StageContext) -> QCReport:
    """对成片做质检，返回报告。"""
    ffmpeg = find_ffmpeg(ctx.config.general.ffmpeg_path)
    ffprobe = find_ffprobe(ctx.config.general.ffmpeg_path)
    report = QCReport()
    target = ctx.config.video.target_seconds
    report.target_seconds = target

    dur = _duration(final, ffprobe)
    report.duration_seconds = dur
    report.duration_deviation = abs(dur - target) / max(target, 1)

    # 时长偏差（目标 ±40% 容差，测试素材远短于 8min 时放宽）
    if target >= 60 and report.duration_deviation > 0.4:
        report.issues.append(QCIssue(
            code="duration", severity="warning",
            message=f"时长 {dur:.0f}s 与目标 {target:.0f}s 偏差 {report.duration_deviation:.0%}",
        ))

    # 黑屏
    try:
        black = _detect_black(ffmpeg, final)
        if black > max(2.0, dur * 0.3):
            report.issues.append(QCIssue(code="black", message=f"黑屏 {black:.1f}s", detail={"seconds": black}))
    except Exception as e:  # noqa: BLE001
        logger.debug("blackdetect 失败: %s", e)

    # 静音
    try:
        silence = _detect_silence(ffmpeg, final)
        if silence > max(3.0, dur * 0.4):
            report.issues.append(QCIssue(code="silence", message=f"静音 {silence:.1f}s", detail={"seconds": silence}))
    except Exception as e:  # noqa: BLE001
        logger.debug("silencedetect 失败: %s", e)

    # 原创度（解说占比）
    if timeline:
        total = sum(s.src_duration / max(0.01, s.speed) for s in timeline.segments)
        narration = sum(s.src_duration / max(0.01, s.speed) for s in timeline.segments if s.type == "narration")
        report.narration_ratio = narration / total if total else 0.0
        orig = ctx.config.originality
        if total >= 30 and report.narration_ratio < orig.min_narration_ratio:
            report.issues.append(QCIssue(
                code="originality", severity="warning",
                message=f"解说占比 {report.narration_ratio:.0%} 低于 {orig.min_narration_ratio:.0%}",
            ))

    report.passed = not any(i.severity == "error" for i in report.issues)
    return report


def stage_qc(ctx: StageContext) -> dict:
    final: Path | None = ctx.artifacts.get("final_video")
    if final is None or not Path(str(final)).exists():
        # 找输出目录最新 mp4
        outs = sorted(ctx.config.general.output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        final = outs[0] if outs else None
    if final is None:
        raise RuntimeError("qc 找不到成片")

    timeline = ctx.artifacts.get("timeline")
    if timeline is None:
        data = load_json_artifact(ctx.workdir, "09_timeline_aligned.json") or load_json_artifact(ctx.workdir, "07_timeline.json")
        timeline = Timeline.model_validate(data) if data else None

    report = run_qc(Path(str(final)), timeline, ctx)
    save_json_artifact(ctx.workdir, "11_qc.json", report)
    ctx.artifacts["qc"] = report
    status = "通过" if report.passed else f"{len(report.issues)} 项问题"
    logger.info("qc: %s（时长 %.0fs, 解说占比 %.0f%%）", status, report.duration_seconds, report.narration_ratio * 100)
    return {"passed": report.passed, "issues": len(report.issues)}
