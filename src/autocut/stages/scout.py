"""scout 阶段：为每个剧情节点初筛画面候选段 → 04_candidates.json。

优先用 LLM scout；失败或关闭时用纯规则（响度峰值 + 台词密度）兜底。
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from ..contracts import Candidate, HighEnergyRange, Outline, Shot
from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from ..prompt import build_cached_prefix, load_prompt
from ..utils import parse_srt

logger = logging.getLogger("autocut.stages.scout")


class CandidateOut(BaseModel):
    id: str = "c1"
    outline_node_id: str | None = None
    src_in: float = 0.0
    src_out: float = 5.0
    reason: str = ""


class ScoutOutput(BaseModel):
    candidates: list[CandidateOut] = Field(default_factory=list)


def _rule_scout(
    outline: Outline, loudness: list[HighEnergyRange], shots: list[Shot], srt_entries, max_n: int
) -> list[Candidate]:
    """纯规则兜底：按响度峰值 + 台词密度给镜头打分，归入节点。"""
    # 台词密度：每秒字幕字数
    def dialogue_density(start: float, end: float) -> float:
        chars = sum(len(e.text) for e in srt_entries if e.start < end and e.end > start)
        return chars / max(0.1, end - start)

    def loudness_score(start: float, end: float) -> float:
        scores = [r.score for r in loudness if r.start < end and r.end > start]
        return max(scores) if scores else 0.0

    cands: list[Candidate] = []
    cid = 0
    # 每个高能区间 → 候选，吸附到镜头边界
    for r in loudness:
        # 找覆盖该区间中点的镜头
        mid = (r.start + r.end) / 2
        shot = next((s for s in shots if s.start <= mid <= s.end), None)
        if shot is None:
            continue
        src_in, src_out = shot.start, min(shot.end, shot.start + 10.0)
        score = 0.6 * r.score + 0.4 * min(1.0, dialogue_density(src_in, src_out) / 5.0)
        # 归入节点（按时间）
        node = next((n for n in outline.nodes if n.time_start <= mid <= n.time_end), None)
        cid += 1
        cands.append(Candidate(
            id=f"c{cid}", outline_node_id=node.id if node else None,
            src_in=src_in, src_out=src_out, score=score,
            reason=f"规则:响度{r.score:.2f}+台词{dialogue_density(src_in, src_out):.1f}",
            has_dialogue=dialogue_density(src_in, src_out) > 0,
        ))
    # 补足：每个节点至少一个候选
    for node in outline.nodes:
        if not any(c.outline_node_id == node.id for c in cands):
            mid = (node.time_start + node.time_end) / 2
            shot = next((s for s in shots if s.start <= mid <= s.end), None)
            if shot:
                cid += 1
                cands.append(Candidate(
                    id=f"c{cid}", outline_node_id=node.id,
                    src_in=shot.start, src_out=min(shot.end, shot.start + 8.0),
                    score=0.3, reason="规则:节点补位", has_dialogue=True,
                ))
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands[:max_n]


def stage_scout(ctx: StageContext) -> dict:
    outline: Outline | None = ctx.artifacts.get("outline")
    if outline is None:
        data = load_json_artifact(ctx.workdir, "03b_outline.json")
        outline = Outline.model_validate(data) if data else Outline()
    loudness = ctx.artifacts.get("loudness") or [
        HighEnergyRange.model_validate(x) for x in (load_json_artifact(ctx.workdir, "03_loudness.json") or [])
    ]
    shots = ctx.artifacts.get("shots") or [
        Shot.model_validate(x) for x in (load_json_artifact(ctx.workdir, "02_shots.json") or [])
    ]
    srt = ctx.workdir / "01c_subtitle_fixed.srt"
    srt_entries = parse_srt(srt) if srt.exists() else []
    max_n = ctx.profile.scout.candidates

    candidates: list[Candidate] = []
    used_model = "rule"
    # 优先 LLM scout
    try:
        prompt = load_prompt("scout", ctx.config.prompts.custom_dir)
        fixed_prefix = build_cached_prefix("你正在为影视解说挑选画面候选段。")
        messages = [
            {"role": "system", "content": prompt.render(
                fixed_prefix=fixed_prefix, outline="{outline}", loudness="{loudness}",
                cues="{cues}", max_candidates=max_n)},
            {"role": "user", "content": json.dumps({
                "nodes": [n.model_dump() for n in outline.nodes],
                "loudness": [r.model_dump() for r in loudness[:20]],
                "dialogue_count": len(srt_entries),
            }, ensure_ascii=False)[:6000]},
        ]
        result, model = ctx.caller.call(
            "scout", messages, ScoutOutput,
            prompt_name="scout", prompt_version=prompt.version, prompt_hash=prompt.hash,
        )
        assert isinstance(result, ScoutOutput)
        candidates = [
            Candidate(id=c.id, outline_node_id=c.outline_node_id, src_in=c.src_in, src_out=c.src_out,
                      reason=c.reason, has_dialogue=True)
            for c in result.candidates if c.src_out > c.src_in
        ]
        used_model = model
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM scout 失败(%r)，回退规则 scout", e)

    if not candidates and ctx.config.providers.scout_rule:
        candidates = _rule_scout(outline, loudness, shots, srt_entries, max_n)
        used_model = "rule"

    save_json_artifact(ctx.workdir, "04_candidates.json", [c.model_dump() for c in candidates])
    ctx.artifacts["candidates"] = candidates
    logger.info("scout: %d 个候选段（%s）", len(candidates), used_model)
    return {"candidates": len(candidates), "by": used_model}
