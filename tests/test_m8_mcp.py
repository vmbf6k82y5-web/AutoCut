"""M8 测试：MCP 工具函数（stdio server 的纯函数部分）。"""

from __future__ import annotations

from autocut.mcp_server import (
    tool_autocut_estimate,
    tool_autocut_get_config,
    tool_autocut_list_models,
    tool_autocut_list_profiles,
    tool_autocut_list_providers,
    tool_autocut_set_role,
    tool_autocut_status,
    tool_autocut_test_model,
)


class TestMCPTools:
    def test_list_providers(self, mock_config):
        provs = tool_autocut_list_providers()
        assert "subtitle" in provs and "mock" in provs["subtitle"]
        assert "ffmpeg_mp4" in provs["renderer"]

    def test_list_models(self, mock_config):
        models = tool_autocut_list_models(mock_config)
        names = {m["name"] for m in models}
        assert "mock-text" in names and "mock-vision" in names
        # 不泄露 api_key
        assert all("api_key" not in m for m in models)

    def test_test_model_mock(self, mock_config):
        res = tool_autocut_test_model(mock_config, "mock-text")
        assert res["ok"] is True

    def test_test_model_unknown(self, mock_config):
        res = tool_autocut_test_model(mock_config, "nonexistent")
        assert res["ok"] is False

    def test_list_profiles(self, mock_config):
        from autocut.config import Profile

        mock_config.profiles = {"economy": Profile(), "best": Profile()}
        profiles = tool_autocut_list_profiles(mock_config)
        assert "economy" in profiles and "best" in profiles

    def test_set_role(self, mock_config):
        res = tool_autocut_set_role(mock_config, "writer", ["mock-text"])
        assert res["ok"] is True
        assert mock_config.roles.writer == ["mock-text"]
        bad = tool_autocut_set_role(mock_config, "nonexistent_role", ["x"])
        assert bad["ok"] is False

    def test_get_config_roundtrip(self, mock_config):
        toml_text = tool_autocut_get_config(mock_config)
        assert "mock-text" in toml_text
        assert "[roles]" in toml_text

    def test_estimate_tool(self, mock_config, test_video):
        result = tool_autocut_estimate(mock_config, str(test_video))
        assert "total_cost" in result
        assert result["duration_seconds"] > 0

    def test_status_unknown_job(self):
        res = tool_autocut_status("nonexistent-job")
        assert res["status"] == "unknown"

    def test_run_and_status(self, mock_config, test_video):
        """run 启动后台任务，status 可查。"""
        import time

        from autocut.mcp_server import tool_autocut_run

        mock_config.video.target_length = "15s"
        job_id = tool_autocut_run(mock_config, str(test_video))
        assert job_id.startswith("job-")
        # 等待完成（mock 流程约几十秒到几分钟）
        for _ in range(180):
            st = tool_autocut_status(job_id)
            if st["status"] in ("done", "error"):
                break
            time.sleep(1)
        assert st["status"] == "done", f"任务未完成: {st}"
