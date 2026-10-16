"""M1 测试：配置加载、provider 注册表、日志脱敏、数据契约。"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocut.config import Config, ModelSpec, Roles, dump_config_toml, load_config
from autocut.contracts import Segment, Timeline
from autocut.logging_utils import SanitizeFilter, register_secret, sanitize
from autocut.providers import get, list_providers, register, reset_registry


class TestConfigLoading:
    def test_defaults(self):
        cfg = Config()
        assert cfg.general.source_language == "zh"
        assert cfg.video.aspect == "16:9"
        assert cfg.video.target_seconds == 480.0
        assert cfg.active_profile == "balanced"

    def test_load_toml(self, tmp_path: Path):
        p = tmp_path / "c.toml"
        p.write_text(
            """
[video]
aspect = "9:16"
target_length = "5min"

[[models]]
name = "m1"
protocol = "openai"
base_url = "http://x"
capabilities = ["text", "json_mode"]

[roles]
writer = ["m1"]
""",
            encoding="utf-8",
        )
        cfg = load_config(p)
        assert cfg.video.aspect == "9:16"
        assert cfg.video.target_seconds == 300.0
        assert cfg.model_by_name("m1") is not None
        assert cfg.roles.writer == ["m1"]

    def test_env_api_key(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret-value")
        m = ModelSpec(name="x", api_key="env:MY_KEY")
        assert m.resolved_api_key() == "secret-value"
        m2 = ModelSpec(name="y", api_key="literal")
        assert m2.resolved_api_key() == "literal"

    def test_role_priority_cli_over_profile_over_default(self):
        cfg = Config(
            models=[ModelSpec(name="a"), ModelSpec(name="b"), ModelSpec(name="c")],
            roles=Roles(writer=["a"], vision=["a"]),
        )
        cfg.profiles = {
            "best": __import__("autocut.config", fromlist=["Profile"]).Profile(
                roles=Roles(writer=["b"])
            )
        }
        # 默认
        assert cfg.effective_roles()["writer"] == ["a"]
        # profile 覆盖
        assert cfg.effective_roles(profile="best")["writer"] == ["b"]
        # CLI 最高
        assert cfg.effective_roles(profile="best", cli_overrides={"writer": "c"})["writer"] == ["c"]
        # 未被 profile 覆盖的角色保持默认
        assert cfg.effective_roles(profile="best")["vision"] == ["a"]

    def test_capability_validation(self):
        cfg = Config(
            models=[ModelSpec(name="text-only", capabilities=["text", "json_mode"])],
            roles=Roles(vision=["text-only"], writer=["text-only"]),
        )
        errors = cfg.validate_role_capabilities()
        assert any("vision" in e and "图像理解" in e for e in errors)
        assert not any("writer" in e for e in errors)

    def test_capability_validation_video_profile(self):
        cfg = Config(
            models=[ModelSpec(name="vis", capabilities=["text", "vision", "json_mode"])],
            roles=Roles(vision=["vis"], writer=["vis"]),
        )
        from autocut.config import Profile

        # frames 模式 OK
        assert cfg.validate_role_capabilities(profile=Profile(vision_mode="frames")) == []
        # video 模式需要 video 能力
        errors = cfg.validate_role_capabilities(profile=Profile(vision_mode="video"))
        assert any("视频理解" in e for e in errors)

    def test_dump_roundtrip(self, tmp_path: Path):
        cfg = Config(
            models=[ModelSpec(name="m1", base_url="http://x", capabilities=["text"])],
            roles=Roles(writer=["m1"]),
        )
        cfg.video.style = "搞笑"
        text = dump_config_toml(cfg)
        p = tmp_path / "rt.toml"
        p.write_text(text, encoding="utf-8")
        cfg2 = load_config(p)
        assert cfg2.video.style == "搞笑"
        assert cfg2.model_by_name("m1") is not None
        assert cfg2.roles.writer == ["m1"]

    def test_sanitized_snapshot_hides_keys(self):
        cfg = Config(models=[ModelSpec(name="m", api_key="super-secret")])
        snap = cfg.sanitized_snapshot()
        assert "super-secret" not in str(snap)
        assert "api_key" not in snap["models"][0]


class TestProviderRegistry:
    def setup_method(self):
        reset_registry()
        from autocut.providers.builtin import register_builtin

        register_builtin()

    def test_builtin_registered(self):
        providers = list_providers()
        for group in ("subtitle", "shots", "loudness", "hardsub", "tts", "renderer"):
            assert group in providers
        assert "mock" in providers["subtitle"]
        assert "edge_tts" in providers["tts"]
        assert "ffmpeg_mp4" in providers["renderer"]
        assert "auto" in providers["subtitle"]

    def test_get_mock(self):
        prov = get("subtitle", "mock")
        assert prov.name == "mock"

    def test_unknown_raises(self):
        with pytest.raises(KeyError, match="未注册"):
            get("subtitle", "nonexistent")

    def test_register_custom(self):
        class My:
            name = "mine"

        register("subtitle", "mine", My)
        assert "mine" in list_providers("subtitle")["subtitle"]
        assert get("subtitle", "mine").name == "mine"


class TestSanitization:
    def test_bearer_token(self):
        s = sanitize('Authorization: Bearer sk-abcdef1234567890')
        assert "abcdef" not in s
        assert "Bearer ***" in s or "***" in s

    def test_url_key_param(self):
        s = sanitize("https://api.example.com/v1?key=AIzaSySecretKey123&other=1")
        assert "AIzaSySecretKey123" not in s

    def test_api_key_json(self):
        s = sanitize('{"api_key": "my-secret-key-123"}')
        assert "my-secret-key-123" not in s

    def test_registered_secret(self):
        register_secret("real-key-abcdef")
        s = sanitize("calling with real-key-abcdef done")
        assert "real-key-abcdef" not in s
        assert "***" in s

    def test_filter_on_log_record(self, caplog):
        import logging

        f = SanitizeFilter()
        record = logging.LogRecord("t", logging.INFO, "", 0, "key=sk-abc12345678", None, None)
        f.filter(record)
        assert "sk-abc12345678" not in record.getMessage()


class TestContracts:
    def test_segment_validation(self):
        with pytest.raises(ValueError, match="src_out"):
            Segment(id="s1", type="narration", text="x", src_in=10, src_out=5)
        with pytest.raises(ValueError, match="text"):
            Segment(id="s2", type="narration", text="", src_in=0, src_out=5)
        # original 不需要 text
        seg = Segment(id="s3", type="original", src_in=0, src_out=5)
        assert seg.resolved_orig_volume(0.15) == 1.0

    def test_hook_reuse_limit(self):
        segs = [
            Segment(id=f"s{i}", type="original", src_in=10, src_out=15)
            for i in range(3)
        ]
        with pytest.raises(ValueError, match="超过 2 次"):
            Timeline(segments=segs)
        # 2 次允许（钩子复用）
        Timeline(segments=segs[:2])

    def test_orig_volume_default(self):
        n = Segment(id="a", type="narration", text="解说", src_in=0, src_out=5)
        assert n.resolved_orig_volume(0.15) == 0.15
        o = Segment(id="b", type="original", src_in=0, src_out=5)
        assert o.resolved_orig_volume(0.15) == 1.0
