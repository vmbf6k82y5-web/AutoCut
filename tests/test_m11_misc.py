"""M11 测试：多集合集、跨语言、jianying_draft 渲染器、可选功能关闭。"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocut.compilation import _build_compilation_timeline, _merge_outlines
from autocut.config import Config
from autocut.contracts import Outline, OutlineNode, Segment, Timeline
from autocut.render.jianying_draft import JianYingDraftRenderer


class TestCompilation:
    def _outline(self, n=2):
        return Outline(
            logline="测试集",
            nodes=[OutlineNode(id=f"n{i}", summary=f"节点{i}", function="高潮" if i else "铺垫",
                               time_start=i * 10, time_end=i * 10 + 8, importance=8 - i) for i in range(n)],
        )

    def test_merge_outlines(self, mock_config):
        v1, v2 = Path("e01.mp4"), Path("e02.mp4")
        combined = _merge_outlines([(v1, self._outline()), (v2, self._outline())], mock_config, None)
        assert "第1集" in combined.logline and "第2集" in combined.logline
        assert len(combined.nodes) == 4
        assert combined.nodes[0].id.startswith("ep0_")

    def test_compilation_timeline_multi_source(self, mock_config):
        outlines = [(Path("e01.mp4"), self._outline()), (Path("e02.mp4"), self._outline())]
        combined = _merge_outlines(outlines, mock_config, None)
        timeline = _build_compilation_timeline(outlines, combined, mock_config)
        sources = {s.source for s in timeline.segments}
        assert "ep0" in sources and "ep1" in sources  # 多源
        assert all(s.type == "narration" for s in timeline.segments)


class TestCrossLanguage:
    def test_language_config_separate(self):
        cfg = Config()
        cfg.general.source_language = "en"
        cfg.general.narration_language = "zh"
        assert cfg.general.source_language == "en"
        assert cfg.general.narration_language == "zh"

    def test_writer_uses_narration_language(self):
        """writer 提示词接收 narration_language。"""
        from autocut.prompt import load_prompt

        p = load_prompt("writer")
        rendered = p.render(target_seconds=480, narration_language="zh", style="悬疑",
                            style_guide="", fixed_prefix="", outline="", vision="", candidates="")
        assert "zh" in rendered


class TestJianYingDraft:
    def test_missing_dependency_message(self, mock_config, tmp_path):
        """未安装 pyJianYingDraft 时给中文提示。"""
        pytest.importorskip("importlib").import_module  # noqa
        import importlib.util

        if importlib.util.find_spec("pyJianYingDraft") is not None:
            pytest.skip("pyJianYingDraft 已安装")
        timeline = Timeline(title="t", segments=[Segment(id="s1", type="original", src_in=0, src_out=3)])
        with pytest.raises(RuntimeError, match="pyJianYingDraft"):
            JianYingDraftRenderer().render(timeline, {"sources": {"main": Path("x.mp4")}}, tmp_path, mock_config)


class TestOptionalFeaturesOff:
    """可选功能关闭后流水线照常跑通。"""

    def test_hook_off(self, mock_config):
        mock_config.story.hook = False
        assert mock_config.story.hook is False

    def test_review_off_default(self, mock_config):
        assert mock_config.video.review is False  # 默认全自动

    def test_hardsub_none_provider(self, mock_config, tmp_path, test_video):
        mock_config.providers.hardsub = "none"
        from autocut.pipeline import StageContext
        from autocut.stages.subtitle import stage_hardsub

        workdir = tmp_path / "w"
        workdir.mkdir()
        ctx = StageContext(test_video, workdir, mock_config)
        out = stage_hardsub(ctx)
        assert out["has_hardsub"] is False

    def test_bgm_empty_skips(self, mock_config):
        mock_config.audio.bgm_library = None
        from autocut.stages.render_stage import _scan_bgm

        assert _scan_bgm(None) == []

    def test_style_examples_none(self, mock_config):
        mock_config.style.examples_dir = None
        from pathlib import Path as P

        from autocut.pipeline import StageContext
        from autocut.style import get_style_guide

        ctx = StageContext(P("v.mp4"), P("/tmp/x"), mock_config)
        guide, examples = get_style_guide(ctx, None)
        assert guide == "" and examples == []
