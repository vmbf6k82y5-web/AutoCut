"""生成测试素材：60 秒带声音的测试视频、测试 SRT、内嵌字幕视频等。

用法：
    python scripts/make_test_media.py [输出目录]
所有素材由 ffmpeg testsrc/sine/drawtext 生成，不依赖真实影片。
"""

from __future__ import annotations

import sys
from pathlib import Path

from autocut.utils import ffmpeg_filter_path, run_cmd

FFMPEG = "ffmpeg"


def run(args: list[str]) -> None:
    print("+", " ".join(str(a) for a in args[:6]), "...")
    # 复用 utils.run_cmd：命令非零退出时异常里带 ffmpeg stderr，真因不再被吞掉
    run_cmd(args)


def make_base_video(out: Path, duration: int = 60, with_audio: bool = True) -> Path:
    """testsrc2 视频 + sine 音频。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={duration}",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    else:
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    args += [str(out)]
    run(args)
    return out


def make_test_srt(out: Path, lines: int = 20, interval: float = 3.0) -> Path:
    """生成测试 SRT。"""
    out.parent.mkdir(parents=True, exist_ok=True)

    def ts(sec: float) -> str:
        ms = int(sec * 1000)
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i in range(lines):
        start = i * interval
        end = start + interval * 0.8
        blocks.append(f"{i + 1}\n{ts(start)} --> {ts(end)}\n这是第{i + 1}句测试台词\n")
    out.write_text("\n".join(blocks), encoding="utf-8")
    return out


def make_embedded_sub_video(out: Path, base: Path, srt: Path) -> Path:
    """把 SRT 作为软字幕轨封装进 MP4（mov_text）。"""
    args = [
        FFMPEG, "-y", "-i", str(base), "-i", str(srt),
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=chi", str(out),
    ]
    run(args)
    return out


def make_hardsub_video(out: Path, base: Path, srt: Path) -> Path:
    """烧录硬字幕的视频（用于 hardsub 检测测试）。"""
    # Windows 下 srt 形如 tests\assets\test.srt，反斜杠会被 subtitles 滤镜当转义符
    # 吞掉（→ testsassetstest.srt），必须转正斜杠并整值加单引号
    args = [
        FFMPEG, "-y", "-i", str(base),
        "-vf", f"subtitles='{ffmpeg_filter_path(srt)}':force_style='FontSize=24,PrimaryColour=&H00FFFFFF'",
        "-c:a", "copy", str(out),
    ]
    run(args)
    return out


def make_scene_change_video(out: Path, duration: int = 40) -> Path:
    """有明显镜头切换的视频（多段 testsrc 拼接，颜色突变）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    seg = duration // 4
    filters = []
    inputs = []
    colors = ["red", "green", "blue", "yellow", "purple", "orange", "cyan", "magenta"]
    for i in range(duration // seg):
        inputs += ["-f", "lavfi", "-i", f"color=c={colors[i % len(colors)]}:size=1280x720:rate=30:duration={seg}"]
        filters.append(f"[{i}:v]format=yuv420p[v{i}]")
    # 音频输入
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    audio_idx = duration // seg
    concat_in = "".join(f"[v{i}]" for i in range(duration // seg))
    filters.append(f"{concat_in}concat=n={duration // seg}:v=1:a=0[vout]")
    args = [FFMPEG, "-y", *inputs, "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)]
    run(args)
    return out


def make_style_examples(out_dir: Path) -> list[Path]:
    """风格学习范例文稿。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    examples = [
        "注意看，这个男人叫小帅。他刚刚发现自己的妻子失踪了。\n"
        "电话打不通，家里一片狼藉，桌上还留着一张神秘的字条。\n"
        "小帅的心一下子沉到了谷底。这背后到底隐藏着什么秘密？",
        "故事发生在一个平静的小镇。女人叫小美，是个普通的上班族。\n"
        "然而今天，一封匿名信彻底打破了她的生活。\n"
        "信里只有一句话：我知道你十年前做了什么。",
    ]
    paths = []
    for i, text in enumerate(examples):
        p = out_dir / f"example_{i + 1}.txt"
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/assets")
    print(f"生成测试素材到 {out.resolve()}")
    base = make_base_video(out / "test_60s.mp4", 60)
    make_base_video(out / "test_silent.mp4", 20, with_audio=False)
    srt = make_test_srt(out / "test.srt")
    make_embedded_sub_video(out / "test_embedded_sub.mp4", base, srt)
    make_hardsub_video(out / "test_hardsub.mp4", base, srt)
    make_scene_change_video(out / "test_scenes.mp4", 40)
    make_style_examples(out / "style_examples")
    print("完成：")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(" -", p, f"({p.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
