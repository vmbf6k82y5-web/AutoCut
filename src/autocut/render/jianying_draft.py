"""jianying_draft 渲染器：导出剪映草稿（pyJianYingDraft，可选依赖）。

不直接产出 MP4，而是生成剪映草稿供人工精修。未安装时给出中文提示。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("autocut.render.jianying")


class JianYingDraftRenderer:
    name = "jianying_draft"

    def render(self, timeline, assets: dict[str, Any], workdir: Path, config) -> Path:
        try:
            import pyJianYingDraft as draft
        except ImportError as e:
            raise RuntimeError(
                "jianying_draft 渲染器需要 pyJianYingDraft: uv pip install 'autocut-ai[jianying]'"
            ) from e

        out_dir = config.general.output_dir / "jianying_drafts"
        out_dir.mkdir(parents=True, exist_ok=True)
        sources: dict[str, Path] = assets.get("sources", {})
        out_w, out_h = config.video.out_size

        script = draft.ScriptFile(width=out_w, height=out_h)
        video_track = script.add_track(draft.TrackType.video, name="main")
        audio_track = script.add_track(draft.TrackType.audio, name="narration")

        # 逐段加入素材
        t_offset = 0.0
        for seg in timeline.segments:
            src = sources.get(seg.source) or sources.get("main")
            if src is None:
                continue
            dur = seg.src_duration / max(0.01, seg.speed)
            try:
                material = draft.VideoMaterial(str(src))
                clip = draft.ClipSettings()
                script.add_segment(
                    draft.VideoSegment(material, draft.Timerange(start=0, duration=int(dur * 1e6))),
                    track_name="main",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("剪映段 %s 添加失败: %s", seg.id, e)
            # TTS 音频
            if seg.type == "narration" and seg.audio_file:
                audio_path = workdir / seg.audio_file
                if audio_path.exists():
                    try:
                        amaterial = draft.AudioMaterial(str(audio_path))
                        script.add_segment(
                            draft.AudioSegment(amaterial, draft.Timerange(start=int(t_offset * 1e6), duration=int(dur * 1e6))),
                            track_name="narration",
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("剪映音频段 %s 失败: %s", seg.id, e)
            t_offset += dur

        draft_path = out_dir / f"{timeline.title or 'autocut'}"
        script.dump(str(draft_path) + ".json")
        logger.info("剪映草稿已导出: %s.json", draft_path)
        return Path(str(draft_path) + ".json")
