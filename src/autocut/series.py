"""剧集记忆 + series 命令：同一部剧共用 series/<剧名>/series.json，逐集/合集处理。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Config
from .pipeline import StageContext
from .prompt import load_prompt

logger = logging.getLogger("autocut.series")

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".flv", ".wmv", ".m4v", ".webm"}


class Character(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    relation: str = ""
    pronunciation: str = ""


class SeriesMemory(BaseModel):
    characters: list[Character] = Field(default_factory=list)
    speaker_map: dict[str, str] = Field(default_factory=dict)
    recap: str = ""  # 前情提要

    def to_prompt_text(self) -> str:
        chars = "；".join(
            f"{c.name}" + (f"(别称:{'/'.join(c.aliases)})" if c.aliases else "") + (f"[{c.relation}]" if c.relation else "")
            for c in self.characters
        )
        return f"人物表：{chars}\n前情提要：{self.recap}"


def load_series_memory(series_dir: Path) -> SeriesMemory:
    p = Path(series_dir) / "series.json"
    if p.exists():
        try:
            return SeriesMemory.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            logger.warning("剧集记忆损坏，重置: %s", e)
    return SeriesMemory()


def save_series_memory(series_dir: Path, mem: SeriesMemory) -> Path:
    series_dir = Path(series_dir)
    series_dir.mkdir(parents=True, exist_ok=True)
    p = series_dir / "series.json"
    p.write_text(mem.model_dump_json(indent=2), encoding="utf-8")
    return p


class MemoryOutput(BaseModel):
    characters: list[Character] = Field(default_factory=list)
    speaker_map: dict[str, str] = Field(default_factory=dict)
    recap: str = ""


def update_series_memory(ctx: StageContext, episode_summary: str) -> SeriesMemory:
    """由 memory 角色更新剧集记忆。"""
    series_dir = ctx.series_dir or (ctx.config.general.series_dir / ctx.video.stem)
    old = load_series_memory(series_dir)
    if not ctx.roles.get("memory"):
        save_series_memory(series_dir, old)
        return old
    prompt = load_prompt("memory", ctx.config.prompts.custom_dir)
    messages = [
        {"role": "system", "content": prompt.render(old_memory="{old_memory}", episode="{episode}")},
        {"role": "user", "content": f"旧记忆：\n{old.model_dump_json()}\n\n本集：\n{episode_summary[:6000]}"},
    ]
    try:
        result, model = ctx.caller.call(
            "memory", messages, MemoryOutput,
            prompt_name="memory", prompt_version=prompt.version, prompt_hash=prompt.hash,
        )
        new = SeriesMemory(
            characters=result.characters or old.characters,
            speaker_map={**old.speaker_map, **result.speaker_map},
            recap=result.recap or old.recap,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("memory 更新失败: %s", e)
        new = old
    save_series_memory(series_dir, new)
    logger.info("剧集记忆已更新: %s", series_dir)
    return new


def run_series(directory: Path, mode: str, config: Config, *, profile_name: str | None = None) -> None:
    """series 命令：episodes 逐集 / compilation 合集。"""
    from .runner import run_video

    directory = Path(directory)
    videos = sorted(p for p in directory.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        raise FileNotFoundError(f"{directory} 下无视频文件")
    series_dir = config.general.series_dir / directory.name
    series_dir.mkdir(parents=True, exist_ok=True)

    if mode == "episodes":
        for v in videos:
            logger.info("=== 处理 %s ===", v.name)
            # 估算并预算检查
            from .estimate import estimate_video

            est = estimate_video(v, config, profile_name)
            if not est.within_budget:
                logger.warning("预估费用 $%.4f 超预算，跳过 %s", est.total_cost, v.name)
                continue
            run_video(v, config, profile_name=profile_name, series_dir=series_dir)
            # 更新剧集记忆
            workdir = config.general.work_dir / v.stem
            ctx = StageContext(v, workdir, config, profile_name=profile_name, series_dir=series_dir)
            outline = {}
            of = workdir / "03b_outline.json"
            if of.exists():
                outline = json.loads(of.read_text(encoding="utf-8"))
            update_series_memory(ctx, outline.get("logline", "") + "\n" + json.dumps(outline.get("nodes", []), ensure_ascii=False))
    elif mode == "compilation":
        from .compilation import run_compilation

        run_compilation(directory, videos, config, profile_name=profile_name, series_dir=series_dir)
    else:
        raise ValueError(f"未知 mode: {mode}（episodes/compilation）")
