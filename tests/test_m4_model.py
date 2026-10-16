"""M4 测试：ModelClient 双协议、fallback、限流重试、JSON 校验、缓存、计费、LLM 阶段。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from autocut.config import Config, ModelSpec, Roles
from autocut.model_client import (
    BudgetExceededError,
    MockModelClient,
    ModelCaller,
    _estimate_tokens,
    make_client,
)
from autocut.prompt import load_prompt


class Out(BaseModel):
    value: int = 0
    label: str = ""


def _cfg(tmp_path, models, roles_dict) -> Config:
    cfg = Config(models=models, roles=Roles(**roles_dict))
    cfg.general.work_dir = tmp_path / "work"
    cfg.limits.max_cost_per_episode = 5.0
    return cfg


class TestMockClient:
    def test_mock_returns_json(self):
        spec = ModelSpec(name="m", protocol="mock")
        client = make_client(spec)
        data, usage = client.chat_json([{"role": "user", "content": "outline 大纲"}], None)
        assert "nodes" in data
        assert usage["input"] > 0 and usage["output"] > 0

    def test_estimate_tokens(self):
        assert _estimate_tokens("你好世界") >= 4
        assert _estimate_tokens("hello world") >= 2


class TestFallback:
    def test_fallback_on_failure(self, tmp_path, monkeypatch):
        """主模型失败时切到 fallback。"""
        cfg = _cfg(tmp_path, [
            ModelSpec(name="bad", protocol="openai", base_url="http://localhost:1"),
            ModelSpec(name="good", protocol="mock"),
        ], {"outline": ["bad", "good"]})
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        # bad 是 openai 指向不可达地址，会失败；fallback 到 good
        data, model = caller.call("outline", [{"role": "user", "content": "outline 大纲"}], None)
        assert model == "good"
        assert "nodes" in data

    def test_all_fail_raises(self, tmp_path):
        cfg = _cfg(tmp_path, [ModelSpec(name="bad", protocol="openai", base_url="http://localhost:1")],
                   {"outline": ["bad"]})
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        with pytest.raises(RuntimeError, match="所有模型均失败"):
            caller.call("outline", [{"role": "user", "content": "x"}], None)


class TestCaching:
    def test_request_cache_no_double_cost(self, tmp_path):
        """相同请求第二次命中缓存，费用不重复计。"""
        cfg = _cfg(tmp_path, [ModelSpec(name="m", protocol="mock", price_in=1.0, price_out=1.0)],
                   {"outline": ["m"]})
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        msg = [{"role": "user", "content": "outline 大纲 内容A"}]
        d1, _ = caller.call("outline", msg, None)
        cost_after_first = caller.tracker.total_cost
        assert cost_after_first > 0
        d2, _ = caller.call("outline", msg, None)
        assert caller.tracker.total_cost == cost_after_first  # 缓存命中不增费
        assert d1 == d2
        # 第二次记录 cache_hit=True
        assert caller.tracker.records[-1].cache_hit is True


class TestBudget:
    def test_budget_exceeded(self, tmp_path):
        cfg = _cfg(tmp_path, [ModelSpec(name="m", protocol="mock", price_in=1000.0, price_out=1000.0)],
                   {"outline": ["m"]})
        cfg.limits.max_cost_per_episode = 0.000001  # 极低预算
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        caller.call("outline", [{"role": "user", "content": "outline 大纲"}], None)
        with pytest.raises(BudgetExceededError):
            caller.call("outline", [{"role": "user", "content": "outline 大纲 不同内容"}], None)


class TestJSONValidation:
    def test_schema_validation_passes(self, tmp_path):
        cfg = _cfg(tmp_path, [ModelSpec(name="m", protocol="mock")], {"writer": ["m"]})
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        from autocut.stages.write import WriterOutput

        result, _ = caller.call("writer", [{"role": "user", "content": "writer 时间轴 解说"}], WriterOutput)
        assert isinstance(result, WriterOutput)
        assert len(result.segments) >= 1

    def test_fixer_repair(self, tmp_path):
        """坏 JSON 经 fixer 修复。"""

        class BadJSONClient(MockModelClient):
            def chat_json(self, messages, schema=None):
                # 返回不符合 Out schema 的数据
                if schema is not None and schema.__name__ == "Out":
                    return {"value": "not-an-int", "label": 123}, {"input": 1, "output": 1, "cached": 0}
                return super().chat_json(messages, schema)

        spec = ModelSpec(name="bad", protocol="mock")
        cfg = _cfg(tmp_path, [spec, ModelSpec(name="fix", protocol="mock")],
                   {"writer": ["bad"], "fixer": ["fix"]})
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        caller._clients["bad"] = BadJSONClient(spec)
        caller._semaphores["bad"] = __import__("threading").Semaphore(4)
        # writer 用 bad 返回坏数据，经 fixer 修复为合法 Out
        result, _ = caller.call("writer", [{"role": "user", "content": "非大纲非时间轴普通"}], Out)
        assert isinstance(result, Out)


class TestConcurrency:
    def test_max_concurrency_semaphore(self, tmp_path):
        spec = ModelSpec(name="m", protocol="mock", max_concurrency=2)
        cfg = _cfg(tmp_path, [spec], {"outline": ["m"]})
        caller = ModelCaller(cfg, cfg.effective_roles(), tmp_path)
        caller._get_client("m")
        sem = caller._semaphores["m"]
        # 信号量初值 = max_concurrency
        assert sem._value == 2


class TestPromptVersioning:
    def test_prompt_has_version(self):
        p = load_prompt("writer")
        assert p.version >= 1
        assert p.hash
        assert "时间轴" in p.body or "解说" in p.body

    def test_custom_dir_override(self, tmp_path):
        custom = tmp_path / "prompts"
        custom.mkdir()
        (custom / "writer.md").write_text("---\nversion: 99\nrole: writer\n---\n自定义 writer 提示词", encoding="utf-8")
        p = load_prompt("writer", custom)
        assert p.version == 99
        assert "自定义" in p.body


class TestStagesEndToEnd:
    """用 mock 模型跑 outline→scout→write 端到端。"""

    def test_outline_scout_write(self, test_video, mock_config, tmp_path):
        from autocut.pipeline import StageContext
        from autocut.stages.outline import stage_outline
        from autocut.stages.scout import stage_scout
        from autocut.stages.subtitle import stage_subtitle
        from autocut.stages.write import stage_write

        workdir = tmp_path / "work" / "v"
        workdir.mkdir(parents=True)
        mock_config.general.work_dir = tmp_path / "work"
        ctx = StageContext(test_video, workdir, mock_config)
        stage_subtitle(ctx)
        out = stage_outline(ctx)
        assert out["nodes"] >= 1
        assert (workdir / "03b_outline.json").exists()
        sc = stage_scout(ctx)
        assert sc["candidates"] >= 1
        wr = stage_write(ctx)
        assert wr["segments"] >= 1
        assert (workdir / "07_timeline.json").exists()
        # manifest 可用：tracker 有记录
        assert ctx.caller.tracker.total_input > 0
