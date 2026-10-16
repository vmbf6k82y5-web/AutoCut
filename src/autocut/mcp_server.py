"""MCP Server（stdio）：暴露 autocut 工具给外部 agent。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from .config import Config, dump_config_toml

logger = logging.getLogger("autocut.mcp")

#: 任务注册表（进程内）
_jobs: dict[str, dict[str, Any]] = {}


def _job_record(job_id: str) -> dict[str, Any]:
    return _jobs.setdefault(job_id, {"status": "pending", "result": None, "error": ""})


# ---------------------------------------------------------------------------
# 工具实现（纯函数，供 MCP 与测试调用）
# ---------------------------------------------------------------------------


def tool_autocut_run(config: Config, video_path: str, config_overrides: dict | None = None) -> str:
    """启动一个任务，返回 job_id。"""
    from .runner import run_video

    overrides = config_overrides or {}
    profile = overrides.get("profile")
    role_overrides = overrides.get("roles")
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    rec = _job_record(job_id)
    rec["status"] = "running"
    rec["video"] = video_path

    def _run() -> None:
        try:
            final = run_video(Path(video_path), config, profile_name=profile,
                              role_overrides=role_overrides, job_id=job_id)
            rec["status"] = "done"
            rec["result"] = str(final)
        except Exception as e:  # noqa: BLE001
            rec["status"] = "error"
            rec["error"] = str(e)

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def tool_autocut_status(job_id: str) -> dict:
    rec = _jobs.get(job_id)
    if rec is None:
        return {"job_id": job_id, "status": "unknown"}
    return {"job_id": job_id, "status": rec["status"], "error": rec.get("error", "")}


def tool_autocut_result(config: Config, job_id: str) -> dict:
    rec = _jobs.get(job_id)
    if rec is None or rec["status"] != "done":
        return {"job_id": job_id, "status": rec["status"] if rec else "unknown"}
    # 读 QC 与 manifest
    final = rec.get("result", "")
    out: dict[str, Any] = {"job_id": job_id, "output": final, "status": "done"}
    if final:
        workdir = config.general.work_dir / Path(final).stem.replace("autocut_final", "")
        # manifest 按视频名目录
        for cand in config.general.work_dir.iterdir() if config.general.work_dir.exists() else []:
            mf = cand / "manifest.json"
            if mf.exists() and job_id.rsplit("-", maxsplit=1)[-1] in str(cand) or True:
                try:
                    manifest = json.loads(mf.read_text(encoding="utf-8"))
                    out["usage"] = manifest.get("usage", {})
                    qc = cand / "11_qc.json"
                    if qc.exists():
                        out["qc"] = json.loads(qc.read_text(encoding="utf-8"))
                    pub = cand / "12_publish" / "publish.json"
                    if pub.exists():
                        out["publish"] = json.loads(pub.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass
                break
    return out


def tool_autocut_estimate(config: Config, video_path: str, profile: str | None = None) -> dict:
    from .estimate import estimate_video

    est = estimate_video(Path(video_path), config, profile_name=profile)
    return est.model_dump()


def tool_autocut_get_config(config: Config) -> str:
    return dump_config_toml(config)


def tool_autocut_list_providers() -> dict[str, list[str]]:
    from .providers import list_providers

    return list_providers()


def tool_autocut_list_models(config: Config) -> list[dict]:
    return [m.model_dump(exclude={"api_key"}) for m in config.models]


def tool_autocut_test_model(config: Config, name: str) -> dict:
    from .model_client import test_model

    ok, msg = test_model(config, name)
    return {"ok": ok, "message": msg}


def tool_autocut_list_profiles(config: Config) -> list[str]:
    return sorted(config.profiles.keys())


def tool_autocut_set_role(config: Config, role: str, models: list[str]) -> dict:
    if not hasattr(config.roles, role):
        return {"ok": False, "error": f"未知角色 {role}"}
    setattr(config.roles, role, models)
    return {"ok": True, "role": role, "models": models}


# ---------------------------------------------------------------------------
# MCP stdio server
# ---------------------------------------------------------------------------


def run_mcp(config: Config) -> None:
    """以 stdio 启动 MCP Server。"""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as e:
        raise RuntimeError("MCP 需要 mcp 包: uv pip install mcp") from e

    server = Server("autocut")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name="autocut_run", description="处理一个视频，返回 job_id",
                 inputSchema={"type": "object", "properties": {
                     "video_path": {"type": "string"},
                     "config_overrides": {"type": "object"}}, "required": ["video_path"]}),
            Tool(name="autocut_status", description="查询任务状态",
                 inputSchema={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
            Tool(name="autocut_result", description="获取任务结果（输出路径、QC、费用、发布物料）",
                 inputSchema={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
            Tool(name="autocut_estimate", description="预估 token 与费用",
                 inputSchema={"type": "object", "properties": {"video_path": {"type": "string"}, "profile": {"type": "string"}}, "required": ["video_path"]}),
            Tool(name="autocut_get_config", description="读取当前配置（TOML）",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="autocut_list_providers", description="列出所有 provider",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="autocut_list_models", description="列出已注册模型",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="autocut_test_model", description="测试模型连通性",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            Tool(name="autocut_list_profiles", description="列出命名方案",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="autocut_set_role", description="设置角色的模型",
                 inputSchema={"type": "object", "properties": {"role": {"type": "string"}, "models": {"type": "array", "items": {"type": "string"}}}, "required": ["role", "models"]}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        def _txt(obj: Any) -> list[TextContent]:
            return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, default=str))]

        try:
            if name == "autocut_run":
                return _txt({"job_id": tool_autocut_run(config, arguments["video_path"], arguments.get("config_overrides"))})
            if name == "autocut_status":
                return _txt(tool_autocut_status(arguments["job_id"]))
            if name == "autocut_result":
                return _txt(tool_autocut_result(config, arguments["job_id"]))
            if name == "autocut_estimate":
                return _txt(tool_autocut_estimate(config, arguments["video_path"], arguments.get("profile")))
            if name == "autocut_get_config":
                return [TextContent(type="text", text=tool_autocut_get_config(config))]
            if name == "autocut_list_providers":
                return _txt(tool_autocut_list_providers())
            if name == "autocut_list_models":
                return _txt(tool_autocut_list_models(config))
            if name == "autocut_test_model":
                return _txt(tool_autocut_test_model(config, arguments["name"]))
            if name == "autocut_list_profiles":
                return _txt(tool_autocut_list_profiles(config))
            if name == "autocut_set_role":
                return _txt(tool_autocut_set_role(config, arguments["role"], arguments["models"]))
            return _txt({"error": f"未知工具 {name}"})
        except Exception as e:  # noqa: BLE001
            return _txt({"error": str(e)})

    import asyncio

    async def _main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())
