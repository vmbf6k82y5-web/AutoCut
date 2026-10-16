"""exe 入口：无参数双击默认进入 GUI；有子命令走 CLI。首启检测 ffmpeg。"""

from __future__ import annotations

import sys


def main() -> None:
    # 首启：检测/下载 ffmpeg（失败不阻塞，真正用时再报）
    try:
        from autocut.ffmpeg_bootstrap import ensure_ffmpeg

        ensure_ffmpeg(auto_download=True)
    except Exception as e:  # noqa: BLE001
        print(f"[AutoCut] ffmpeg 检测: {e}", file=sys.stderr)

    # 无参数 → 默认 GUI
    if len(sys.argv) == 1:
        sys.argv = [sys.argv[0], "gui"]

    from autocut.cli import app

    app()


if __name__ == "__main__":
    main()
