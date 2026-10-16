"""本地 Web GUI：FastAPI + 原生 HTML/JS。任务页 / 设置页 / 评测页。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from ..config import Config, dump_config_toml

logger = logging.getLogger("autocut.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class JobManager:
    """任务管理：启动、状态、进度推送。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.jobs: dict[str, dict[str, Any]] = {}
        self._ws_clients: set[Any] = set()

    def register_ws(self, ws) -> None:
        self._ws_clients.add(ws)

    def unregister_ws(self, ws) -> None:
        self._ws_clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self._ws_clients):
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                self.unregister_ws(ws)

    def start_job(self, video: str, profile: str | None, roles: dict | None) -> str:
        job_id = f"gui-{uuid.uuid4().hex[:8]}"
        self.jobs[job_id] = {
            "id": job_id, "video": video, "status": "running", "progress": "启动中",
            "result": None, "error": "", "usage": {}, "stage": "",
        }

        def _run() -> None:
            from ..runner import run_video

            try:
                final = run_video(Path(video), self.config, profile_name=profile,
                                  role_overrides=roles, job_id=job_id)
                self.jobs[job_id].update(status="done", result=str(final), progress="完成")
                self._fill_usage(job_id)
            except Exception as e:  # noqa: BLE001
                self.jobs[job_id].update(status="error", error=str(e), progress="失败")

        threading.Thread(target=_run, daemon=True).start()
        return job_id

    def _fill_usage(self, job_id: str) -> None:
        # 从最近 workdir 读 manifest
        work_root = self.config.general.work_dir
        if not work_root.exists():
            return
        for d in sorted(work_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            mf = d / "manifest.json"
            if mf.exists():
                try:
                    manifest = json.loads(mf.read_text(encoding="utf-8"))
                    self.jobs[job_id]["usage"] = manifest.get("usage", {})
                    qc = d / "11_qc.json"
                    if qc.exists():
                        self.jobs[job_id]["qc"] = json.loads(qc.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass
                break


def create_app(config: Config, config_path: Path | None = None):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="AutoCut GUI")
    jm = JobManager(config)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/config")
    async def get_config():
        return {"toml": dump_config_toml(config), "config": _safe_config(config)}

    @app.post("/api/config")
    async def set_config(body: dict):
        # 接受 TOML 文本写回
        toml_text = body.get("toml", "")
        target = config_path or Path("config.toml")
        target.write_text(toml_text, encoding="utf-8")
        return {"ok": True, "path": str(target)}

    @app.get("/api/models")
    async def list_models():
        return {"models": [m.model_dump(exclude={"api_key"}) for m in config.models]}

    @app.post("/api/models/test")
    async def test_model_ep(body: dict):
        from ..model_client import test_model

        ok, msg = test_model(config, body.get("name", ""))
        return {"ok": ok, "message": msg}

    @app.get("/api/models/available")
    async def available_models(name: str = ""):
        """调用模型端点 /models 拉取可选模型列表。"""
        spec = config.model_by_name(name) if name else (config.models[0] if config.models else None)
        if spec is None or spec.protocol == "mock":
            return {"models": [m.model for m in config.models]}
        try:
            from openai import OpenAI

            client = OpenAI(base_url=spec.base_url or None, api_key=spec.resolved_api_key() or "x")
            lst = client.models.list()
            return {"models": [m.id for m in lst.data]}
        except Exception as e:  # noqa: BLE001
            return {"models": [], "error": str(e)}

    @app.get("/api/providers")
    async def list_providers_ep():
        from ..providers import list_providers

        return list_providers()

    @app.get("/api/profiles")
    async def list_profiles():
        return {"profiles": sorted(config.profiles.keys()), "active": config.active_profile}

    @app.get("/api/roles")
    async def get_roles():
        return {"roles": config.roles.as_dict()}

    @app.post("/api/estimate")
    async def estimate_ep(body: dict):
        from ..estimate import estimate_video

        est = estimate_video(Path(body["video"]), config, body.get("profile"))
        return est.model_dump()

    @app.post("/api/run")
    async def run_ep(body: dict):
        job_id = jm.start_job(body["video"], body.get("profile"), body.get("roles"))
        return {"job_id": job_id}

    @app.get("/api/jobs")
    async def list_jobs():
        return {"jobs": list(jm.jobs.values())}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        return jm.jobs.get(job_id, {"error": "not found"})

    @app.get("/api/result/{job_id}/preview")
    async def preview(job_id: str):
        job = jm.jobs.get(job_id)
        if job and job.get("result") and Path(job["result"]).exists():
            return FileResponse(job["result"])
        return JSONResponse({"error": "无成片"}, status_code=404)

    @app.get("/api/eval/list")
    async def eval_list():
        reports = sorted(Path(".").glob("eval_report*.html"))
        return {"reports": [str(r) for r in reports]}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        jm.register_ws(websocket)
        try:
            while True:
                # 定期推送任务状态
                await websocket.send_text(json.dumps({"jobs": list(jm.jobs.values())}, ensure_ascii=False, default=str))
                await asyncio.sleep(2)
        except WebSocketDisconnect:
            jm.unregister_ws(websocket)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def _safe_config(config: Config) -> dict:
    return config.sanitized_snapshot()


def run_gui(config: Config, port: int = 8787, open_browser: bool = True, config_path: Path | None = None) -> None:
    import uvicorn

    app = create_app(config, config_path)
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    logger.info("AutoCut GUI: %s", url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
