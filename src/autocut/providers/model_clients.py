"""模型协议客户端注册（M4 补全真实实现；M1 先注册占位以便协议表可见）。"""

from __future__ import annotations

from . import register_model_protocol

_REGISTERED = False


def register_protocols(force: bool = False) -> None:
    global _REGISTERED
    if _REGISTERED and not force:
        return
    _REGISTERED = True

    def _lazy(module: str, cls: str):
        def factory():
            import importlib

            mod = importlib.import_module(module)
            return getattr(mod, cls)()

        return factory

    register_model_protocol("openai", _lazy("autocut.model_client", "OpenAIProtocolClient"))
    register_model_protocol("gemini_native", _lazy("autocut.model_client", "GeminiNativeClient"))
    register_model_protocol("mock", _lazy("autocut.model_client", "MockModelClient"))


register_protocols()
