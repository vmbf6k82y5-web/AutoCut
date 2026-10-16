"""align 阶段：TTS 时长与画面对齐，吸附镜头/字幕边界，原创度约束校验 → 09_timeline_aligned.json。

算法（8.4）：对每个 narration 段比较 TTS 时长 T 与画面时长 V：
1. V > T：src_out 往前收，吸附到 [src_in+T, src_in+T+1s] 内最近镜头边界；没有则截到 T
2. V < T：src_out 往后延，吸附到最近镜头边界，不与相邻片段原片区间冲突
3. 仍有差距：speed 在 0.85–1.2 调整
4. 仍不行：rewrite 改写文案（最多 2 次）再重新 TTS
入出点避免落在台词中间（吸附 SRT 句子边界，容差 0.3s）。
"""

from __future__ import annotations

import logging

from ..contracts import Shot, Timeline
from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from ..utils import parse_srt

logger = logging.getLogger("autocut.stages.align")

SHOT_SNAP_TOLERANCE = 1.0
SRT_SNAP_TOLERANCE = 0.3
SPEED_MIN, SPEED_MAX = 0.85, 1.2
REWRITE_MAX = 2


def _nearest_shot_boundary(shots: list[Shot], t: float, lo: float, hi: float) -> float | None:
    """在 [lo, hi] 内找离 t 最近的镜头边界。"""
    cands = [s.start for s in shots if lo <= s.start <= hi] + [s.end for s in shots if lo <= s.end <= hi]
    if not cands:
        return None
    return min(cands, key=lambda x: abs(x - t))


def _snap_srt_boundary(srt_bounds: list[float], t: float) -> float:
    """若 t 附近 0.3s 内有字幕句边界，吸附之（避免落在台词中间）。"""
    for b in srt_bounds:
        if abs(b - t) <= SRT_SNAP_TOLERANCE:
            return b
    return t


def _srt_boundaries(workdir) -> list[float]:
    srt = workdir / "01c_subtitle_fixed.srt"
    if not srt.exists():
        srt = workdir / "01_subtitle.srt"
    if not srt.exists():
        return []
    bounds: list[float] = []
    for e in parse_srt(srt):
        bounds += [e.start, e.end]
    return bounds


def _overlaps(a_in, a_out, b_in, b_out) -> bool:
    return a_in < b_out and b_in < a_out


def stage_align(ctx: StageContext) -> dict:
    timeline: Timeline | None = ctx.artifacts.get("timeline")
    if timeline is None:
        data = load_json_artifact(ctx.workdir, "07b_timeline_reviewed.json") or load_json_artifact(ctx.workdir, "07_timeline.json")
        if data:
            timeline = Timeline.model_validate(data)
    if timeline is None:
        raise RuntimeError("align 需要 timeline")

    shots: list[Shot] = ctx.artifacts.get("shots") or [
        Shot.model_validate(x) for x in (load_json_artifact(ctx.workdir, "02_shots.json") or [])
    ]
    probe = load_json_artifact(ctx.workdir, "00_probe.json") or {}
    duration = probe.get("duration", 1e9)
    srt_bounds = _srt_boundaries(ctx.workdir)

    adjusted = 0
    for i, seg in enumerate(timeline.segments):
        if seg.type != "narration":
            continue
        T = seg.tts_duration or max(1.0, len(seg.text) * 0.18)
        V = seg.src_duration
        if abs(T - V) / max(V, 0.01) < 0.05:
            continue  # 已对齐

        if V > T:
            # 缩短：吸附 [src_in+T, src_in+T+1s] 内镜头边界
            target = seg.src_in + T
            snap = _nearest_shot_boundary(shots, target, target, target + SHOT_SNAP_TOLERANCE)
            new_out = snap if snap is not None else target
            new_out = _snap_srt_boundary(srt_bounds, new_out)
            new_out = min(new_out, duration)
            if new_out > seg.src_in + 0.3:
                seg.src_out = new_out
                adjusted += 1
        else:
            # 延长：src_out 往后延到最近镜头边界，避免与下一段冲突
            target = seg.src_in + T
            snap = _nearest_shot_boundary(shots, target, target, min(target + 5.0, duration))
            new_out = snap if snap is not None else min(target, duration)
            # 避免与后续段冲突
            for j in range(i + 1, len(timeline.segments)):
                nxt = timeline.segments[j]
                if nxt.source == seg.source and _overlaps(seg.src_in, new_out, nxt.src_in, nxt.src_out):
                    new_out = min(new_out, nxt.src_in - 0.05)
                    break
            new_out = _snap_srt_boundary(srt_bounds, new_out)
            new_out = min(new_out, duration)
            if new_out > seg.src_in + 0.3 and new_out <= duration:
                seg.src_out = new_out
                adjusted += 1

        # 仍有差距：speed 微调
        V2 = seg.src_duration
        if T > 0 and abs(T - V2) / max(V2, 0.01) > 0.15:
            speed = V2 / T
            if SPEED_MIN <= speed <= SPEED_MAX:
                seg.speed = round(speed, 3)
            else:
                seg.speed = max(SPEED_MIN, min(SPEED_MAX, speed))

    # rewrite 兜底（差距仍过大且启用了 rewrite 角色）
    # 简化：此处不重跑 TTS，仅记录；完整 rewrite 循环在 align 与 tts 间迭代
    save_json_artifact(ctx.workdir, "09_timeline_aligned.json", timeline)
    ctx.artifacts["timeline"] = timeline

    # 原创度校验
    violations = _check_originality(timeline, ctx.config.originality)
    if violations:
        save_json_artifact(ctx.workdir, "09b_originality_violations.json", violations)
        logger.warning("原创度约束: %d 项超限", len(violations))
    logger.info("align: 调整 %d 段", adjusted)
    return {"adjusted": adjusted, "originality_violations": len(violations)}


def _check_originality(timeline: Timeline, orig) -> list[dict]:
    """校验原创度约束，返回违规项。"""
    violations: list[dict] = []
    total = sum(s.src_duration / max(0.01, s.speed) for s in timeline.segments)
    narration = sum(s.src_duration / max(0.01, s.speed) for s in timeline.segments if s.type == "narration")
    original = sum(s.src_duration / max(0.01, s.speed) for s in timeline.segments if s.type == "original")
    if total > 0:
        ratio = narration / total
        if ratio < orig.min_narration_ratio:
            violations.append({"code": "min_narration_ratio", "value": round(ratio, 3), "min": orig.min_narration_ratio})
        orig_ratio = original / total
        if orig_ratio > orig.max_original_total_ratio:
            violations.append({"code": "max_original_total_ratio", "value": round(orig_ratio, 3), "max": orig.max_original_total_ratio})
    for s in timeline.segments:
        if s.type == "original" and s.src_duration > orig.max_original_segment_seconds:
            violations.append({"code": "max_original_segment", "segment": s.id, "value": round(s.src_duration, 2),
                               "max": orig.max_original_segment_seconds})
    return violations
