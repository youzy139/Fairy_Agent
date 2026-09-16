# PyInstaller spec：构建单文件悬浮球 GUI（Fairy.exe）
# 用法：pyinstaller Fairy.spec --noconfirm

from PyInstaller.utils.hooks import collect_data_files

# 收集悬浮球图片等包内资源（直接指向源码目录，兼容 editable 安装）
datas = [("src/fairy/ui/assets", "fairy/ui/assets")]


a = Analysis(
    ["scripts/fairy_gui_launcher.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
