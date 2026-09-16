"""PyInstaller 打包入口：等价于 ``fairy --gui``。

打包后双击 Fairy.exe 即启动悬浮球；``.env`` 放在 exe 同目录即可被读取。
"""

from __future__ import annotations

import sys

from fairy.main import main

if __name__ == "__main__":
    sys.exit(main(["--gui"]))
