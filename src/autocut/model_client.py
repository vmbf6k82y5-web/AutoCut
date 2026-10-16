"""模型客户端层：统一 ModelClient 接口 + 各协议实现 + 角色调度（fallback/重试/限流/缓存/计费）。

设计要点：
- ModelClient 协议：chat_json / chat_with_images / chat_with_video
- openai 协议：openai SDK，覆盖 DeepSeek/Gemini 兼容端点/OpenAI/Ollama/中转
- gemini_native：google-genai，仅 video 能力需要
- mock：确定性假数据，测试专用
- ModelCaller：按角色 fallback 顺序调用，429/5xx 指数退避，pydantic 校验 JSON，
  失败回传重试（最多3次），仍失败交 fixer 修，再失败抛错
- 请求/响应按内容哈希缓存到 work/<视频>/cache，重跑不重复花钱
- 记录 token 与费用，累计超 max_cost_per_episode 停止
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from .config import ROLE_NAMES, Config, ModelSpec
from .logging_utils import sanitize
from .utils import text_hash_cache_key

logger = logging.getLogger("autocut.model_client")


# ---------------------------------------------------------------------------
# 调用记录与计费
# ---------------------------------------------------------------------------


class CallRecord(BaseModel):
    role: str
    model: str
    prompt_name: str = ""
    prompt_version: int = 0
    prompt_hash: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0  # 上下文缓存命中 token
    cost: float = 0.0
    cache_hit: bool = False  # 请求级缓存命中
    duration: float = 0.0
    error: str = ""


class CostTracker:
    """累计 token 与费用，超限停止。"""

    def __init__(self, max_cost: float = 10.0) -> None:
        self.max_cost = max_cost
        self.records: list[CallRecord] = []
        self.total_cost = 0.0
        self.total_input = 0
        self.total_output = 0
        self._lock = threading.Lock()

    def add(self, rec: CallRecord) -> None:
        with self._lock:
            self.records.append(rec)
            self.total_cost += rec.cost
            self.total_input += rec.input_tokens
            self.total_output += rec.output_tokens

    def over_budget(self) -> bool:
        return self.total_cost > self.max_cost

    def summary(self) -> dict[str, Any]:
        by_role: dict[str, dict[str, Any]] = {}
        for r in self.records:
            b = by_role.setdefault(r.role, {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "models": set()})
            b["calls"] += 1
            b["input"] += r.input_tokens
            b["output"] += r.output_tokens
            b["cost"] += r.cost
            b["models"].add(r.model)
        for b in by_role.values():
            b["models"] = sorted(b["models"])
        return {
            "total_cost": round(self.total_cost, 6),
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "calls": len(self.records),
            "by_role": by_role,
        }


class BudgetExceededError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 客户端协议
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelClient(Protocol):
    spec: ModelSpec

    def chat_json(self, messages: list[dict], schema: type[BaseModel] | None = None) -> tuple[dict, dict]: ...

    def chat_with_images(
        self, messages: list[dict], images: list[Path], schema: type[BaseModel] | None = None
    ) -> tuple[dict, dict]: ...

    def chat_with_video(
        self, messages: list[dict], video_path: Path, schema: type[BaseModel] | None = None
    ) -> tuple[dict, dict]: ...


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文约 1 字 1 token，英文约 4 字符 1 token。"""
    cn = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cn
    return cn + other // 4 + 1


def _usage_from_response(resp: Any, prompt_text: str, completion_text: str) -> dict[str, int]:
    """从响应提取 token 用量；无则估算。"""
    usage = getattr(resp, "usage", None)
    if usage:
        return {
            "input": getattr(usage, "prompt_tokens", 0) or _estimate_tokens(prompt_text),
            "output": getattr(usage, "completion_tokens", 0) or _estimate_tokens(completion_text),
            "cached": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0,
        }
    return {"input": _estimate_tokens(prompt_text), "output": _estimate_tokens(completion_text), "cached": 0}


# ---------------------------------------------------------------------------
# OpenAI 兼容协议
# ---------------------------------------------------------------------------


class OpenAIProtocolClient:
    """openai SDK，覆盖 DeepSeek/Gemini 兼容端点/OpenAI/Ollama/中转。"""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        from openai import OpenAI

        self._client = OpenAI(
            base_url=spec.base_url or None,
            api_key=spec.resolved_api_key() or "not-needed",
            timeout=120.0,
            max_retries=0,  # 重试由 ModelCaller 控制
        )

    def _chat(self, messages: list[dict], schema, **kw) -> tuple[dict, dict]:
        kwargs: dict[str, Any] = {"model": self.spec.model, "messages": messages, **kw}
        if schema is not None and "json_mode" in self.spec.capabilities:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        prompt_text = json.dumps(messages, ensure_ascii=False)
        usage = _usage_from_response(resp, prompt_text, content)
        data = _extract_json(content)
        return data, usage

    def chat_json(self, messages, schema=None):
        return self._chat(messages, schema)

    def chat_with_images(self, messages, images, schema=None):
        import base64

        content: list[dict] = [{"type": "text", "text": messages[-1]["content"]}]
        for img in images:
            b64 = base64.b64encode(Path(img).read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        msgs = messages[:-1] + [{"role": "user", "content": content}]
        return self._chat(msgs, schema)

    def chat_with_video(self, messages, video_path, schema=None):
        # OpenAI 兼容端点多数不支持视频；降级抽帧（由调用方决定）。这里直接报错。
        raise NotImplementedError(f"模型 {self.spec.name} 的 openai 协议不支持视频直传，请用抽帧或 gemini_native")


class GeminiNativeClient:
    """google-genai 原生协议，仅 video 能力需要（上传视频文件）。"""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError("gemini_native 需要 google-genai: uv pip install 'autocut-ai[gemini]'") from e
        self._genai = genai
        self._client = genai.Client(api_key=spec.resolved_api_key())

    def chat_json(self, messages, schema=None):
        text = "\n".join(m["content"] for m in messages)
        resp = self._client.models.generate_content(model=self.spec.model, contents=text)
        content = resp.text or ""
        usage = {"input": _estimate_tokens(text), "output": _estimate_tokens(content), "cached": 0}
        return _extract_json(content), usage

    def chat_with_images(self, messages, images, schema=None):
        text = "\n".join(m["content"] for m in messages)
        parts: list[Any] = [text]
        for img in images:
            parts.append(self._genai.types.Part.from_bytes(data=Path(img).read_bytes(), mime_type="image/jpeg"))
        resp = self._client.models.generate_content(model=self.spec.model, contents=parts)
        content = resp.text or ""
        usage = {"input": _estimate_tokens(text), "output": _estimate_tokens(content), "cached": 0}
        return _extract_json(content), usage

    def chat_with_video(self, messages, video_path, schema=None):
        text = "\n".join(m["content"] for m in messages)
        video_file = self._client.files.upload(file=str(video_path))
        # 等待处理完成
        import time as _t

        while video_file.state.name == "PROCESSING":
            _t.sleep(2)
            video_file = self._client.files.get(name=video_file.name)
        resp = self._client.models.generate_content(
            model=self.spec.model,
            contents=[video_file, text],
        )
        content = resp.text or ""
        usage = {"input": _estimate_tokens(text), "output": _estimate_tokens(content), "cached": 0}
        return _extract_json(content), usage


# ---------------------------------------------------------------------------
# Mock 客户端（确定性假数据，测试专用）
# ---------------------------------------------------------------------------


class MockModelClient:
    """按角色返回确定性 JSON，模拟 token 用量。"""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self.call_count = 0

    def _mock_output(self, messages: list[dict]) -> dict:
        """根据 __role__ 标记或内容关键字返回结构合理的假数据。"""
        text = json.dumps(messages, ensure_ascii=False)
        # 优先用调用方注入的 __role__ 标记（避免多角色共享关键字误判）
        m = re.search(r'__role__\\?"\s*:\s*\\?"(\w+)', text)
        if m:
            return self._by_role(m.group(1))
        # 依据提示词中的角色关键字
        if "name_fix" in text or "人名" in text or "说话人" in text:
            return {"corrected": [], "speaker_map": {"SPEAKER_00": "张三"}}
        if "outline" in text or "大纲" in text or "剧情节点" in text:
            return {
                "logline": "一个测试故事",
                "characters": ["张三", "李四"],
                "nodes": [
                    {"id": "n1", "summary": "开端", "function": "铺垫", "time_start": 0, "time_end": 15, "importance": 5},
                    {"id": "n2", "summary": "发展", "function": "冲突", "time_start": 15, "time_end": 35, "importance": 7},
                    {"id": "n3", "summary": "高潮", "function": "高潮", "time_start": 35, "time_end": 55, "importance": 10},
                ],
            }
        if "scout" in text or "候选" in text:
            return {"candidates": [
                {"id": "c1", "outline_node_id": "n1", "src_in": 0, "src_out": 5, "reason": "开端"},
                {"id": "c2", "outline_node_id": "n2", "src_in": 18, "src_out": 24, "reason": "冲突"},
                {"id": "c3", "outline_node_id": "n3", "src_in": 38, "src_out": 46, "reason": "高潮"},
            ]}
        if "vision" in text or "画面" in text:
            return {"descriptions": [{"candidate_id": "c1", "summary": "画面内容", "usable": True}]}
        if "reviewer" in text or "自审" in text or "裁判" in text:
            return {"pass": True, "issues": [], "improved_timeline": None, "scores": {}}
        if "rewrite" in text or "改写" in text:
            return {"text": "改写后的文案"}
        if "memory" in text or "记忆" in text:
            return {"characters": [], "recap": "前情提要", "speaker_map": {}}
        if "metadata" in text or "标题" in text:
            return {"titles": ["标题一", "标题二", "标题三"], "description": "简介", "tags": ["影视", "解说"]}
        if "style" in text or "风格" in text:
            return {"style_guide": "# 风格指南\n- 第二人称开场\n- 短句"}
        if "fixer" in text or "修" in text and "JSON" in text:
            return {}
        # writer：生成时间轴
        if "writer" in text or "时间轴" in text or "解说" in text:
            return self._by_role("writer")
        return {"ok": True}

    def _by_role(self, role: str) -> dict:
        """按角色名返回确定性假数据。"""
        if role == "name_fix":
            return {"corrected": [], "speaker_map": {"SPEAKER_00": "张三"}}
        if role == "outline":
            return {
                "logline": "一个测试故事",
                "characters": ["张三", "李四"],
                "nodes": [
                    {"id": "n1", "summary": "开端", "function": "铺垫", "time_start": 0, "time_end": 15, "importance": 5},
                    {"id": "n2", "summary": "发展", "function": "冲突", "time_start": 15, "time_end": 35, "importance": 7},
                    {"id": "n3", "summary": "高潮", "function": "高潮", "time_start": 35, "time_end": 55, "importance": 10},
                ],
            }
        if role == "scout":
            return {"candidates": [
                {"id": "c1", "outline_node_id": "n1", "src_in": 0, "src_out": 5, "reason": "开端"},
                {"id": "c2", "outline_node_id": "n2", "src_in": 18, "src_out": 24, "reason": "冲突"},
                {"id": "c3", "outline_node_id": "n3", "src_in": 38, "src_out": 46, "reason": "高潮"},
            ]}
        if role == "vision":
            return {"descriptions": [{"candidate_id": "c1", "summary": "画面内容", "usable": True}]}
        if role == "writer":
            return {
                "title": "测试解说",
                "segments": [
                    {"id": "seg1", "type": "narration", "outline_node_id": "n1", "text": "注意看这个男人叫小帅", "emotion": "平叙", "src_in": 0, "src_out": 5},
                    {"id": "seg2", "type": "original", "outline_node_id": "n2", "src_in": 18, "src_out": 24},
                    {"id": "seg3", "type": "narration", "outline_node_id": "n3", "text": "他发现了惊人的秘密", "emotion": "紧张", "src_in": 38, "src_out": 46},
                ],
            }
        if role == "reviewer":
            return {"pass": True, "issues": [], "improved_timeline": None, "scores": {"hook": 8}}
        if role == "rewrite":
            return {"text": "改写后的文案"}
        if role == "memory":
            return {"characters": [], "recap": "前情提要", "speaker_map": {}}
        if role == "metadata":
            return {"titles": ["标题一", "标题二", "标题三"], "description": "简介", "tags": ["影视", "解说"]}
        if role == "style":
            return {"style_guide": "# 风格指南\n- 第二人称开场\n- 短句"}
        if role == "fixer":
            return {}
        return {"ok": True}

    def _chat(self, messages, schema=None) -> tuple[dict, dict]:
        self.call_count += 1
        data = self._mock_output(messages)
        prompt_text = json.dumps(messages, ensure_ascii=False)
        completion = json.dumps(data, ensure_ascii=False)
        usage = {
            "input": _estimate_tokens(prompt_text),
            "output": _estimate_tokens(completion),
            "cached": 0,
        }
        return data, usage

    def chat_json(self, messages, schema=None):
        return self._chat(messages, schema)

    def chat_with_images(self, messages, images, schema=None):
        return self._chat(messages, schema)

    def chat_with_video(self, messages, video_path, schema=None):
        return self._chat(messages, schema)


_PROTOCOL_CLASSES = {
    "openai": OpenAIProtocolClient,
    "gemini_native": GeminiNativeClient,
    "mock": MockModelClient,
}


def make_client(spec: ModelSpec) -> ModelClient:
    cls = _PROTOCOL_CLASSES.get(spec.protocol)
    if cls is None:
        # 尝试 entry points 注册的协议
        from .providers import get_model_protocol

        factory = get_model_protocol(spec.protocol)
        return factory(spec) if callable(factory) else factory
    return cls(spec)


def _extract_json(content: str) -> dict:
    """从模型输出提取 JSON（容忍 markdown 代码块包裹）。"""
    content = content.strip()
    if content.startswith("```"):
        # 去掉 ```json ... ```
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    # 找第一个 { 到最后一个 }
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"模型输出非 JSON: {content[:200]}")
    return json.loads(content[start : end + 1])


# ---------------------------------------------------------------------------
# 角色调度器
# ---------------------------------------------------------------------------


class ModelCaller:
    """按角色调度模型：fallback、退避重试、限流、JSON 校验、缓存、计费。"""

    def __init__(
        self,
        config: Config,
        roles: dict[str, list[str]],
        workdir: Path,
        profile_vision_mode: str = "frames",
    ) -> None:
        self.config = config
        self.roles = roles
        self.workdir = workdir
        self.vision_mode = profile_vision_mode
        self.tracker = CostTracker(config.limits.max_cost_per_episode)
        self.cache_dir = workdir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, ModelClient] = {}
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._locks: dict[str, threading.Lock] = {}

    # ---- 客户端管理 ------------------------------------------------------
    def _get_client(self, name: str) -> ModelClient:
        if name in self._clients:
            return self._clients[name]
        spec = self.config.model_by_name(name)
        if spec is None:
            raise RuntimeError(f"模型未注册: {name}")
        client = make_client(spec)
        self._clients[name] = client
        self._semaphores[name] = threading.Semaphore(max(1, spec.max_concurrency))
        return client

    # ---- 缓存 --------------------------------------------------------------
    def _cache_key(self, kind: str, role: str, model: str, payload: str, extra: str = "") -> str:
        return text_hash_cache_key(kind, role, model, payload, extra)

    def _cache_get(self, key: str) -> dict | None:
        f = self.cache_dir / f"{key}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
        return None

    def _cache_put(self, key: str, value: dict) -> None:
        # 缓存文件不存请求头/密钥，只存模型输出与 token
        (self.cache_dir / f"{key}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )

    # ---- 核心调用 ----------------------------------------------------------
    def call(
        self,
        role: str,
        messages: list[dict],
        schema: type[BaseModel] | None = None,
        *,
        images: list[Path] | None = None,
        video: Path | None = None,
        prompt_name: str = "",
        prompt_version: int = 0,
        prompt_hash: str = "",
    ) -> tuple[BaseModel | dict, str]:
        """调用角色的模型（带 fallback/重试/缓存/校验/计费）。

        返回 (解析后的数据, 实际使用的模型名)。
        """
        if role not in ROLE_NAMES:
            raise ValueError(f"未知角色: {role}")
        names = self.roles.get(role) or []
        if not names:
            raise RuntimeError(f"角色 [{role}] 未分配模型，请在 [roles] 或 profile 中配置")

        # 注入角色标记（供 mock 模型按角色返回；真实模型会忽略此 system 前缀的语义）
        messages = self._inject_role_tag(messages, role)

        payload = json.dumps({"messages": messages, "images": [str(p) for p in images or []],
                              "video": str(video) if video else "", "schema": schema.__name__ if schema else ""},
                             ensure_ascii=False, sort_keys=True)

        last_err: Exception | None = None
        for model_name in names:
            if self.tracker.over_budget():
                raise BudgetExceededError(
                    f"累计费用 ${self.tracker.total_cost:.4f} 超过上限 ${self.tracker.max_cost:.2f}，已停止"
                )
            # 请求级缓存（按内容哈希）
            cache_key = self._cache_key("call", role, model_name, payload, prompt_hash)
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.info("[缓存命中] %s/%s", role, model_name)
                self.tracker.add(CallRecord(
                    role=role, model=model_name, prompt_name=prompt_name,
                    prompt_version=prompt_version, prompt_hash=prompt_hash,
                    input_tokens=cached["usage"]["input"], output_tokens=cached["usage"]["output"],
                    cached_tokens=cached["usage"].get("cached", 0),
                    cost=0.0, cache_hit=True,
                ))
                return self._validate(cached["data"], schema, role), model_name

            try:
                data, usage = self._call_one_model(role, model_name, messages, schema, images, video)
            except Exception as e:  # noqa: BLE001
                logger.warning("模型 %s 调用失败(%s)，尝试下一个 fallback", model_name, sanitize(e))
                last_err = e
                continue

            # JSON 校验（pydantic）
            try:
                validated = self._validate(data, schema, role)
            except ValidationError as ve:
                # 回传重试最多 3 次，再交 fixer
                validated = self._repair_with_retries(
                    role, model_name, messages, data, schema, ve, images, video
                )
                data = validated if isinstance(validated, dict) else validated.model_dump()

            # 计费
            cost = self._compute_cost(model_name, usage)
            self.tracker.add(CallRecord(
                role=role, model=model_name, prompt_name=prompt_name,
                prompt_version=prompt_version, prompt_hash=prompt_hash,
                input_tokens=usage["input"], output_tokens=usage["output"],
                cached_tokens=usage.get("cached", 0), cost=cost,
            ))
            self._cache_put(cache_key, {"data": data if isinstance(data, dict) else data, "usage": usage})
            return self._validate(data, schema, role), model_name

        raise RuntimeError(f"角色 [{role}] 所有模型均失败。最后错误: {sanitize(last_err)}")

    @staticmethod
    def _inject_role_tag(messages: list[dict], role: str) -> list[dict]:
        """在首条 system 消息头部注入 __role__ 标记（不破坏字节级缓存前缀，追加在最前）。"""
        tag = {"role": "system", "content": f'{{"__role__": "{role}"}}'}
        return [tag, *messages]

    def _call_one_model(self, role, model_name, messages, schema, images, video):
        """限流 + 指数退避重试单个模型。"""
        client = self._get_client(model_name)
        sem = self._semaphores[model_name]
        delays = [1, 2, 4]
        last_err: Exception | None = None
        for attempt, delay in enumerate([0, *delays]):
            if delay:
                time.sleep(delay)
            try:
                with sem:
                    if video is not None:
                        return client.chat_with_video(messages, video, schema)
                    if images:
                        return client.chat_with_images(messages, images, schema)
                    return client.chat_json(messages, schema)
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e)
                # 429/5xx 重试；其他错误直接抛
                if any(k in msg for k in ("429", "500", "502", "503", "504", "timeout", "rate")):
                    logger.info("模型 %s 第 %d 次重试(%s)", model_name, attempt + 1, sanitize(msg[:80]))
                    continue
                raise
        raise last_err or RuntimeError("未知错误")

    def _validate(self, data, schema, role):
        if schema is None:
            return data
        if isinstance(data, schema):
            return data
        return schema.model_validate(data)

    def _repair_with_retries(self, role, model_name, messages, data, schema, ve, images, video):
        """JSON 校验失败：回传错误重试最多 3 次，仍失败交 fixer。"""
        err_text = str(ve)
        cur_messages = list(messages)
        for attempt in range(3):
            logger.info("角色 %s 输出校验失败，第 %d 次回传重试", role, attempt + 1)
            cur_messages = cur_messages + [
                {"role": "user", "content": f"上次输出不符合 JSON schema，错误：{err_text}\n请严格按 schema 重新输出 JSON。"}
            ]
            try:
                new_data, _usage = self._call_one_model(role, model_name, cur_messages, schema, images, video)
                return self._validate(new_data, schema, role)
            except (ValidationError, ValueError) as e:
                err_text = str(e)
                continue
        # fixer 修
        logger.info("角色 %s 重试仍失败，交 fixer 修复", role)
        return self._fixer_repair(schema, data, err_text)

    def _fixer_repair(self, schema, bad_data, err_text):
        """用 fixer 角色把坏 JSON 修好。"""
        fixer_msgs = [
            {"role": "system", "content": "你是 JSON 修复助手。把不符合 schema 的 JSON 修正为合法且符合 schema 的 JSON，只输出 JSON。"},
            {"role": "user", "content": (
                f"目标 schema: {schema.__name__}\n"
                f"schema 定义: {json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
                f"校验错误: {err_text}\n"
                f"坏的 JSON: {json.dumps(bad_data, ensure_ascii=False, default=str)[:4000]}\n"
                "请输出修正后的 JSON。"
            )},
        ]
        names = self.roles.get("fixer") or self.roles.get("writer") or []
        for name in names:
            try:
                client = self._get_client(name)
                fixed, _ = client.chat_json(fixer_msgs, schema)
                return schema.model_validate(fixed)
            except Exception as e:  # noqa: BLE001
                logger.warning("fixer %s 修复失败: %s", name, sanitize(e))
                continue
        raise RuntimeError(f"JSON 校验失败且 fixer 无法修复: {err_text[:300]}")

    def _compute_cost(self, model_name: str, usage: dict) -> float:
        spec = self.config.model_by_name(model_name)
        if spec is None:
            return 0.0
        # 缓存命中 token 通常半价或不计费，这里按 0 计（已命中上下文缓存）
        billable_in = max(0, usage["input"] - usage.get("cached", 0))
        return billable_in / 1e6 * spec.price_in + usage["output"] / 1e6 * spec.price_out


# ---------------------------------------------------------------------------
# 辅助：模型列表与测试
# ---------------------------------------------------------------------------


def list_models_table(config: Config) -> str:
    lines = [f"{'name':<20}{'protocol':<14}{'capabilities':<28}{'price(in/out)':<16}model"]
    for m in config.models:
        caps = ",".join(m.capabilities)
        lines.append(f"{m.name:<20}{m.protocol:<14}{caps:<28}{m.price_in}/{m.price_out:<10}{m.model}")
    return "\n".join(lines)


def test_model(config: Config, name: str) -> tuple[bool, str]:
    spec = config.model_by_name(name)
    if spec is None:
        return False, f"模型未注册: {name}"
    try:
        client = make_client(spec)
        if spec.protocol == "mock":
            data, _ = client.chat_json([{"role": "user", "content": "ping"}], None)
            return True, f"mock 模型 {name} 正常（返回 {list(data)[:3]}）"
        # 真实模型：发一个最小请求
        data, usage = client.chat_json(
            [{"role": "user", "content": "只回复 JSON：{\"ok\":true}"}], None
        )
        return True, f"模型 {name} 连通正常，输出 {str(data)[:60]}，输入 token≈{usage['input']}"
    except Exception as e:  # noqa: BLE001
        return False, f"模型 {name} 连接失败: {sanitize(e)}"


def transcribe_audio(config: Config, audio: Path, diarize: bool = True) -> dict:
    """ASR 转写（OpenAI 兼容）。返回 {segments:[{start,end,text}], speakers?}。"""

    from openai import OpenAI

    asr = config.asr
    # 优先用指向的模型 spec，否则用独立 base_url
    if asr.model and config.model_by_name(asr.model):
        spec = config.model_by_name(asr.model)
        client = OpenAI(base_url=spec.base_url or None, api_key=spec.resolved_api_key())
        model_name = spec.model
    else:
        client = OpenAI(base_url=asr.base_url or None, api_key=asr.api_key or "not-needed")
        model_name = asr.model_name

    with audio.open("rb") as f:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "file": f,
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["segment"],
        }
        resp = client.audio.transcriptions.create(**kwargs)
    segments = [
        {"start": s.start, "end": s.end, "text": s.text}
        for s in getattr(resp, "segments", [])
    ]
    result: dict[str, Any] = {"segments": segments}
    # 说话人分离（部分端点支持，如 Groq diarize）
    speakers = getattr(resp, "speakers", None)
    if diarize and speakers:
        result["speakers"] = {getattr(s, "speaker", f"SPEAKER_{i}"): getattr(s, "speaker", f"SPEAKER_{i}") for i, s in enumerate(speakers)}
    return result
