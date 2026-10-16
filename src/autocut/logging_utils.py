"""日志与脱敏：统一 logging filter 屏蔽 api_key、Authorization 头、URL 中的 key 参数。"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

# 常见敏感模式
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer xxx / Token xxx
    (re.compile(r"(Authorization['\":\s]+(?:Bearer|Token|Basic)\s+)[^\s'\"]+", re.I), r"\1***"),
    # api_key=xxx / apikey=xxx / key=xxx（JSON、查询串、键值对）
    (re.compile(r"((?:api[-_]?key|apikey|access[-_]?token|secret)['\"\s:=]+)[^\s'\"&,}]+", re.I), r"\1***"),
    # URL 查询参数中的 key/token
    (re.compile(r"([?&](?:key|token|api_key|apikey|access_token)=)[^\s&'\"]+", re.I), r"\1***"),
    # sk- 开头的 OpenAI 风格密钥
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "sk-***"),
]

#: 运行期注册的额外敏感串（如从配置读到的真实 api_key）
_registered_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """把一个真实密钥注册进脱敏列表（长度 < 6 的忽略，避免误伤）。"""
    if value and len(value) >= 6:
        _registered_secrets.add(value)


def clear_registered_secrets() -> None:
    _registered_secrets.clear()


def sanitize(text: Any) -> str:
    """对任意文本做脱敏，返回安全字符串。"""
    s = text if isinstance(text, str) else str(text)
    for secret in _registered_secrets:
        if secret in s:
            s = s.replace(secret, "***")
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


class SanitizeFilter(logging.Filter):
    """logging Filter：对 record.msg 与 args 脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.msg = sanitize(record.getMessage())
                record.args = ()
            elif isinstance(record.msg, str):
                record.msg = sanitize(record.msg)
        except Exception:  # noqa: BLE001 — 脱敏绝不能弄崩日志
            pass
        return True


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """初始化 root logger：控制台 + 可选文件，全部挂脱敏 filter。"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 避免重复添加 handler（重复初始化时先清理）
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console.addFilter(SanitizeFilter())
    root.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(SanitizeFilter())
        root.addHandler(fh)
    return root
