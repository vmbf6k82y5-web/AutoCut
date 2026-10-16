"""M12 测试：ffmpeg 首启检测、__main__ 入口、打包配置完整性。"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocut.ffmpeg_bootstrap import _DOWNLOADS, ensure_ffmpeg
from autocut.utils import find_executable


class TestFFmpegBootstrap:
    def test_detect_existing(self, ffmpeg_dir):
        """已安装时直接返回路径。"""
        ffmpeg, ffprobe = ensure_ffmpeg(str(ffmpeg_dir))
        assert ffmpeg.exists() and ffprobe.exists()

    def test_find_in_path(self, ffmpeg_dir, monkeypatch):
        monkeypatch.setenv("PATH", str(ffmpeg_dir))
        ffmpeg = find_executable("ffmpeg")
        assert ffmpeg.name == "ffmpeg"

    def test_download_urls_defined(self):
        """各平台下载地址已定义。"""
        assert ("Windows", "AMD64") in _DOWNLOADS
        assert ("Linux", "x86_64") in _DOWNLOADS

    def test_missing_raises_without_auto_download(self, monkeypatch, tmp_path):
        """auto_download=False 且找不到时抛 FileNotFoundError。"""
        # 清空 PATH 与 override
        monkeypatch.setenv("PATH", str(tmp_path))
        import autocut.utils as utils
        from autocut.utils import set_ffmpeg_override_dir

        saved = utils._FFMPEG_OVERRIDE_DIR
        set_ffmpeg_override_dir(None)
        monkeypatch.chdir(tmp_path)
        try:
            with pytest.raises(FileNotFoundError):
                ensure_ffmpeg("", auto_download=False)
        finally:
            set_ffmpeg_override_dir(saved)  # 恢复，避免污染其他测试


class TestMainEntry:
    def test_main_module_exists(self):
        import autocut.__main__ as m

        assert hasattr(m, "main")

    def test_no_args_defaults_to_gui(self, monkeypatch):
        """无参数时 sys.argv 注入 gui。"""
        import sys

        import autocut.__main__ as m

        called = {}

        def fake_app():
            called["argv"] = list(sys.argv)

        monkeypatch.setattr("autocut.cli.app", fake_app)
        monkeypatch.setattr(sys, "argv", ["autocut"])
        # 避免真实 ffmpeg 下载
        monkeypatch.setattr("autocut.ffmpeg_bootstrap.ensure_ffmpeg", lambda **k: (Path("/x"), Path("/y")))
        m.main()
        assert called["argv"][1] == "gui"


class TestPackagingFiles:
    def test_spec_exists(self):
        root = Path(__file__).resolve().parent.parent
        assert (root / "autocut.spec").exists()
        content = (root / "autocut.spec").read_text(encoding="utf-8")
        assert "prompts" in content and "web/static" in content
        assert "binaries=[]" in content.replace(" ", "")  # 无二进制打包（ffmpeg 外置）

    def test_build_scripts_exist(self):
        root = Path(__file__).resolve().parent.parent
        assert (root / "build_exe.bat").exists()

    def test_readme_and_docs(self):
        root = Path(__file__).resolve().parent.parent
        assert (root / "README.md").exists()
        assert (root / "docs" / "PLUGINS.md").exists()
        assert (root / "docs" / "DECISIONS.md").exists()
        assert (root / "LICENSE").exists()

    def test_skill_md(self):
        root = Path(__file__).resolve().parent.parent
        skill = root / "skills" / "autocut" / "SKILL.md"
        assert skill.exists()
        assert "autocut run" in skill.read_text(encoding="utf-8")
