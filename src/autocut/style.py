"""风格学习：从范例文稿提炼 style_guide.md，按范例哈希缓存。"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel

from .prompt import load_prompt
from .utils import sha256_text

logger = logging.getLogger("autocut.style")


class StyleOutput(BaseModel):
    style_guide: str = ""


def get_style_guide(ctx, examples_dir: Path | None) -> tuple[str, list[str]]:
    """返回 (style_guide 文本, 范例节选列表)。无范例时返回空。"""
    if not examples_dir or not Path(examples_dir).exists():
        return "", []
    examples_dir = Path(examples_dir)
    files = sorted(examples_dir.glob("*.txt")) + sorted(examples_dir.glob("*.md"))
    if not files:
        return "", []
    contents = [f.read_text(encoding="utf-8") for f in files]
    # 按范例哈希缓存
    cache_key = sha256_text("".join(contents))[:16]
    cache_file = ctx.workdir / "style_guide_cache.json"
    if cache_file.exists():
        import json

        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("key") == cache_key:
            logger.info("风格指南命中缓存")
            return cached["guide"], contents[:2]

    prompt = load_prompt("style", ctx.config.prompts.custom_dir)
    examples_text = "\n\n---范例---\n\n".join(contents)
    messages = [
        {"role": "system", "content": prompt.render(examples="{examples}")},
        {"role": "user", "content": examples_text[:10000]},
    ]
    try:
        result, model = ctx.caller.call(
            "style", messages, StyleOutput,
            prompt_name="style", prompt_version=prompt.version, prompt_hash=prompt.hash,
        )
        guide = result.style_guide
        import json

        cache_file.write_text(json.dumps({"key": cache_key, "guide": guide}, ensure_ascii=False), encoding="utf-8")
        logger.info("提炼风格指南（模型 %s，%d 字）", model, len(guide))
        return guide, contents[:2]
    except Exception as e:  # noqa: BLE001
        logger.warning("风格提炼失败: %s", e)
        return "", contents[:2]
