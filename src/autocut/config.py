"""AutoCut 配置模型与加载逻辑。

配置来源优先级（高 → 低）：
CLI 覆盖（--role/--profile/--set）> 指定 config.toml > 项目 config.toml > 内置默认。
"""

from __future__ import annotations

import copy
import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger("autocut.config")


# ---------------------------------------------------------------------------
# 模型层
# ---------------------------------------------------------------------------

Capability = Literal["text", "vision", "video", "json_mode"]
Protocol = Literal["openai", "gemini_native", "mock"]


class ModelSpec(BaseModel):
    """config.toml 中 [[models]] 的一项。"""

    name: str
    protocol: Protocol = "openai"
    base_url: str = ""
    api_key: str = ""  # 支持 env:VAR_NAME
    model: str = ""
    capabilities: list[Capability] = Field(default_factory=lambda: ["text", "json_mode"])
    price_in: float = 0.0  # 每百万 token 价格（USD）
    price_out: float = 0.0
    context_window: int = 128000
    max_concurrency: int = 4

    def resolved_api_key(self) -> str:
        """解析 env:XXX 形式的密钥引用。"""
        if self.api_key.startswith("env:"):
            return os.environ.get(self.api_key[4:], "")
        return self.api_key

    @model_validator(mode="after")
    def _default_model(self) -> ModelSpec:
        if not self.model:
            self.model = self.name
        return self


ROLE_NAMES = (
    "name_fix",
    "outline",
    "scout",
    "vision",
    "writer",
    "reviewer",
    "rewrite",
    "memory",
    "metadata",
    "style",
    "fixer",
)

#: 每个角色所需的模型能力
ROLE_REQUIRED_CAPABILITIES: dict[str, set[Capability]] = {
    "name_fix": {"text", "json_mode"},
    "outline": {"text", "json_mode"},
    "scout": {"text", "json_mode"},
    "vision": {"vision", "json_mode"},
    "writer": {"text", "json_mode"},
    "reviewer": {"text", "json_mode"},
    "rewrite": {"text", "json_mode"},
    "memory": {"text", "json_mode"},
    "metadata": {"text", "json_mode"},
    "style": {"text", "json_mode"},
    "fixer": {"text", "json_mode"},
}


class Roles(BaseModel):
    """[roles]：角色 → 模型名列表（fallback 顺序）。"""

    name_fix: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)
    scout: list[str] = Field(default_factory=list)
    vision: list[str] = Field(default_factory=list)
    writer: list[str] = Field(default_factory=list)
    reviewer: list[str] = Field(default_factory=list)
    rewrite: list[str] = Field(default_factory=list)
    memory: list[str] = Field(default_factory=list)
    metadata: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    fixer: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {r: list(getattr(self, r)) for r in ROLE_NAMES}

    def merge_over(self, base: Roles) -> Roles:
        """非空字段覆盖 base。"""
        data = base.as_dict()
        for role in ROLE_NAMES:
            mine = getattr(self, role)
            if mine:
                data[role] = list(mine)
        return Roles(**data)


class ScoutParams(BaseModel):
    candidates: int = 25


class Profile(BaseModel):
    """[profiles.xxx]：命名方案，覆盖 roles 与质量参数。"""

    roles: Roles = Field(default_factory=Roles)
    scout: ScoutParams = Field(default_factory=ScoutParams)
    vision_mode: Literal["frames", "video"] = "frames"
    review_rounds: int = 0  # best 为 1


# ---------------------------------------------------------------------------
# 其余配置段
# ---------------------------------------------------------------------------


def _parse_duration(value: str | int | float) -> float:
    """把 "8min" / "90s" / "1.5min" / 480 解析为秒。"""
    if isinstance(value, int | float):
        return float(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*(s|sec|min|m|h)?\s*", str(value), re.I)
    if not m:
        raise ValueError(f"无法解析时长: {value!r}")
    num = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    return num * {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}[unit]


class GeneralConfig(BaseModel):
    output_dir: Path = Path("output")
    work_dir: Path = Path("work")
    series_dir: Path = Path("series")
    ffmpeg_path: str = ""  # 为空则自动检测 ./bin、PATH
    source_language: str = "zh"
    narration_language: str = "zh"
    ffmpeg_concurrency: int = 2
    log_level: str = "INFO"


class VideoConfig(BaseModel):
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    resolution: str = "1080p"  # 1080p/720p/480p
    fps: int = 30
    target_length: str = "8min"
    style: str = "悬疑"
    review: bool = False  # true 时生成时间轴后暂停等待确认

    @property
    def target_seconds(self) -> float:
        return _parse_duration(self.target_length)

    @property
    def out_size(self) -> tuple[int, int]:
        h = {"1080p": 1080, "720p": 720, "480p": 360}[self.resolution]
        w = {"16:9": round(h * 16 / 9), "9:16": round(h * 9 / 16), "1:1": h}[self.aspect]
        return w - w % 2, h - h % 2  # 保证偶数


class ProvidersConfig(BaseModel):
    subtitle: str = "auto"
    shots: str = "pyscenedetect"
    loudness: str = "ffmpeg_ebur128"
    hardsub: str = "opencv"
    tts: str = "edge_tts"
    renderer: str = "ffmpeg_mp4"
    scout_rule: bool = True  # 纯规则 scout 兜底可用
    vision_none_ok: bool = True  # vision 允许跳过


class ASRConfig(BaseModel):
    model: str = ""  # 指向 [[models]] 的 name，或独立 base_url
    base_url: str = ""
    api_key: str = ""
    model_name: str = "whisper-1"
    diarization: bool = True


class StoryConfig(BaseModel):
    hook: bool = True  # 倒叙钩子


class StyleConfig(BaseModel):
    examples_dir: Path | None = None


class TTSConfig(BaseModel):
    provider: str = "edge_tts"
    voice: str = "zh-CN-YunxiNeural"
    rate: str = "+0%"
    pronunciation_file: Path | None = None
    sentence_pause: float = 0.2
    custom_http_url: str = ""
    model: str = ""  # openai_tts 模型名
    emotion_rate: dict[str, str] = Field(
        default_factory=lambda: {
            "平叙": "+0%",
            "紧张": "+10%",
            "悲伤": "-8%",
            "调侃": "+5%",
        }
    )
    emotion_pitch: dict[str, str] = Field(
        default_factory=lambda: {
            "平叙": "+0Hz",
            "紧张": "+5Hz",
            "悲伤": "-5Hz",
            "调侃": "+3Hz",
        }
    )


class SubtitleStyleConfig(BaseModel):
    font: str = "Sans"
    font_size: int = 20
    color: str = "&H00FFFFFF"  # ASS BGR
    outline_color: str = "&H00000000"
    outline: int = 2
    position: Literal["bottom", "top"] = "bottom"
    margin_v: int = 40
    max_chars_per_line: int = 16
    hardsub_mode: Literal["move_up", "bar", "blur"] = "move_up"
    translation_font_size: int = 14


class AudioConfig(BaseModel):
    narration_orig_volume: float = 0.15
    bgm_library: Path | None = None
    bgm_volume: float = 0.12
    bgm_on_original: Literal["duck", "mute"] = "duck"


class OriginalityConfig(BaseModel):
    min_narration_ratio: float = 0.6
    max_original_segment_seconds: float = 8.0
    max_original_total_ratio: float = 0.3


class LimitsConfig(BaseModel):
    max_cost_per_episode: float = 10.0  # USD


class PromptsConfig(BaseModel):
    custom_dir: Path | None = None


class Config(BaseModel):
    """根配置。"""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    models: list[ModelSpec] = Field(default_factory=list)
    roles: Roles = Field(default_factory=Roles)
    profiles: dict[str, Profile] = Field(default_factory=dict)
    active_profile: str = "balanced"
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    story: StoryConfig = Field(default_factory=StoryConfig)
    style: StyleConfig = Field(default_factory=StyleConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    subtitle_style: SubtitleStyleConfig = Field(default_factory=SubtitleStyleConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    originality: OriginalityConfig = Field(default_factory=OriginalityConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)

    # ---- 模型/角色解析 ---------------------------------------------------

    def model_by_name(self, name: str) -> ModelSpec | None:
        for m in self.models:
            if m.name == name:
                return m
        return None

    def effective_roles(
        self,
        profile: str | None = None,
        cli_overrides: dict[str, str] | None = None,
    ) -> dict[str, list[str]]:
        """优先级：CLI --role > --profile > [roles] 默认。

        cli_overrides: {"writer": "gemini-3.8f"} → 该角色只使用指定模型。
        """
        merged = self.roles
        pname = profile or self.active_profile
        if pname and pname in self.profiles:
            merged = self.profiles[pname].roles.merge_over(merged)
        data = merged.as_dict()
        for role, model_name in (cli_overrides or {}).items():
            if role not in ROLE_NAMES:
                raise ValueError(f"未知角色: {role}")
            data[role] = [model_name]
        return data

    def effective_profile(self, profile: str | None = None) -> Profile:
        pname = profile or self.active_profile
        if pname in self.profiles:
            return self.profiles[pname]
        return Profile()

    # ---- 校验 ------------------------------------------------------------

    @field_validator("profiles", mode="before")
    @classmethod
    def _profiles_default(cls, v: Any) -> Any:
        return v or {}

    def validate_role_capabilities(
        self,
        roles: dict[str, list[str]] | None = None,
        profile: Profile | None = None,
    ) -> list[str]:
        """校验角色所需能力与模型 capabilities 匹配，返回中文错误列表。"""
        errors: list[str] = []
        roles = roles or self.effective_roles()
        profile = profile or self.effective_profile()
        for role, names in roles.items():
            if not names:
                continue  # 未分配的角色在运行到该阶段时才报错
            required = set(ROLE_REQUIRED_CAPABILITIES.get(role, {"text"}))
            # best 模式 vision 直接看视频，需要 video 能力
            if role == "vision" and profile.vision_mode == "video":
                required = {"video", "json_mode"}
            for name in names:
                spec = self.model_by_name(name)
                if spec is None:
                    errors.append(f"角色 [{role}] 引用了未注册的模型「{name}」，请在 [[models]] 中添加")
                    continue
                missing = required - set(spec.capabilities)
                if missing:
                    cap_names = {"text": "文本", "vision": "图像理解", "video": "视频理解", "json_mode": "JSON 输出"}
                    missing_cn = "、".join(cap_names.get(c, c) for c in sorted(missing))
                    errors.append(
                        f"角色 [{role}] 需要「{missing_cn}」能力，但模型「{name}」不具备；"
                        f"请更换模型或在 [[models]] 中为该模型补充 capabilities"
                    )
        return errors

    def sanitized_snapshot(self) -> dict[str, Any]:
        """配置快照（去掉 api_key），写入 manifest。"""
        data = self.model_dump(mode="json")
        for m in data.get("models", []):
            m.pop("api_key", None)
        data.get("asr", {}).pop("api_key", None)
        return data


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_TOML = ""  # 全默认值即可运行


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Config:
    """加载配置：默认 ← 项目 config.toml（若存在）← 指定路径 ← CLI 覆盖。"""
    data: dict[str, Any] = {}
    default_path = Path("config.toml")
    if config_path is None and default_path.exists():
        config_path = default_path
    if config_path is not None:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        with path.open("rb") as f:
            data = tomllib.load(f)
    if cli_overrides:
        data = _deep_merge(data, cli_overrides)
    return Config.model_validate(data)


def dump_config_toml(config: Config) -> str:
    """把配置序列化为 TOML（GUI 设置页写回用），可被 load_config 无损读回。"""

    def to_plain(v: Any) -> Any:
        if isinstance(v, BaseModel):
            return {k: to_plain(x) for k, x in v.__dict__.items() if x is not None}
        if isinstance(v, dict):
            return {k: to_plain(x) for k, x in v.items() if x is not None}
        if isinstance(v, list | tuple):
            return [to_plain(x) for x in v]
        if isinstance(v, Path):
            return v.as_posix()
        return v

    def fmt_key(k: str) -> str:
        # TOML 裸键只允许 A-Za-z0-9_-，其余（如中文）需加引号
        if re.fullmatch(r"[A-Za-z0-9_-]+", k):
            return k
        return '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def fmt_scalar(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int | float):
            return repr(v)
        if isinstance(v, list):
            return "[" + ", ".join(fmt_scalar(x) for x in v) + "]"
        if isinstance(v, dict):
            inner = ", ".join(f"{fmt_key(k)} = {fmt_scalar(x)}" for k, x in v.items())
            return "{ " + inner + " }"
        s = str(v).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'

    def is_table(v: Any) -> bool:
        return isinstance(v, dict)

    def is_table_array(v: Any) -> bool:
        return isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict)

    lines: list[str] = []

    def emit_table(path: str, table: dict[str, Any]) -> None:
        # 先标量，再子表，保证 TOML 结构合法
        scalars = {k: v for k, v in table.items() if not is_table(v) and not is_table_array(v)}
        sub_tables = {k: v for k, v in table.items() if is_table(v)}
        arrays = {k: v for k, v in table.items() if is_table_array(v)}
        if path:
            lines.append(f"[{path}]")
        for k, v in scalars.items():
            if isinstance(v, list) and not v:
                continue  # 空列表与默认一致，省略
            lines.append(f"{fmt_key(k)} = {fmt_scalar(v)}")
        if scalars:
            lines.append("")
        for k, v in sub_tables.items():
            emit_table(f"{path}.{k}" if path else k, v)
        for k, arr in arrays.items():
            for item in arr:
                lines.append(f"[[{path + '.' if path else ''}{k}]]")
                for ik, iv in item.items():
                    if isinstance(iv, dict):
                        continue
                    lines.append(f"{fmt_key(ik)} = {fmt_scalar(iv)}")
                lines.append("")

    emit_table("", to_plain(config))
    return "\n".join(lines)
