"""manifest：记录各角色实际使用的模型、提示词版本与哈希、配置快照、代码版本、token 与费用。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .pipeline import StageContext, save_json_artifact

logger = logging.getLogger("autocut.manifest")


def write_manifest(ctx: StageContext, extra: dict[str, Any] | None = None) -> Path:
    """汇总本次运行的 manifest.json。"""
    from . import __version__

    tracker = ctx.caller.tracker if ctx._caller is not None else None
    by_role_models: dict[str, list[str]] = {}
    prompt_meta: dict[str, dict] = {}
    if tracker:
        for rec in tracker.records:
            by_role_models.setdefault(rec.role, [])
            if rec.model not in by_role_models[rec.role]:
                by_role_models[rec.role].append(rec.model)
            if rec.prompt_name:
                prompt_meta[rec.prompt_name] = {"version": rec.prompt_version, "hash": rec.prompt_hash}

    manifest = {
        "job_id": ctx.job_id,
        "video": str(ctx.video),
        "code_version": __version__,
        "profile": ctx.profile_name or ctx.config.active_profile,
        "role_models": by_role_models,
        "prompts": prompt_meta,
        "config_snapshot": ctx.config.sanitized_snapshot(),
        "usage": tracker.summary() if tracker else {},
        **(extra or {}),
    }
    path = save_json_artifact(ctx.workdir, "manifest.json", manifest)
    logger.info("manifest 已写入: %s", path)
    return path
