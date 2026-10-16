"""frames / vision 阶段：抽帧或切短视频，然后画面理解。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from ..contracts import Candidate, Shot
from ..pipeline import StageContext, load_json_artifact, save_json_artifact
from ..prompt import load_prompt
from ..utils import find_ffmpeg, run_cmd

logger = logging.getLogger("autocut.stages.frames")


# ---------------------------------------------------------------------------
# frames：抽帧（economy/balanced）或切 360p 短视频（best）
# ---------------------------------------------------------------------------


def _shot_boundaries_in(shots: list[Shot], start: float, end: float) -> list[float]:
    return [s.start for s in shots if start < s.start < end]


def _extract_frame(ffmpeg: Path, video: Path, t: float, out: Path, width: int = 512) -> bool:
    try:
        run_cmd(
            [ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
             "-vf", f"scale={width}:-2", str(out)],
            timeout=60,
        )
        return out.exists()
    except Exception as e:  # noqa: BLE001
        logger.debug("抽帧失败 t=%.1f: %s", t, e)
        return False


def _cut_clip(ffmpeg: Path, video: Path, start: float, end: float, out: Path) -> bool:
    try:
        run_cmd(
            [ffmpeg, "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", video,
             "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
             "-c:a", "aac", "-b:a", "64k", str(out)],
            timeout=120,
        )
        return out.exists()
    except Exception as e:  # noqa: BLE001
        logger.debug("切段失败 %.1f-%.1f: %s", start, end, e)
        return False


def stage_frames(ctx: StageContext) -> dict:
    candidates: list[Candidate] = ctx.artifacts.get("candidates") or [
        Candidate.model_validate(x) for x in (load_json_artifact(ctx.workdir, "04_candidates.json") or [])
    ]
    shots: list[Shot] = ctx.artifacts.get("shots") or [
        Shot.model_validate(x) for x in (load_json_artifact(ctx.workdir, "02_shots.json") or [])
    ]
    ffmpeg = find_ffmpeg(ctx.config.general.ffmpeg_path)
    vision_mode = ctx.profile.vision_mode
    max_workers = max(1, ctx.config.general.ffmpeg_concurrency)

    if vision_mode == "video":
        # best：候选段 360p 短视频
        clips_dir = ctx.workdir / "05_clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        def cut(c: Candidate) -> tuple[str, Path | None]:
            out = clips_dir / f"{c.id}.mp4"
            return c.id, (out if _cut_clip(ffmpeg, ctx.video, c.src_in, c.src_out, out) else None)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = dict(ex.map(cut, candidates))
        ctx.artifacts["clips"] = results
        n = sum(1 for v in results.values() if v)
        logger.info("clips: %d/%d 段短视频", n, len(candidates))
        return {"clips": n, "mode": "video"}

    # frames：抽帧（候选段按镜头边界 2–5 帧）
    frames_dir = ctx.workdir / "05_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    def grab(c: Candidate) -> tuple[str, list[Path]]:
        # 镜头边界采样 2-5 帧
        boundaries = _shot_boundaries_in(shots, c.src_in, c.src_out)
        times = [c.src_in + 0.2, *boundaries[:3], c.src_out - 0.2]
        times = sorted({max(c.src_in, min(t, c.src_out - 0.05)) for t in times})[:5]
        paths: list[Path] = []
        for i, t in enumerate(times):
            out = frames_dir / f"{c.id}_{i}.jpg"
            if _extract_frame(ffmpeg, ctx.video, t, out):
                paths.append(out)
        return c.id, paths

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = dict(ex.map(grab, candidates))
    ctx.artifacts["frames"] = results
    n = sum(len(v) for v in results.values())
    logger.info("frames: %d 帧 / %d 候选段", n, len(candidates))
    return {"frames": n, "mode": "frames"}


# ---------------------------------------------------------------------------
# vision：画面理解
# ---------------------------------------------------------------------------


class VisionDesc(BaseModel):
    candidate_id: str = ""
    summary: str = ""
    usable: bool = True


class VisionOutput(BaseModel):
    descriptions: list[VisionDesc] = Field(default_factory=list)


def stage_vision(ctx: StageContext) -> dict:
    if not ctx.config.providers.vision_none_ok and not ctx.roles.get("vision"):
        raise RuntimeError("vision 角色未配置且不允许跳过")
    if not ctx.roles.get("vision"):
        logger.info("vision 角色未配置，跳过画面理解")
        save_json_artifact(ctx.workdir, "06_vision.json", {"descriptions": [], "skipped": True})
        return {"skipped": True}

    candidates: list[Candidate] = ctx.artifacts.get("candidates") or [
        Candidate.model_validate(x) for x in (load_json_artifact(ctx.workdir, "04_candidates.json") or [])
    ]
    prompt = load_prompt("vision", ctx.config.prompts.custom_dir)
    vision_mode = ctx.profile.vision_mode
    descs: list[VisionDesc] = []

    if vision_mode == "video":
        # 每段视频单独调用（gemini_native）
        clips = ctx.artifacts.get("clips") or {}
        for c in candidates:
            clip = clips.get(c.id)
            if not clip:
                continue
            messages = [
                {"role": "system", "content": prompt.render(fixed_prefix="", candidates="{candidates}")},
                {"role": "user", "content": f"描述候选段 {c.id}（{c.src_in:.1f}-{c.src_out:.1f}s）的画面内容，判断 usable。"},
            ]
            try:
                result, _ = ctx.caller.call(
                    "vision", messages, VisionOutput, video=clip,
                    prompt_name="vision", prompt_version=prompt.version, prompt_hash=prompt.hash,
                )
                for d in result.descriptions:
                    d.candidate_id = d.candidate_id or c.id
                    descs.append(d)
            except Exception as e:  # noqa: BLE001
                logger.warning("vision 段 %s 失败: %s", c.id, e)
    else:
        # 抽帧批量：把所有帧一次性给 vision 模型
        frames_map = ctx.artifacts.get("frames") or {}
        all_frames: list[Path] = []
        cand_desc = []
        for c in candidates:
            imgs = frames_map.get(c.id, [])
            all_frames.extend(imgs)
            cand_desc.append({"id": c.id, "node": c.outline_node_id, "frames": len(imgs)})
        messages = [
            {"role": "system", "content": prompt.render(fixed_prefix="", candidates="{candidates}")},
            {"role": "user", "content": f"候选段列表：{cand_desc}\n按候选段顺序给出画面描述与 usable 判断。"},
        ]
        try:
            result, model = ctx.caller.call(
                "vision", messages, VisionOutput, images=all_frames[:60],
                prompt_name="vision", prompt_version=prompt.version, prompt_hash=prompt.hash,
            )
            descs = result.descriptions
        except Exception as e:  # noqa: BLE001
            logger.warning("vision 批量失败: %s", e)

    save_json_artifact(ctx.workdir, "06_vision.json", {
        "descriptions": [d.model_dump() for d in descs], "mode": vision_mode,
    })
    ctx.artifacts["vision"] = descs
    logger.info("vision: %d 条画面描述", len(descs))
    return {"descriptions": len(descs)}
