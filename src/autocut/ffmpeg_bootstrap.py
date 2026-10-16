"""ffmpeg 首启检测与自动下载。

顺序：配置 ffmpeg_path → ./bin → PATH → 自动下载到 ./bin。
不打包进 exe；缺失时按平台下载静态构建。
"""

from __future__ import annotations

import logging
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger("autocut.ffmpeg_bootstrap")

# 各平台静态构建下载地址（BtbN/gyan/johnvansickle）
_DOWNLOADS = {
    ("Windows", "AMD64"): "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    ("Linux", "x86_64"): "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
    ("Darwin", "arm64"): "https://www.osxexperts.net/ffmpeg7arm.zip",
    ("Darwin", "x86_64"): "https://www.osxexperts.net/ffmpeg7intel.zip",
}


def _bin_dir() -> Path:
    d = Path("bin")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_in(path: Path, name: str) -> Path | None:
    exe = name + (".exe" if sys.platform == "win32" else "")
    p = path / exe
    return p if p.is_file() else None


def ensure_ffmpeg(configured_path: str = "", auto_download: bool = True) -> tuple[Path, Path]:
    """确保 ffmpeg/ffprobe 可用，返回 (ffmpeg, ffprobe)。

    缺失且 auto_download=True 时自动下载到 ./bin。
    """
    from .utils import find_executable

    # 1. 已存在则直接用
    try:
        return find_executable("ffmpeg", configured_path), find_executable("ffprobe", configured_path)
    except FileNotFoundError:
        if not auto_download:
            raise
    # 2. 自动下载
    logger.info("未检测到 ffmpeg，开始自动下载到 ./bin ...")
    _download_ffmpeg(_bin_dir())
    return find_executable("ffmpeg", str(_bin_dir())), find_executable("ffprobe", str(_bin_dir()))


def _download_ffmpeg(dest: Path) -> None:
    system = platform.system()
    machine = platform.machine()
    url = _DOWNLOADS.get((system, machine)) or _DOWNLOADS.get((system, "x86_64"))
    if url is None:
        raise RuntimeError(f"不支持的平台 {system}/{machine}，请手动安装 ffmpeg 并配置 [general] ffmpeg_path")
    archive = dest / Path(url).name
    logger.info("下载 %s", url)
    urllib.request.urlretrieve(url, archive)  # noqa: S310
    logger.info("解压到 %s", dest)
    _extract(archive, dest)
    archive.unlink()
    # 授权可执行
    for f in dest.rglob("*"):
        if f.name in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"):
            f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            logger.info("ffmpeg 就绪: %s", f)


def _extract(archive: Path, dest: Path) -> None:
    tmp = dest / "_extract_tmp"
    tmp.mkdir(exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    elif archive.name.endswith(".tar.xz") or archive.suffix == ".xz":
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
    # 找到 ffmpeg/ffprobe 二进制移到 dest
    for name in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"):
        for f in tmp.rglob(name):
            shutil.move(str(f), dest / name)
            break
    shutil.rmtree(tmp, ignore_errors=True)
