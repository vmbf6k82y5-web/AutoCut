"""ASS 字幕生成：由 TTS 词级时间戳 + 成片时间轴生成烧录字幕。

- 按成片时间轴偏移
- 单行不超过 max_chars_per_line 自动断句
- 硬字幕存在时按 hardsub_mode 处理（move_up 上移 / bar 底条 / blur 由渲染处理）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def format_ass_time(seconds: float) -> str:
    """秒 → ASS 时间 H:MM:SS.cc（厘秒）"""
    seconds = max(0.0, seconds)
    cs = round(seconds * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


@dataclass
class ASSEvent:
    start: float
    end: float
    text: str
    style: str = "Default"


def split_text_to_lines(text: str, max_chars: int) -> list[str]:
    """把一句文案按 max_chars 断成多行/多条字幕（按标点优先断）。"""
    text = text.strip()
    if not text:
        return []
    # 先按强标点切分子句
    import re

    clauses = re.split(r"(?<=[，。！？；、…—,.!?;])", text)
    clauses = [c for c in (c.strip() for c in clauses) if c]
    lines: list[str] = []
    buf = ""
    for c in clauses:
        if len(buf) + len(c) <= max_chars:
            buf += c
        else:
            if buf:
                lines.append(buf)
            # 单句仍超长则硬切
            rest = c
            while len(rest) > max_chars:
                lines.append(rest[:max_chars])
                rest = rest[max_chars:]
            buf = rest
    if buf:
        lines.append(buf)
    return lines or [text]


def events_from_narration(
    text: str,
    words: list[dict],
    seg_out_start: float,
    seg_out_end: float,
    max_chars_per_line: int,
) -> list[ASSEvent]:
    """为一个 narration 段生成 ASS 事件。

    words: TTS 词级时间戳（相对段内，秒）。可能为空 → 均分。
    返回按成片时间偏移后的事件。
    """
    lines = split_text_to_lines(text, max_chars_per_line)
    if not lines:
        return []
    seg_dur = max(0.01, seg_out_end - seg_out_start)

    # 计算每行的时间范围：用字数占比 + 词级时间戳（若有）
    # 词级时间戳是相对段内的；映射到成片时间 = seg_out_start + t
    char_counts = [len(line) for line in lines]
    total_chars = sum(char_counts)
    events: list[ASSEvent] = []

    # 构造字符→时间映射（词级或线性）
    def char_time(char_index: int) -> float:
        """第 char_index 个字符（全局）的成片时间。"""
        if words:
            # words 覆盖 text 的每个字
            idx = min(char_index, len(words) - 1)
            return seg_out_start + words[idx]["start"]
        frac = char_index / max(1, total_chars)
        return seg_out_start + frac * seg_dur

    cursor = 0
    for li, line in enumerate(lines):
        start_idx = cursor
        end_idx = cursor + len(line) - 1
        cursor += len(line)
        t_start = char_time(start_idx)
        # 结束时间：下一行开头，或段尾
        t_end = char_time(end_idx + 1) if li < len(lines) - 1 else seg_out_end
        # 保证最小时长与顺序
        t_end = max(t_end, t_start + 0.4)
        events.append(ASSEvent(start=t_start, end=t_end, text=line))
    return events


def build_ass(
    events: list[ASSEvent],
    out_path: Path,
    *,
    font: str,
    font_size: int,
    color: str,
    outline_color: str,
    outline: int,
    margin_v: int,
    width: int,
    height: int,
    position: str = "bottom",
    translation_font_size: int = 14,
) -> Path:
    """写 ASS 文件。position: bottom/top（move_up 时调用方传 top 或更大 margin_v）。"""
    # Alignment: 2=底部居中, 8=顶部居中
    alignment = 2 if position == "bottom" else 8
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{color},&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{outline},0,{alignment},20,20,{margin_v},1
Style: Translation,{font},{translation_font_size},&H00D0D0D0,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{max(1, outline - 1)},0,{alignment},20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for ev in sorted(events, key=lambda e: e.start):
        text = ev.text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{format_ass_time(ev.start)},{format_ass_time(ev.end)},{ev.style},,0,0,0,,{text}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
