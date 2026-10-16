"""TTS provider：edge_tts（默认）/ openai_tts / custom_http。含读音字典与情绪映射。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..utils import run_cmd

logger = logging.getLogger("autocut.stages.tts_providers")


# ---------------------------------------------------------------------------
# 读音字典
# ---------------------------------------------------------------------------


def load_pronunciation_dict(path: Path | None) -> dict[str, str]:
    """读取读音字典：每行「原词=替换读法或 SSML phoneme」。"""
    if not path or not Path(path).exists():
        return {}
    d: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def apply_pronunciation(text: str, mapping: dict[str, str]) -> str:
    """应用读音字典（字幕显示仍用原词，这里只改 TTS 输入）。"""
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


# ---------------------------------------------------------------------------
# edge-tts
# ---------------------------------------------------------------------------


class EdgeTTSProvider:
    """edge-tts（默认）：免费、带词级时间戳（WordBoundary）。"""

    name = "edge_tts"

    def synth(self, text: str, voice: str, emotion: str, workdir: Path, config, seg_id: str):
        import asyncio

        import edge_tts

        rate = config.tts.emotion_rate.get(emotion, config.tts.rate)
        pitch = config.tts.emotion_pitch.get(emotion, "+0Hz")
        out = workdir / f"tts_{seg_id}.mp3"
        words: list[dict[str, Any]] = []

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            with out.open("wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        words.append({
                            "text": chunk["text"],
                            "start": chunk["offset"] / 1e7,  # 100ns → s
                            "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                        })

        try:
            asyncio.run(_run())
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"edge-tts 合成失败: {e}") from e
        return out, words


class OpenAITTSProvider:
    """OpenAI 兼容 TTS（tts-1 等）。无词级时间戳 → 均分。"""

    name = "openai_tts"

    def synth(self, text: str, voice: str, emotion: str, workdir: Path, config, seg_id: str):
        from openai import OpenAI

        tts_cfg = config.tts
        # 复用 asr 端点或独立配置
        base_url = getattr(tts_cfg, "base_url", "") or config.asr.base_url or None
        api_key = getattr(tts_cfg, "api_key", "") or config.asr.api_key or "not-needed"
        client = OpenAI(base_url=base_url, api_key=api_key)
        out = workdir / f"tts_{seg_id}.mp3"
        model = tts_cfg.model or "tts-1"
        resp = client.audio.speech.create(model=model, voice=voice or "alloy", input=text)
        out.write_bytes(resp.content)
        words = _even_word_timestamps(text, _probe_duration(out, config))
        return out, words


class CustomHTTPTTSProvider:
    """custom_http：URL 模板 POST（CosyVoice 等自部署）。返回 wav/mp3 或 base64。"""

    name = "custom_http"

    def synth(self, text: str, voice: str, emotion: str, workdir: Path, config, seg_id: str):
        import json
        import urllib.request

        url_template = config.tts.custom_http_url
        if not url_template:
            raise RuntimeError("custom_http 需要配置 tts.custom_http_url")
        url = url_template.format(text=text, voice=voice, emotion=emotion)
        payload = json.dumps({"text": text, "voice": voice, "emotion": emotion}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        out = workdir / f"tts_{seg_id}.wav"
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read()
        if b"audio" in ctype.encode() or ctype == "":
            out.write_bytes(body)
        else:
            # JSON 含 base64
            import base64

            data = json.loads(body)
            out.write_bytes(base64.b64decode(data["audio"]))
        words = _even_word_timestamps(text, _probe_duration(out, config))
        return out, words


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _probe_duration(audio: Path, config) -> float:
    from ..utils import find_ffprobe

    try:
        proc = run_cmd(
            [find_ffprobe(config.general.ffmpeg_path), "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", str(audio)],
            timeout=30,
        )
        return float(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return max(1.0, len(str(audio)) * 0.0 + 2.0)


def _even_word_timestamps(text: str, duration: float) -> list[dict[str, Any]]:
    """无词级时间戳时按字均分。"""
    n = max(1, len(text))
    return [{"text": ch, "start": duration * i / n, "end": duration * (i + 1) / n} for i, ch in enumerate(text)]


# ---------------------------------------------------------------------------
# TTS 调度（读音字典 + 情绪 + 停顿）
# ---------------------------------------------------------------------------


def synth_segment(text: str, voice: str, emotion: str, workdir: Path, config, seg_id: str, provider_name: str):
    """合成一个段：应用读音字典 → 调 provider → 返回 (音频路径, 词级时间戳, 时长)。"""
    from ..providers import get

    pron = load_pronunciation_dict(config.tts.pronunciation_file)
    tts_text = apply_pronunciation(text, pron)
    prov = get("tts", provider_name)
    audio, words = prov.synth(tts_text, voice, emotion, workdir, config, seg_id)
    duration = _probe_duration(audio, config)
    return audio, words, duration
