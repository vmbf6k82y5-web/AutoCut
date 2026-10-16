"""AutoCut CLI 入口（typer）。

命令：run / rerun / estimate / series / eval / models / gui / watch / mcp / gen-test-media
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="autocut",
    help="AutoCut：输入影视剧视频，全自动产出带 AI 解说配音和字幕的解说视频。",
    no_args_is_help=True,
    add_completion=False,
)
models_app = typer.Typer(name="models", help="模型管理", no_args_is_help=True)
app.add_typer(models_app, name="models")

logger = logging.getLogger("autocut.cli")


def _parse_role_overrides(role: list[str] | None) -> dict[str, str]:
    """解析 --role writer=gemini-3.8f 列表。"""
    out: dict[str, str] = {}
    for item in role or []:
        if "=" not in item:
            raise typer.BadParameter(f"--role 格式应为 role=model，收到: {item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _load(config: Path | None):
    from .config import load_config
    from .logging_utils import setup_logging

    cfg = load_config(config)
    setup_logging(cfg.general.log_level)
    return cfg


@app.command()
def run(
    video: Annotated[Path, typer.Argument(help="输入视频路径")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p", help="命名方案 economy/balanced/best")] = None,
    role: Annotated[list[str] | None, typer.Option("--role", help="覆盖角色模型，如 writer=gemini-3.8f")] = None,
) -> None:
    """全流程处理一个视频。"""
    cfg = _load(config)
    from .runner import run_video

    try:
        result = run_video(
            video,
            cfg,
            profile_name=profile,
            role_overrides=_parse_role_overrides(role),
        )
    except Exception as e:
        typer.secho(f"失败: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"完成: {result}", fg=typer.colors.GREEN)


@app.command()
def rerun(
    job: Annotated[str, typer.Argument(help="job_id 或工作目录名")],
    from_stage: Annotated[str, typer.Option("--from", help="从该阶段重跑")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    role: Annotated[list[str] | None, typer.Option("--role")] = None,
) -> None:
    """从某阶段重跑一个任务。"""
    cfg = _load(config)
    from .runner import rerun_job

    try:
        result = rerun_job(job, from_stage, cfg, profile_name=profile, role_overrides=_parse_role_overrides(role))
    except Exception as e:
        typer.secho(f"失败: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"完成: {result}", fg=typer.colors.GREEN)


@app.command()
def estimate(
    video: Annotated[Path, typer.Argument(help="输入视频路径")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """预估 token 与费用（不调用真实模型）。"""
    cfg = _load(config)
    from .estimate import estimate_video

    est = estimate_video(video, cfg, profile_name=profile)
    typer.echo(f"视频: {est.video}")
    typer.echo(f"时长: {est.duration_seconds:.0f}s, 字幕字数: {est.subtitle_chars}")
    for r in est.roles:
        typer.echo(f"  {r.role:10s} [{r.model}] 输入≈{r.est_input_tokens} 输出≈{r.est_output_tokens} 费用≈${r.est_cost:.4f}")
    typer.secho(f"总费用预估: ${est.total_cost:.4f}（预算内: {est.within_budget}）", fg=typer.colors.CYAN)


@app.command()
def series(
    directory: Annotated[Path, typer.Argument(help="剧集目录")],
    mode: Annotated[str, typer.Option("--mode", help="episodes 逐集 / compilation 合集")] = "episodes",
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """批量处理剧集目录。"""
    cfg = _load(config)
    from .series import run_series

    run_series(directory, mode, cfg, profile_name=profile)


@app.command(name="eval")
def eval_cmd(
    eval_dir: Annotated[Path, typer.Argument(help="评测集目录")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    compare: Annotated[Path | None, typer.Option("--compare", help="对比旧报告")] = None,
) -> None:
    """评测打分。"""
    cfg = _load(config)
    from .eval_runner import run_eval

    run_eval(eval_dir, cfg, profile_name=profile, compare=compare)


@models_app.command("list")
def models_list(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """列出已注册模型。"""
    cfg = _load(config)
    from .model_client import list_models_table

    typer.echo(list_models_table(cfg))


@models_app.command("test")
def models_test(
    name: Annotated[str, typer.Argument(help="模型名")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """测试模型连通性。"""
    cfg = _load(config)
    from .model_client import test_model

    ok, msg = test_model(cfg, name)
    typer.secho(msg, fg=typer.colors.GREEN if ok else typer.colors.RED)
    if not ok:
        raise typer.Exit(1)


@app.command()
def gui(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    port: Annotated[int, typer.Option("--port")] = 8787,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="不自动打开浏览器")] = False,
) -> None:
    """启动本地 Web 界面并打开浏览器。"""
    cfg = _load(config)
    from .web.server import run_gui

    run_gui(cfg, port=port, open_browser=not no_browser)


@app.command()
def watch(
    inbox: Annotated[Path, typer.Argument(help="监听目录")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """监听文件夹，新视频自动处理。"""
    cfg = _load(config)
    from .watcher import run_watch

    run_watch(inbox, cfg, profile_name=profile)


@app.command()
def mcp(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """以 MCP Server（stdio）暴露工具给外部 agent。"""
    cfg = _load(config)
    from .mcp_server import run_mcp

    run_mcp(cfg)


@app.command(name="gen-test-media")
def gen_test_media(
    out: Annotated[Path, typer.Argument(help="输出目录")] = Path("tests/assets"),
) -> None:
    """用 ffmpeg 生成测试素材。"""
    import subprocess

    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "make_test_media.py"
    subprocess.run([sys.executable, str(script), str(out)], check=True)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
