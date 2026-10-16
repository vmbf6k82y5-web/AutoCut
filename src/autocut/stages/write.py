"""write / review 阶段：生成解说时间轴（含倒叙钩子），best 模式自审。"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from ..contracts import Candidate, Outline, Segment, Timeline
from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from ..prompt import build_cached_prefix, load_prompt
from ..style import get_style_guide

logger = logging.getLogger("autocut.stages.write")


class WriterSegment(BaseModel):
    id: str = "seg1"
    type: str = "narration"
    outline_node_id: str | None = None
    text: str = ""
    emotion: str = "平叙"
    src_in: float = 0.0
    src_out: float = 5.0
    is_hook: bool = False


class WriterOutput(BaseModel):
    title: str = "解说"
    segments: list[WriterSegment] = Field(default_factory=list)


def _build_timeline(ctx: StageContext, out: WriterOutput) -> Timeline:
    segments: list[Segment] = []
    for ws in out.segments:
        try:
            segments.append(Segment(
                id=ws.id, type=ws.type, outline_node_id=ws.outline_node_id,
                text=ws.text, emotion=ws.emotion, src_in=ws.src_in, src_out=ws.src_out,
                is_hook=ws.is_hook,
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("跳过非法 segment %s: %s", ws.id, e)
    return Timeline(
        title=out.title,
        style=ctx.config.video.style,
        total_target_seconds=ctx.config.video.target_seconds,
        segments=segments,
    )


def stage_write(ctx: StageContext) -> dict:
    outline: Outline | None = ctx.artifacts.get("outline")
    if outline is None:
        data = load_json_artifact(ctx.workdir, "03b_outline.json")
        outline = Outline.model_validate(data) if data else Outline()
    candidates: list[Candidate] = ctx.artifacts.get("candidates") or [
        Candidate.model_validate(x) for x in (load_json_artifact(ctx.workdir, "04_candidates.json") or [])
    ]
    vision_descs = ctx.artifacts.get("vision") or []
    vision_text = json.dumps([d.model_dump() for d in vision_descs], ensure_ascii=False) if vision_descs else "（无画面理解）"

    # 风格学习
    style_guide, examples = get_style_guide(ctx, ctx.config.style.examples_dir)
    style_block = ""
    if style_guide:
        ex = "\n\n范例节选：\n" + "\n---\n".join(e[:500] for e in examples) if examples else ""
        style_block = f"【风格指南】（优先于风格标签）：\n{style_guide}\n{ex}"
    else:
        style_block = f"【风格标签】{ctx.config.video.style}"

    prompt = load_prompt("writer", ctx.config.prompts.custom_dir)
    hook_note = "开头可选一个高潮/转折节点的 3-5 秒画面作为倒叙钩子（is_hook=true），配一句悬念句。" if ctx.config.story.hook else "不使用倒叙钩子。"
    fixed_prefix = build_cached_prefix(
        "你是顶级影视解说写手。",
        f"剧集记忆：\n{_mem(ctx)}" if _mem(ctx) else "",
        hook_note,
    )
    cand_text = json.dumps(
        [{"id": c.id, "node": c.outline_node_id, "in": c.src_in, "out": c.src_out, "reason": c.reason}
         for c in candidates],
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": prompt.render(
            target_seconds=int(ctx.config.video.target_seconds),
            narration_language=ctx.config.general.narration_language,
            style=ctx.config.video.style,
            style_guide=style_block,
            fixed_prefix=fixed_prefix,
            outline="{outline}", vision="{vision}", candidates="{candidates}",
        )},
        {"role": "user", "content": (
            f"剧情大纲：{json.dumps(outline.model_dump(), ensure_ascii=False)}\n\n"
            f"画面理解：{vision_text}\n\n"
            f"可用画面区间：{cand_text}"
        )[:14000]},
    ]
    result, model = ctx.caller.call(
        "writer", messages, WriterOutput,
        prompt_name="writer", prompt_version=prompt.version, prompt_hash=prompt.hash,
    )
    assert isinstance(result, WriterOutput)
    timeline = _build_timeline(ctx, result)
    save_json_artifact(ctx.workdir, "07_timeline.json", timeline)
    ctx.artifacts["timeline"] = timeline
    logger.info("write: %d 段（模型 %s）", len(timeline.segments), model)

    # review 配置为 true 时暂停等待人工确认（默认全自动跳过）
    if ctx.config.video.review:
        save_json_artifact(ctx.workdir, "07_awaiting_review.flag", {"message": "等待人工确认时间轴"})
        logger.info("review=true：时间轴已生成，等待人工确认后继续")

    return {"segments": len(timeline.segments), "title": timeline.title}


def _mem(ctx: StageContext) -> str:
    p = ctx.series_memory_path()
    return p.read_text(encoding="utf-8") if p and p.exists() else ""


# ---------------------------------------------------------------------------
# review（best 模式自审）
# ---------------------------------------------------------------------------


class ReviewIssue(BaseModel):
    segment_id: str = ""
    problem: str = ""
    suggestion: str = ""


class ReviewOutput(BaseModel):
    passed: bool = Field(default=True, alias="pass")
    issues: list[ReviewIssue] = Field(default_factory=list)
    improved_timeline: WriterOutput | None = None
    scores: dict[str, float] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


def stage_review(ctx: StageContext) -> dict:
    if ctx.profile.review_rounds <= 0:
        logger.info("review_rounds=0，跳过自审")
        return {"skipped": True}
    timeline: Timeline | None = ctx.artifacts.get("timeline")
    if timeline is None:
        data = load_json_artifact(ctx.workdir, "07_timeline.json")
        timeline = Timeline.model_validate(data) if data else None
    if timeline is None:
        return {"skipped": True}

    prompt = load_prompt("reviewer", ctx.config.prompts.custom_dir)
    messages = [
        {"role": "system", "content": prompt.render(timeline="{timeline}")},
        {"role": "user", "content": timeline.model_dump_json()},
    ]
    try:
        result, model = ctx.caller.call(
            "reviewer", messages, ReviewOutput,
            prompt_name="reviewer", prompt_version=prompt.version, prompt_hash=prompt.hash,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("reviewer 失败，沿用原时间轴: %s", e)
        return {"skipped": True, "reason": str(e)}

    final = timeline
    if result.improved_timeline and not result.passed:
        final = _build_timeline(ctx, result.improved_timeline)
        logger.info("reviewer 改稿：%d 个问题", len(result.issues))
    save_json_artifact(ctx.workdir, "07b_timeline_reviewed.json", final)
    save_json_artifact(ctx.workdir, "07b_review.json", result)
    ctx.artifacts["timeline"] = final
    logger.info("review: pass=%s scores=%s（模型 %s）", result.passed, result.scores, model)
    return {"pass": result.passed, "issues": len(result.issues), "scores": result.scores}
