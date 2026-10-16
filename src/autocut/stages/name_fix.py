"""name_fix 阶段：人名纠错 + 说话人映射 → 01c_subtitle_fixed.srt。"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..pipeline import StageContext, save_json_artifact
from ..prompt import build_cached_prefix, load_prompt
from ..utils import SRTEntry, parse_srt, write_srt

logger = logging.getLogger("autocut.stages.name_fix")


class Correction(BaseModel):
    line_index: int = 0
    before: str = ""
    after: str = ""


class NameFixOutput(BaseModel):
    corrected: list[Correction] = Field(default_factory=list)
    speaker_map: dict[str, str] = Field(default_factory=dict)


def _load_series_memory(ctx: StageContext) -> str:
    p = ctx.series_memory_path()
    if p and p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def stage_name_fix(ctx: StageContext) -> dict:
    srt = ctx.workdir / "01_subtitle.srt"
    if not srt.exists():
        logger.warning("字幕不存在，跳过 name_fix")
        return {"skipped": True}
    entries = parse_srt(srt)
    subtitle_text = "\n".join(f"{e.index}\t{e.text}" for e in entries)

    prompt = load_prompt("name_fix", ctx.config.prompts.custom_dir)
    speakers = ctx.artifacts.get("speakers") or {}
    fixed_prefix = build_cached_prefix(
        "你正在处理一部影视剧的字幕纠错。",
        f"已知人物表/剧集记忆：\n{_load_series_memory(ctx)}" if _load_series_memory(ctx) else "",
        f"ASR 说话人：{speakers}" if speakers else "",
    )
    messages = [
        {"role": "system", "content": prompt.render(fixed_prefix=fixed_prefix, subtitle="{subtitle}")},
        {"role": "user", "content": subtitle_text[:8000]},
    ]
    # 用 schema 校验
    result, model = ctx.caller.call(
        "name_fix", messages, NameFixOutput,
        prompt_name="name_fix", prompt_version=prompt.version, prompt_hash=prompt.hash,
    )
    assert isinstance(result, NameFixOutput)

    # 应用纠错
    fixed = 0
    for c in result.corrected:
        for e in entries:
            if e.index == c.line_index and c.before and c.before in e.text:
                e.text = e.text.replace(c.before, c.after)
                fixed += 1

    # 说话人 → 角色名映射，前缀到字幕
    speaker_map = result.speaker_map or {}
    # 读取说话人标注（若 ASR 阶段产生 per-line speaker）
    line_speakers = ctx.artifacts.get("line_speakers") or {}
    out_entries: list[SRTEntry] = []
    for e in entries:
        text = e.text
        spk = line_speakers.get(e.index)
        char = speaker_map.get(spk) if spk else None
        if char:
            text = f"[{char}] {text}"
        out_entries.append(SRTEntry(e.index, e.start, e.end, text))

    out = ctx.workdir / "01c_subtitle_fixed.srt"
    write_srt(out_entries, out)
    save_json_artifact(ctx.workdir, "01c_speaker_map.json", speaker_map)
    ctx.artifacts["speaker_map"] = speaker_map
    ctx.artifacts["fixed_srt"] = out
    logger.info("name_fix: 纠错 %d 处，说话人映射 %d 个（模型 %s）", fixed, len(speaker_map), model)
    return {"corrections": fixed, "speakers": len(speaker_map)}
