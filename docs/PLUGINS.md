# AutoCut 插件开发指南

AutoCut 的每个环节都是可替换 provider，通过注册表 + 配置选择；第三方包可通过 Python entry points 注册，无需改 AutoCut 源码。

## Provider 组与接口

| 组（entry point group） | 接口方法 | 说明 |
|---|---|---|
| `autocut.providers.subtitle` | `get_subtitles(video, workdir, config) -> (srt_path, speakers_or_None)` | 字幕获取 |
| `autocut.providers.shots` | `detect(video, workdir, config) -> list[Shot]` | 镜头切分 |
| `autocut.providers.loudness` | `analyze(video, workdir, config) -> list[HighEnergyRange]` | 响度分析 |
| `autocut.providers.hardsub` | `detect(video, srt, workdir, config) -> HardsubInfo` | 硬字幕检测 |
| `autocut.providers.tts` | `synth(text, voice, emotion, workdir, config, seg_id) -> (audio_path, words)` | 配音合成 |
| `autocut.providers.renderer` | `render(timeline, assets, workdir, config) -> Path` | 渲染 |
| `autocut.model_protocols` | 工厂函数 `(spec) -> ModelClient` | 新增模型协议 |

## 最小示例：自定义 TTS provider

```python
# my_autocut_plugin/__init__.py
from pathlib import Path
from typing import Any

class MyTTSProvider:
    name = "my_tts"

    def synth(self, text: str, voice: str, emotion: str, workdir: Path, config, seg_id: str):
        """合成音频，返回 (音频路径, 词级时间戳)。"""
        out = workdir / f"tts_{seg_id}.wav"
        # ... 调用你的 TTS 服务 ...
        words = [{"text": ch, "start": i * 0.2, "end": (i + 1) * 0.2} for i, ch in enumerate(text)]
        return out, words
```

在 `pyproject.toml` 注册 entry point：

```toml
[project.entry-points."autocut.providers.tts"]
my_tts = "my_autocut_plugin:MyTTSProvider"
```

安装后，配置中即可使用：

```toml
[providers]
tts = "my_tts"
```

## 最小示例：自定义模型协议

```python
# my_protocol/__init__.py
class MyProtocolClient:
    def __init__(self, spec):
        self.spec = spec

    def chat_json(self, messages, schema=None):
        # 返回 (dict, {"input": int, "output": int, "cached": int})
        ...

    def chat_with_images(self, messages, images, schema=None): ...
    def chat_with_video(self, messages, video_path, schema=None): ...
```

```toml
[project.entry-points."autocut.model_protocols"]
my_protocol = "my_protocol:MyProtocolClient"
```

```toml
[[models]]
name = "my-model"
protocol = "my_protocol"
```

## 约定

- **数据契约**：所有结构用 `autocut.contracts` 中的 pydantic 模型（`Shot`、`HighEnergyRange`、`HardsubInfo`、`Timeline` 等）。
- **路径**：一律 `pathlib.Path`，支持中文与空格。
- **脱敏**：日志中勿打印 api_key；用 `autocut.logging_utils.sanitize`。
- **词级时间戳**：TTS 无词级时间戳时，用 `autocut.stages.tts_providers._even_word_timestamps` 均分兜底。
- **可关闭**：可选 provider 在配置中可换成 `none` / 关闭项，流水线须照常跑通。

## 内置 provider 一览

见 `config.example.toml` 的 `[providers]` 注释，或运行：
```bash
autocut mcp  # autocut_list_providers 工具
# 或
python -c "from autocut.providers import list_providers; print(list_providers())"
```
