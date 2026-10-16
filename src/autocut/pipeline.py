"""流水线引擎：确定性编排各阶段，支持断点续跑。

阶段按顺序注册，每阶段声明产物文件；已完成的阶段自动跳过，
--from 可强制从某阶段重跑（同时使其后的阶段失效）。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("autocut.pipeline")

#: 阶段顺序（M6 起逐步启用全部）
STAGE_ORDER: list[str] = [
    "probe",
    "hardsub",
    "subtitle",
    "name_fix",
    "shots",
    "loudness",
    "outline",
    "scout",
    "frames",
    "vision",
    "write",
    "review",
    "tts",
    "align",
    "render",
    "qc",
    "metadata",
]


@dataclass
class StageResult:
    stage: str
    skipped: bool = False
    outputs: dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0


class StageContext:
    """阶段执行上下文：视频、工作目录、配置、已完成产物。"""

    def __init__(
        self,
        video: Path,
        workdir: Path,
        config,
        profile_name: str | None = None,
        role_overrides: dict[str, str] | None = None,
        job_id: str = "",
        series_dir: Path | None = None,
    ) -> None:
        self.video = video
        self.workdir = workdir
        self.config = config
        self.profile_name = profile_name
        self.role_overrides = role_overrides or {}
        self.job_id = job_id or safe_job_id(video)
        self.series_dir = series_dir
        self.artifacts: dict[str, Any] = {}  # 阶段间传递的对象
        self.profile = config.effective_profile(profile_name)
        self.roles = config.effective_roles(profile_name, role_overrides)
        self.sources: dict[str, Path] = {"main": video}  # 多集合集时扩充
        self._caller: Any = None  # ModelCaller，惰性创建

    @property
    def caller(self):
        """惰性创建 ModelCaller（避免无 LLM 阶段也建缓存目录）。"""
        if self._caller is None:
            from .model_client import ModelCaller

            self._caller = ModelCaller(self.config, self.roles, self.workdir, self.profile.vision_mode)
        return self._caller

    def series_memory_path(self) -> Path | None:
        """剧集记忆文件路径。"""
        if self.series_dir:
            return self.series_dir / "series.json"
        return None

    def done_marker(self, stage: str) -> Path:
        return self.workdir / ".done" / f"{stage}.done"

    def mark_done(self, stage: str) -> None:
        m = self.done_marker(stage)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(str(time.time()), encoding="utf-8")

    def is_done(self, stage: str) -> bool:
        return self.done_marker(stage).exists()

    def invalidate_from(self, stage: str) -> None:
        """删除该阶段及之后所有阶段的完成标记。"""
        try:
            idx = STAGE_ORDER.index(stage)
        except ValueError:
            raise ValueError(f"未知阶段: {stage}（可选: {STAGE_ORDER}）") from None
        for s in STAGE_ORDER[idx:]:
            m = self.done_marker(s)
            if m.exists():
                m.unlink()
                logger.info("失效阶段标记: %s", s)


def safe_job_id(video: Path) -> str:
    import hashlib

    h = hashlib.sha256(str(video.resolve()).encode()).hexdigest()[:8]
    return f"{video.stem[:20]}-{h}"


#: 阶段函数签名：fn(ctx) -> dict[str, Any]（产物摘要）
StageFn = Callable[[StageContext], dict[str, Any]]


class Pipeline:
    """确定性流水线编排器。"""

    def __init__(self) -> None:
        self._stages: dict[str, StageFn] = {}

    def register(self, name: str, fn: StageFn) -> None:
        if name not in STAGE_ORDER:
            raise ValueError(f"阶段 {name} 不在 STAGE_ORDER 中")
        self._stages[name] = fn

    def run(
        self,
        ctx: StageContext,
        from_stage: str | None = None,
        only_stages: list[str] | None = None,
    ) -> list[StageResult]:
        """按 STAGE_ORDER 执行已注册阶段。"""
        if from_stage:
            ctx.invalidate_from(from_stage)
        results: list[StageResult] = []
        for stage in STAGE_ORDER:
            if only_stages and stage not in only_stages:
                continue
            fn = self._stages.get(stage)
            if fn is None:
                continue  # 未注册的阶段跳过（里程碑未实现）
            if ctx.is_done(stage) and not only_stages:
                logger.info("[跳过] %s（已完成）", stage)
                results.append(StageResult(stage=stage, skipped=True))
                continue
            logger.info("[执行] %s", stage)
            t0 = time.time()
            outputs = fn(ctx) or {}
            ctx.mark_done(stage)
            dt = time.time() - t0
            logger.info("[完成] %s (%.1fs)", stage, dt)
            results.append(StageResult(stage=stage, outputs=outputs, duration=dt))
        return results


def build_default_pipeline() -> Pipeline:
    """构建含全部已实现阶段的流水线（逐步启用）。"""
    from ..stages import register_all_stages

    p = Pipeline()
    register_all_stages(p)
    return p


def load_json_artifact(workdir: Path, name: str) -> Any:
    path = workdir / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_artifact(workdir: Path, name: str, data: Any) -> Path:
    from pydantic import BaseModel

    path = workdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, BaseModel):
        path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
