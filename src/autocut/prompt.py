"""提示词管理：从 src/autocut/prompts/*.md 加载，支持 custom_dir 覆盖，版本化与哈希。

提示词文件头部以 YAML front-matter 携带 version：
    ---
    version: 1
    role: writer
    ---
    正文...
固定前缀（系统提示词、风格指南、人物表、剧集记忆）与变化部分分离，便于上下文缓存。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .utils import sha256_text

logger = logging.getLogger("autocut.prompt")

_BUILTIN_DIR = Path(__file__).resolve().parent / "prompts"

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


@dataclass
class PromptTemplate:
    name: str
    version: int
    role: str
    body: str  # 含 {placeholder} 的模板
    source_path: Path
    hash: str  # body 的哈希（用于 manifest 与缓存键）

    def render(self, **kwargs) -> str:
        """渲染模板，缺失的 placeholder 留空字符串。"""
        body = self.body
        for k, v in kwargs.items():
            body = body.replace("{" + k + "}", str(v))
        # 清理未提供的占位符为提示，避免 KeyError
        return body


def _parse_front_matter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def load_prompt(name: str, custom_dir: Path | None = None) -> PromptTemplate:
    """按名加载提示词（custom_dir 优先于内置）。"""
    candidates = []
    if custom_dir:
        candidates.append(Path(custom_dir) / f"{name}.md")
    candidates.append(_BUILTIN_DIR / f"{name}.md")
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_front_matter(text)
            version = int(meta.get("version", "1"))
            role = meta.get("role", name)
            return PromptTemplate(
                name=name,
                version=version,
                role=role,
                body=body,
                source_path=path,
                hash=sha256_text(body)[:12],
            )
    raise FileNotFoundError(f"提示词不存在: {name}（查找: {[str(c) for c in candidates]}）")


def build_cached_prefix(*parts: str) -> str:
    """把固定前缀各部分按字节级一致拼接（用于上下文缓存友好）。"""
    return "\n\n".join(p for p in parts if p)
