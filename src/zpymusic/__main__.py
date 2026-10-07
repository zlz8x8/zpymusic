"""``python -m zpymusic`` 入口（等同 ``zpymusic`` 命令行）。

也支持按文件路径直接执行（``python src/zpymusic/__main__.py``），
用 ``__package__`` 守卫处理相对导入失败的问题，理由见 :mod:`zpymusic.gui`。
"""

from __future__ import annotations

import sys

if __package__ in (None, ""):
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from zpymusic.cli import main  # noqa: E402
else:
    from .cli import main

if __name__ == "__main__":
    sys.exit(main())
