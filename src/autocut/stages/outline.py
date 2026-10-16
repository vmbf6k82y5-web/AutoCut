"""outline 阶段：剧情大纲（质量关键）→ 03b_outline.json。"""

from __future__ import annotations

import logging

from ..contracts import Outline
from ..pipeline import StageContext, save_json_artifact
from ..prompt import build_cached_prefix, load_prompt
from ..utils import parse_srt

logger = logging.getLogger("autocut.stages.outline")


def stage_outline(ctx: StageContext) -> dict:
    srt = ctx.artifacts.get("fixed_srt") or ctx.workdir / "01c_subtitle_fixed.srt"
    if not srt.exists():
        srt = ctx.workdir / "01_subtitle.srt"
    entries = parse_srt(srt) if srt.exists() else []
    subtitle_text = "\n".join(f"[{e.start:.1f}] {e.text}" for e in entries)

    prompt = load_prompt("outline", ctx.config.prompts.custom_dir)
    memory = ""
    p = ctx.series_memory_path()
    if p and p.exists():
        memory = p.read_text(encoding="utf-8")
    fixed_prefix = build_cached_prefix(
        "你正在梳理一部影视剧的剧情大纲。",
        f"剧集记忆/人物表：\n{memory}" if memory else "",
    )
    messages = [
        {"role": "system", "content": prompt.render(fixed_prefix=fixed_prefix, subtitle="{subtitle}")},
        {"role": "user", "content": subtitle_text[:12000]},
    ]
    result, model = ctx.caller.call(
        "outline", messages, Outline,
        prompt_name="outline", prompt_version=prompt.version, prompt_hash=prompt.hash,
    )
    assert isinstance(result, Outline)
    save_json_artifact(ctx.workdir, "03b_outline.json", result)
    ctx.artifacts["outline"] = result
    logger.info("outline: %d 个节点（模型 %s）", len(result.nodes), model)
    return {"nodes": len(result.nodes), "logline": result.logline}
