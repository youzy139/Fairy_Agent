"""语音合成（TTS）：edge-tts 生成 MP3，播放由调用方负责。

edge-tts 走微软在线语音服务（免费、中文声音自然），属于 network 级行为：
合成事件应写审计日志（由调用方统一记录）。播放放在 GUI 层
（QtMultimedia），这里只负责「文本 → MP3 文件」。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

# 默认声音：晓晓（中文女声，自然度好）
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
# 送入合成的文本上限：回复太长时只读开头，保证按键说话场景的响应速度
MAX_SPEAK_CHARS = 300


class TtsError(RuntimeError):
    """合成失败（多为网络问题）。"""


def truncate_for_speech(text: str, max_chars: int = MAX_SPEAK_CHARS) -> str:
    """超长回复截断后朗读（附加省略提示）。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "。回复较长，完整内容请看屏幕。"


class TextToSpeech:
    """edge-tts 封装：``synthesize(text) -> MP3 文件路径``。

    合成结果按内容哈希缓存到 ``~/.fairy/tts``，相同文本不重复请求。
    """

    def __init__(self, voice: str = DEFAULT_VOICE, cache_dir: Path | None = None) -> None:
        self._voice = voice
        self._cache_dir = cache_dir or Path("~/.fairy/tts").expanduser()

    def synthesize(self, text: str) -> Path:
        """合成文本为 MP3 文件，返回路径；空文本返回 None 由调用方跳过。"""
        text = truncate_for_speech(text)
        if not text:
            raise TtsError("没有可朗读的文本")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{self._voice}|{text}".encode()).hexdigest()[:16]
        out = self._cache_dir / f"{digest}.mp3"
        if out.exists() and out.stat().st_size > 0:
            return out  # 命中缓存

        try:
            asyncio.run(self._save(text, out))
        except TtsError:
            raise
        except Exception as exc:
            out.unlink(missing_ok=True)  # 失败不留半截文件
            raise TtsError(f"语音合成失败（需联网）：{exc}") from exc
        return out

    async def _save(self, text: str, out: Path) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(text, self._voice)
        await communicate.save(str(out))
