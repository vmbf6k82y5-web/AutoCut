"""通用工具：ffmpeg 定位、子进程、路径、SRT 解析、时间格式化。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .logging_utils import sanitize

logger = logging.getLogger("autocut.utils")

#: 测试/受限环境下可注入的 ffmpeg 目录（优先级最高）
_FFMPEG_OVERRIDE_DIR: Path | None = None


def set_ffmpeg_override_dir(p: Path | None) -> None:
    global _FFMPEG_OVERRIDE_DIR
    _FFMPEG_OVERRIDE_DIR = p


def find_executable(name: str, configured_path: str = "") -> Path:
    """按顺序查找可执行文件：配置路径 → override 目录 → ./bin → PATH。"""
    exe = name + (".exe" if sys.platform == "win32" else "")
    candidates: list[Path] = []
    if configured_path:
        cp = Path(configured_path)
        candidates.append(cp)
        if cp.is_dir():
            candidates.append(cp / exe)
    if _FFMPEG_OVERRIDE_DIR:
        candidates.append(_FFMPEG_OVERRIDE_DIR / exe)
    candidates.append(Path("bin") / exe)
    candidates.append(Path(__file__).resolve().parent.parent.parent / "bin" / exe)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c.resolve()
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise FileNotFoundError(
        f"未找到 {name}。请安装 ffmpeg，或在配置 [general] ffmpeg_path 中指定路径，"
        f"或放到项目 ./bin 目录。首次启动可自动下载（见 README）。"
    )


def find_ffmpeg(configured_path: str = "") -> Path:
    return find_executable("ffmpeg", configured_path)


def find_ffprobe(configured_path: str = "") -> Path:
    return find_executable("ffprobe", configured_path)


def run_cmd(
    args: list[str | Path],
    *,
    capture: bool = True,
    check: bool = True,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    """运行外部命令，日志脱敏，返回结果。"""
    str_args = [str(a) for a in args]
    logger.debug("执行命令: %s", sanitize(" ".join(str_args)))
    proc = subprocess.run(
        str_args,
        capture_output=capture,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    if check and proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise RuntimeError(f"命令失败({proc.returncode}): {sanitize(str_args[0])}\n{sanitize(tail)}")
    return proc


def ffmpeg_filter_path(path: str | Path) -> str:
    """把路径转成能安全放进 ffmpeg 滤镜参数的写法。

    滤镜参数里反斜杠是转义符、冒号是 key=value 分隔符：Windows 路径直接拼进
    `subtitles=` 会被吞掉反斜杠（tests\\assets\\x.srt → testsassetsx.srt，报
    "Unable to open"）。这里统一转正斜杠并转义冒号（盘符）；调用处还要用单引号
    把整个值包起来，否则 `\\:` 仍会被当成参数分隔（实测三种写法只有这种能过）。
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def ffprobe_json(video: Path, ffprobe: Path, extra: list[str] | None = None) -> dict:
    """ffprobe 输出 JSON。"""
    import json

    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        *(extra or []),
        str(video),
    ]
    proc = run_cmd(args, timeout=120)
    return json.loads(proc.stdout)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def safe_work_name(video_path: Path) -> str:
    """由视频文件名生成工作目录名，支持中文与空格。"""
    stem = video_path.stem.strip() or "video"
    # 去掉文件系统不友好字符，保留中文
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    return cleaned[:80]


def format_srt_time(seconds: float) -> str:
    """秒 → SRT 时间戳 00:00:00,000"""
    seconds = max(0.0, seconds)
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_time(ts: str) -> float:
    """SRT 时间戳 → 秒"""
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        raise ValueError(f"非法 SRT 时间: {ts!r}")
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mi * 60 + s + ms / 1000.0


class SRTEntry:
    __slots__ = ("index", "start", "end", "text")

    def __init__(self, index: int, start: float, end: float, text: str) -> None:
        self.index, self.start, self.end, self.text = index, start, end, text

    def __repr__(self) -> str:  # pragma: no cover
        return f"SRTEntry({self.index}, {self.start:.2f}-{self.end:.2f}, {self.text!r})"


_SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)\s*\n((?:.+\n?)+?)(?=\n\n|\Z)",
    re.M,
)


def parse_srt(path: Path) -> list[SRTEntry]:
    """解析 SRT 文件。"""
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    entries: list[SRTEntry] = []
    for m in _SRT_BLOCK_RE.finditer(content):
        entries.append(
            SRTEntry(
                index=int(m.group(1)),
                start=parse_srt_time(m.group(2)),
                end=parse_srt_time(m.group(3)),
                text=m.group(4).strip(),
            )
        )
    return entries


def write_srt(entries: list[SRTEntry], path: Path) -> Path:
    """写 SRT 文件。"""
    lines: list[str] = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{format_srt_time(e.start)} --> {format_srt_time(e.end)}")
        lines.append(e.text)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def text_hash_cache_key(*parts: object) -> str:
    """多个部分拼成稳定缓存键。"""
    joined = "\x00".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
