"""metadata 阶段：发布物料（封面、标题、简介、标签）→ 12_publish/。M7 完善封面图。"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from ..prompt import load_prompt

logger = logging.getLogger("autocut.stages.metadata")


class MetadataOutput(BaseModel):
    titles: list[str] = Field(default_factory=list)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


def stage_metadata(ctx: StageContext) -> dict:
    publish = ctx.workdir / "12_publish"
    publish.mkdir(parents=True, exist_ok=True)

    timeline = ctx.artifacts.get("timeline")
    title = timeline.title if timeline else ""
    outline = load_json_artifact(ctx.workdir, "03b_outline.json") or {}
    content = f"标题:{title}\n大纲:{outline.get('logline','')}\n文案:{_narration_text(timeline)}"

    meta = MetadataOutput(titles=[title or "影视解说"], description="", tags=[])
    if ctx.roles.get("metadata"):
        try:
            prompt = load_prompt("metadata", ctx.config.prompts.custom_dir)
            result, model = ctx.caller.call(
                "metadata",
                [{"role": "system", "content": prompt.render(content="{content}")},
                 {"role": "user", "content": content[:4000]}],
                MetadataOutput,
                prompt_name="metadata", prompt_version=prompt.version, prompt_hash=prompt.hash,
            )
            meta = result
        except Exception as e:  # noqa: BLE001
            logger.warning("metadata 生成失败: %s", e)

    save_json_artifact(publish, "publish.json", meta)

    # 封面图：高潮节点最佳帧 + 大字标题
    from ..contracts import Outline
    from ..cover import make_cover

    outline_obj = None
    if outline:
        try:
            outline_obj = Outline.model_validate(outline)
        except Exception:  # noqa: BLE001
            outline_obj = None
    cover_title = meta.titles[0] if meta.titles else (title or "影视解说")
    cover = make_cover(ctx.video, cover_title, outline_obj, ctx.workdir, ctx.config, publish / "cover.jpg")
    if cover:
        logger.info("封面: %s", cover)

    ctx.artifacts["metadata"] = meta
    logger.info("metadata: %d 个备选标题", len(meta.titles))
    return {"titles": len(meta.titles), "cover": bool(cover)}


def _narration_text(timeline) -> str:
    if not timeline:
        return ""
    return " ".join(s.text for s in timeline.segments if s.type == "narration")[:2000]
