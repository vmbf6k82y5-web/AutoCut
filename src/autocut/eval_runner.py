"""eval 评测：对固定评测集重跑全流程，reviewer 当裁判打分，输出 HTML 报告，支持对比。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Config
from .pipeline import StageContext
from .prompt import load_prompt
from .runner import run_video
from .series import VIDEO_EXTS

logger = logging.getLogger("autocut.eval")


class JudgeScores(BaseModel):
    story_coherence: float = 0.0  # 故事连贯 1-10
    narration_match: float = 0.0  # 文案与画面匹配
    name_accuracy: float = 0.0  # 人名准确
    hook_appeal: float = 0.0  # 开头吸引力
    pacing: float = 0.0  # 节奏

    @property
    def overall(self) -> float:
        vals = [self.story_coherence, self.narration_match, self.name_accuracy, self.hook_appeal, self.pacing]
        return sum(vals) / len(vals)


class EvalItem(BaseModel):
    video: str
    scores: JudgeScores = Field(default_factory=JudgeScores)
    qc_passed: bool = False
    narration_ratio: float = 0.0
    duration_seconds: float = 0.0
    duration_deviation: float = 0.0
    total_cost: float = 0.0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    error: str = ""


class EvalReport(BaseModel):
    profile: str = "balanced"
    items: list[EvalItem] = Field(default_factory=list)
    avg_overall: float = 0.0
    total_cost: float = 0.0


def _judge(ctx: StageContext, timeline_text: str) -> JudgeScores:
    """reviewer 当裁判打分。"""
    if not ctx.roles.get("reviewer"):
        return JudgeScores()
    prompt = load_prompt("reviewer", ctx.config.prompts.custom_dir)
    judge_msgs = [
        {"role": "system", "content": "你是影视解说评测裁判，对解说质量按 1-10 打分。"},
        {"role": "user", "content": (
            f"{prompt.body}\n\n请对以下解说打分，以 JSON 输出：\n"
            '{"story_coherence":0-10,"narration_match":0-10,"name_accuracy":0-10,'
            '"hook_appeal":0-10,"pacing":0-10}\n\n解说内容：\n' + timeline_text[:6000]
        )},
    ]
    try:
        result, _ = ctx.caller.call("reviewer", judge_msgs, JudgeScores,
                                    prompt_name="reviewer", prompt_version=prompt.version, prompt_hash=prompt.hash)
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("裁判打分失败: %s", e)
        return JudgeScores()


def run_eval(eval_dir: Path, config: Config, *, profile_name: str | None = None, compare: Path | None = None) -> Path:
    """对评测集目录重跑全流程并打分，输出 eval_report.html。"""
    eval_dir = Path(eval_dir)
    videos = sorted(p for p in eval_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        raise FileNotFoundError(f"评测集 {eval_dir} 无视频")
    profile = profile_name or config.active_profile
    report = EvalReport(profile=profile)

    for video in videos:
        logger.info("=== 评测 %s ===", video.name)
        item = EvalItem(video=video.name)
        t0 = time.time()
        try:
            final = run_video(video, config, profile_name=profile)
            workdir = config.general.work_dir / video.stem
            # 读 QC 与 manifest
            qc_file = workdir / "11_qc.json"
            if qc_file.exists():
                qc = json.loads(qc_file.read_text(encoding="utf-8"))
                item.qc_passed = qc.get("passed", False)
                item.narration_ratio = qc.get("narration_ratio", 0.0)
                item.duration_seconds = qc.get("duration_seconds", 0.0)
                item.duration_deviation = qc.get("duration_deviation", 0.0)
            mf = workdir / "manifest.json"
            if mf.exists():
                usage = json.loads(mf.read_text(encoding="utf-8")).get("usage", {})
                item.total_cost = usage.get("total_cost", 0.0)
                item.total_tokens = usage.get("total_input_tokens", 0) + usage.get("total_output_tokens", 0)
            # 裁判打分
            tl = workdir / "09_timeline_aligned.json"
            tl_text = tl.read_text(encoding="utf-8") if tl.exists() else ""
            ctx = StageContext(video, workdir, config, profile_name=profile)
            item.scores = _judge(ctx, tl_text)
        except Exception as e:  # noqa: BLE001
            item.error = str(e)
            logger.error("评测 %s 失败: %s", video.name, e)
        item.elapsed_seconds = time.time() - t0
        report.items.append(item)

    scored = [i.scores.overall for i in report.items if not i.error]
    report.avg_overall = sum(scored) / len(scored) if scored else 0.0
    report.total_cost = sum(i.total_cost for i in report.items)

    out = eval_dir / "eval_report.html"
    out.write_text(_render_html(report, compare), encoding="utf-8")
    # 同时存 JSON 供对比
    (eval_dir / "eval_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info("评测报告: %s（平均 %.1f 分）", out, report.avg_overall)
    return out


def _render_html(report: EvalReport, compare: Path | None) -> str:
    rows = []
    for it in report.items:
        s = it.scores
        rows.append(
            f"<tr><td>{it.video}</td><td>{s.overall:.1f}</td><td>{s.story_coherence:.0f}</td>"
            f"<td>{s.narration_match:.0f}</td><td>{s.name_accuracy:.0f}</td><td>{s.hook_appeal:.0f}</td>"
            f"<td>{s.pacing:.0f}</td><td>{'✅' if it.qc_passed else '❌'}</td>"
            f"<td>{it.narration_ratio:.0%}</td><td>${it.total_cost:.4f}</td>"
            f"<td>{it.elapsed_seconds:.0f}s</td><td>{it.error}</td></tr>"
        )
    compare_html = ""
    if compare and Path(compare).exists():
        try:
            old = json.loads(Path(compare).read_text(encoding="utf-8"))
            old_avg = old.get("avg_overall", 0)
            diff = report.avg_overall - old_avg
            compare_html = f"<p>对比旧报告：旧平均 {old_avg:.1f} → 新平均 {report.avg_overall:.1f}（{'+' if diff>=0 else ''}{diff:.1f}）</p>"
        except Exception:  # noqa: BLE001
            pass
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>AutoCut 评测报告</title>
<style>body{{font-family:sans-serif;background:#0f1419;color:#eee;padding:30px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #333;padding:6px 10px;font-size:13px}}
th{{background:#1a2230}}</style></head><body>
<h1>AutoCut 评测报告（profile: {report.profile}）</h1>
<p>平均总分：<b>{report.avg_overall:.1f}</b> / 10　总费用：${report.total_cost:.4f}　样本数：{len(report.items)}</p>
{compare_html}
<table><thead><tr><th>视频</th><th>总分</th><th>连贯</th><th>匹配</th><th>人名</th><th>开头</th><th>节奏</th>
<th>QC</th><th>解说占比</th><th>费用</th><th>耗时</th><th>错误</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""
