"""pytest 共享夹具：ffmpeg 定位、测试素材生成、mock 配置。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "tests" / "assets"

# 预下载的静态 ffmpeg（受限环境无系统 ffmpeg）
HOST_FFMPEG_DIR = Path.home() / "tools" / "ffmpeg"


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    cand = HOST_FFMPEG_DIR / name
    return str(cand) if cand.is_file() else None


@pytest.fixture(scope="session")
def ffmpeg_dir() -> Path:
    """返回含 ffmpeg/ffprobe 的目录，并注入 override。"""
    ff = _which("ffmpeg")
    fp = _which("ffprobe")
    assert ff and fp, "需要 ffmpeg/ffprobe（系统 PATH 或 ~/tools/ffmpeg）"
    d = Path(ff).parent
    from autocut.utils import set_ffmpeg_override_dir

    set_ffmpeg_override_dir(d)
    return d


@pytest.fixture(scope="session")
def assets(ffmpeg_dir, tmp_path_factory) -> Path:
    """生成全套测试素材（session 级，一次）。"""
    out = tmp_path_factory.mktemp("assets")
    env = os.environ.copy()
    env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{env.get('PATH', '')}"
    script = ROOT / "scripts" / "make_test_media.py"
    subprocess.run([sys.executable, str(script), str(out)], check=True, env=env)
    return out


@pytest.fixture()
def test_video(assets, tmp_path, monkeypatch) -> Path:
    """把 60s 测试视频复制到 tmp_path，并 chdir 到隔离目录。"""
    monkeypatch.chdir(tmp_path)
    dst = tmp_path / "test.mp4"
    shutil.copy(assets / "test_60s.mp4", dst)
    return dst


@pytest.fixture()
def mock_config(tmp_path):
    """全 mock 配置：注册 mock 模型 + 所有角色指向 mock。"""
    from autocut.config import Config, ModelSpec, Roles

    models = [
        ModelSpec(name="mock-text", protocol="mock", capabilities=["text", "json_mode"], price_in=0.1, price_out=0.2),
        ModelSpec(
            name="mock-vision",
            protocol="mock",
            capabilities=["text", "vision", "video", "json_mode"],
            price_in=0.1,
            price_out=0.2,
        ),
    ]
    roles = Roles(**{r: ["mock-text"] for r in Roles.model_fields})
    roles.vision = ["mock-vision"]
    cfg = Config(
        models=models,
        roles=roles,
        providers=_mock_providers(),
    )
    cfg.general.work_dir = tmp_path / "work"
    cfg.general.output_dir = tmp_path / "output"
    cfg.general.series_dir = tmp_path / "series"
    return cfg


def _mock_providers():
    from autocut.config import ProvidersConfig

    return ProvidersConfig(
        subtitle="mock",
        shots="mock",
        loudness="mock",
        hardsub="mock",
        tts="mock",
        renderer="ffmpeg_mp4",  # 渲染用真实 ffmpeg
    )
