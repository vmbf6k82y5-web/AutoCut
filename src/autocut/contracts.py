"""核心数据契约（pydantic v2）：Timeline 及各阶段产物结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 基础结构
# ---------------------------------------------------------------------------


class SubtitleLine(BaseModel):
    index: int
    start: float
    end: float
    text: str
    speaker_id: str | None = None  # ASR 说话人分离结果
    character: str | None = None  # name_fix 后的角色名


class Shot(BaseModel):
    """镜头：原片时间区间（秒）。"""

    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class HighEnergyRange(BaseModel):
    """响度高能区间。"""

    start: float
    end: float
    mean_loudness: float  # LUFS
    max_loudness: float
    score: float = 0.0  # 归一化 0-1


class HardsubInfo(BaseModel):
    """硬字幕检测结果。"""

    has_hardsub: bool = False
    region_top_ratio: float = 0.75  # 字幕区域上边缘（相对高度）
    confidence: float = 0.0


class SpeakerMap(BaseModel):
    """说话人 ID → 角色名 映射。"""

    mapping: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 剧情大纲
# ---------------------------------------------------------------------------

NodeFunction = Literal["铺垫", "冲突", "转折", "高潮", "收尾"]


class OutlineNode(BaseModel):
    id: str  # 如 "n1"
    summary: str  # 概要
    function: NodeFunction = "铺垫"
    time_start: float = 0.0  # 大致时间范围（秒）
    time_end: float = 0.0
    importance: int = 5  # 1-10


class Outline(BaseModel):
    logline: str = ""
    characters: list[str] = Field(default_factory=list)
    nodes: list[OutlineNode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scout 候选段
# ---------------------------------------------------------------------------


class Candidate(BaseModel):
    id: str  # 如 "c1"
    outline_node_id: str | None = None
    src_in: float
    src_out: float
    score: float = 0.0  # 排序分
    reason: str = ""
    has_dialogue: bool = True

    @property
    def duration(self) -> float:
        return self.src_out - self.src_in


# ---------------------------------------------------------------------------
# Timeline（核心契约）
# ---------------------------------------------------------------------------

SegmentType = Literal["narration", "original"]


class Segment(BaseModel):
    id: str
    source: str = "main"  # 多集合集时的源视频标识
    type: SegmentType
    outline_node_id: str | None = None
    text: str = ""  # narration 必填
    emotion: str = "平叙"  # 平叙/紧张/悲伤/调侃
    src_in: float
    src_out: float
    orig_volume: float | None = None  # None → 按类型取默认
    speed: float = 1.0
    is_hook: bool = False
    # align 阶段写入
    tts_duration: float = 0.0
    audio_file: str = ""  # 相对工作目录

    @field_validator("src_out")
    @classmethod
    def _check_range(cls, v: float, info) -> float:  # noqa: ANN001
        return v

    @model_validator(mode="after")
    def _validate(self) -> Segment:
        if self.src_out <= self.src_in:
            raise ValueError(f"segment {self.id}: src_out({self.src_out}) 必须大于 src_in({self.src_in})")
        if self.type == "narration" and not self.text.strip():
            raise ValueError(f"segment {self.id}: narration 类型必须提供 text")
        if self.emotion not in ("平叙", "紧张", "悲伤", "调侃"):
            self.emotion = "平叙"
        return self

    @property
    def src_duration(self) -> float:
        return self.src_out - self.src_in

    def resolved_orig_volume(self, narration_default: float) -> float:
        if self.orig_volume is not None:
            return self.orig_volume
        return narration_default if self.type == "narration" else 1.0


class Timeline(BaseModel):
    title: str = ""
    style: str = ""
    total_target_seconds: float = 480.0
    segments: list[Segment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_hook_reuse(self) -> Timeline:
        """同一原片区间在成片中最多出现 2 次（钩子复用）。"""
        counts: dict[tuple[str, int, int], int] = {}
        for seg in self.segments:
            key = (seg.source, round(seg.src_in * 10), round(seg.src_out * 10))
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > 2:
                raise ValueError(
                    f"原片区间 {seg.src_in:.1f}-{seg.src_out:.1f} 出现超过 2 次，违反钩子复用约束"
                )
        return self


# ---------------------------------------------------------------------------
# QC 报告
# ---------------------------------------------------------------------------


class QCIssue(BaseModel):
    code: str  # black/silence/duration/subtitle/originality
    message: str
    severity: Literal["error", "warning"] = "error"
    detail: dict = Field(default_factory=dict)


class QCReport(BaseModel):
    passed: bool = True
    duration_seconds: float = 0.0
    target_seconds: float = 0.0
    duration_deviation: float = 0.0
    narration_ratio: float = 0.0
    issues: list[QCIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 估算
# ---------------------------------------------------------------------------


class RoleEstimate(BaseModel):
    role: str
    model: str
    est_input_tokens: int
    est_output_tokens: int
    est_cost: float  # USD


class Estimate(BaseModel):
    video: str
    duration_seconds: float
    subtitle_chars: int
    roles: list[RoleEstimate] = Field(default_factory=list)
    total_cost: float = 0.0
    within_budget: bool = True
