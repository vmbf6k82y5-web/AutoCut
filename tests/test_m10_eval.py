"""M10 测试：eval 评测打分 + HTML 报告 + 对比。"""

from __future__ import annotations

import json
import shutil

from autocut.eval_runner import EvalReport, JudgeScores, _render_html, run_eval


class TestJudgeScores:
    def test_overall(self):
        s = JudgeScores(story_coherence=8, narration_match=8, name_accuracy=8, hook_appeal=8, pacing=8)
        assert s.overall == 8.0


class TestEvalRunner:
    def test_run_eval_produces_report(self, mock_config, tmp_path, assets):
        """对 1 个视频跑评测，产出 HTML 与 JSON 报告。"""
        mock_config.video.target_length = "15s"
        mock_config.general.work_dir = tmp_path / "work"
        mock_config.general.output_dir = tmp_path / "output"
        eval_dir = tmp_path / "evalset"
        eval_dir.mkdir()
        shutil.copy(assets / "test_60s.mp4", eval_dir / "ep01.mp4")
        out = run_eval(eval_dir, mock_config, profile_name="balanced")
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert "评测报告" in html
        assert "ep01.mp4" in html
        # JSON 报告
        data = json.loads((eval_dir / "eval_report.json").read_text(encoding="utf-8"))
        assert len(data["items"]) == 1
        assert data["items"][0]["qc_passed"] in (True, False)

    def test_compare_html(self, tmp_path):
        """--compare 生成对比信息。"""
        old = tmp_path / "old.json"
        old.write_text(json.dumps({"avg_overall": 6.5}), encoding="utf-8")
        report = EvalReport(profile="best", avg_overall=7.2)
        html = _render_html(report, old)
        assert "对比旧报告" in html
        assert "7.2" in html

    def test_render_html_structure(self):
        from autocut.eval_runner import EvalItem

        report = EvalReport(items=[EvalItem(video="a.mp4", scores=JudgeScores(story_coherence=9))])
        html = _render_html(report, None)
        assert "a.mp4" in html and "总分" in html
