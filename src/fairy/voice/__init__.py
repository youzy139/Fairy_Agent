"""语音子系统：按键说话（STT）+ 语音回复（TTS）。

设计原则：
- 可选依赖：faster-whisper / sounddevice / edge-tts 仅在 ``fairy-agent[voice]``
  额外组件中安装；未安装时语音功能优雅降级（入口给出明确提示），不影响其他功能。
- 本地优先：STT 用 faster-whisper 在本地识别，音频不出本机；
  TTS 用 edge-tts（需联网，文本上传微软语音服务，属 network 级行为并写审计日志）。
"""

from __future__ import annotations


def voice_available() -> bool:
    """语音依赖是否齐全（faster-whisper + sounddevice + edge-tts）。"""
    try:
        import edge_tts  # noqa: F401
        import faster_whisper  # noqa: F401
        import sounddevice  # noqa: F401
    except ImportError:
        return False
    return True


# 依赖缺失时给用户的提示（GUI Toast / CLI 共用）
VOICE_MISSING_HINT = "语音依赖未安装，请先执行：pip install fairy-agent[voice]"
