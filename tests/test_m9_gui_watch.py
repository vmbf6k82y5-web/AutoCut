"""M9 测试：GUI（FastAPI app 端点）+ watch 模式。"""

from __future__ import annotations

import shutil

import pytest

from autocut.watcher import run_watch
from autocut.web.server import create_app


@pytest.fixture()
def client(mock_config, tmp_path):
    from fastapi.testclient import TestClient

    mock_config.general.work_dir = tmp_path / "work"
    mock_config.general.output_dir = tmp_path / "output"
    app = create_app(mock_config, tmp_path / "config.toml")
    return TestClient(app)


class TestGUI:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "AutoCut" in r.text

    def test_config_roundtrip(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        assert "mock-text" in r.json()["toml"]

    def test_models_list(self, client):
        r = client.get("/api/models")
        names = {m["name"] for m in r.json()["models"]}
        assert "mock-text" in names
        # 不泄露 api_key
        assert all("api_key" not in m for m in r.json()["models"])

    def test_models_test_mock(self, client):
        r = client.post("/api/models/test", json={"name": "mock-text"})
        assert r.json()["ok"] is True

    def test_providers(self, client):
        r = client.get("/api/providers")
        assert "subtitle" in r.json()

    def test_profiles(self, client):
        r = client.get("/api/profiles")
        assert r.status_code == 200

    def test_roles(self, client):
        r = client.get("/api/roles")
        assert "writer" in r.json()["roles"]

    def test_estimate_endpoint(self, client, test_video):
        r = client.post("/api/estimate", json={"video": str(test_video)})
        assert r.status_code == 200
        assert "total_cost" in r.json()

    def test_run_and_job(self, client, test_video, mock_config):
        mock_config.video.target_length = "15s"
        r = client.post("/api/run", json={"video": str(test_video)})
        job_id = r.json()["job_id"]
        import time

        for _ in range(180):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(1)
        assert job["status"] == "done", job.get("error")

    def test_eval_list(self, client):
        r = client.get("/api/eval/list")
        assert "reports" in r.json()


class TestWatch:
    def test_watch_once_processes_new_video(self, mock_config, tmp_path, assets):
        """watch once 模式：放入新视频 → 自动处理并写 done 标记。"""
        mock_config.video.target_length = "15s"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        # 直接复制（跳过大文件稳定等待，once 模式只扫一次）
        shutil.copy(assets / "test_60s.mp4", inbox / "新剧01.mp4")
        # 稳定检测等待 2s，运行一次
        run_watch(inbox, mock_config, once=True, interval=0.1)
        marker = inbox / ".processed" / "新剧01.mp4.done"
        # 可能因稳定检测首次跳过；再跑一次确保处理
        if not marker.exists():
            run_watch(inbox, mock_config, once=True, interval=0.1)
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        assert content.startswith("done:") or content.startswith("skipped"), content
