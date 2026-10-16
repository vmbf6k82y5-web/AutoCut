"""M7 测试：剧集记忆 + metadata 发布物料（封面）。"""

from __future__ import annotations

import json

from autocut.contracts import Outline, OutlineNode
from autocut.cover import make_cover, pick_best_frame
from autocut.pipeline import StageContext
from autocut.series import (
    Character,
    SeriesMemory,
    load_series_memory,
    save_series_memory,
    update_series_memory,
)


class TestSeriesMemory:
    def test_save_load_roundtrip(self, tmp_path):
        mem = SeriesMemory(
            characters=[Character(name="张三", aliases=["小帅"], relation="主角", pronunciation="zhāng sān")],
            speaker_map={"SPEAKER_00": "张三"},
            recap="第一集张三发现秘密",
        )
        save_series_memory(tmp_path, mem)
        loaded = load_series_memory(tmp_path)
        assert loaded.characters[0].name == "张三"
        assert loaded.characters[0].pronunciation == "zhāng sān"
        assert loaded.speaker_map["SPEAKER_00"] == "张三"
        assert "张三" in loaded.recap

    def test_load_missing_returns_empty(self, tmp_path):
        mem = load_series_memory(tmp_path / "nonexistent")
        assert mem.characters == []

    def test_prompt_text(self):
        mem = SeriesMemory(characters=[Character(name="李四", aliases=["小美"], relation="女主")])
        text = mem.to_prompt_text()
        assert "李四" in text and "小美" in text

    def test_update_via_memory_role(self, test_video, mock_config, tmp_path):
        series_dir = tmp_path / "series" / "剧"
        workdir = tmp_path / "work"
        workdir.mkdir()
        ctx = StageContext(test_video, workdir, mock_config, series_dir=series_dir)
        mem = update_series_memory(ctx, "本集大纲内容")
        assert series_dir.joinpath("series.json").exists()
        assert isinstance(mem, SeriesMemory)


class TestCover:
    def test_pick_best_frame(self, assets, tmp_path, mock_config):
        outline = Outline(nodes=[OutlineNode(id="n1", summary="高潮", function="高潮", time_start=10, time_end=20)])
        frame = pick_best_frame(assets / "test_60s.mp4", outline, tmp_path, mock_config)
        assert frame is None or frame.exists()

    def test_make_cover(self, assets, tmp_path, mock_config):
        outline = Outline(nodes=[OutlineNode(id="n1", summary="高潮", function="高潮", time_start=10, time_end=20)])
        out = tmp_path / "cover.jpg"
        result = make_cover(assets / "test_60s.mp4", "爆款解说标题", outline, tmp_path, mock_config, out)
        assert result is not None and result.exists()
        assert result.suffix == ".jpg"


class TestMetadataStage:
    def test_metadata_full(self, test_video, mock_config, tmp_path):
        from autocut.contracts import Segment, Timeline
        from autocut.pipeline import save_json_artifact
        from autocut.stages.metadata_stage import stage_metadata

        workdir = tmp_path / "w"
        workdir.mkdir()
        save_json_artifact(workdir, "03b_outline.json", {"logline": "测试", "nodes": [
            {"id": "n1", "summary": "高潮", "function": "高潮", "time_start": 10, "time_end": 20}
        ]})
        timeline = Timeline(title="测试解说", segments=[
            Segment(id="s1", type="narration", text="解说词", src_in=0, src_out=3),
        ])
        save_json_artifact(workdir, "07_timeline.json", timeline)
        ctx = StageContext(test_video, workdir, mock_config)
        ctx.artifacts["timeline"] = timeline
        out = stage_metadata(ctx)
        assert out["titles"] >= 1
        publish = workdir / "12_publish"
        assert (publish / "publish.json").exists()
        data = json.loads((publish / "publish.json").read_text(encoding="utf-8"))
        assert len(data["titles"]) >= 1
        # 封面（mock outline 高潮节点 10-20s，测试素材 60s 可抽帧）
        assert (publish / "cover.jpg").exists()
