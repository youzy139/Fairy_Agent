# PyInstaller spec：构建单文件悬浮球 GUI（Fairy.exe）
# 用法：pyinstaller Fairy.spec --noconfirm

from PyInstaller.utils.hooks import collect_all

# 收集悬浮球图片等包内资源（直接指向源码目录，兼容 editable 安装）
datas = [("src/fairy/ui/assets", "fairy/ui/assets")]
binaries = []
hiddenimports = []

# 语音链依赖（voice/ 内为函数内延迟 import，静态分析扫不到，需显式收集）：
# faster-whisper（STT）、sounddevice（录音，含 PortAudio DLL）、edge-tts（TTS）。
# 连带 ctranslate2 / onnxruntime（VAD）/ tokenizers / av / huggingface_hub。
for pkg in (
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "tokenizers",
    "av",
    "huggingface_hub",
    "sounddevice",
    "edge_tts",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# TTS 播放用的 QtMultimedia 在代码中是延迟 import，需显式声明（触发 Qt 插件收集）
hiddenimports += ["PySide6.QtMultimedia"]


a = Analysis(
    ["scripts/fairy_gui_launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Fairy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 窗口程序，不弹黑色控制台
    disable_windowed_traceback=False,
    icon="assets/fairy.ico",
)
