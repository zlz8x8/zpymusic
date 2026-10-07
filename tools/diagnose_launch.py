"""排障：验证"按文件路径执行"与"按模块执行"两种启动方式。

背景：``python src/zpymusic/gui.py`` 曾报

    ImportError: attempted relative import with no known parent package

原因是直接执行文件时 Python 不设置 ``__package__``，相对导入失效。
``gui.py`` / ``__main__.py`` 现在用 ``__package__`` 守卫 + 绝对导入解决。

用法::

    python tools/diagnose_launch.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PY = sys.executable
GUI = SRC / "zpymusic" / "gui.py"
MAIN = SRC / "zpymusic" / "__main__.py"

# (说明, 命令, 额外环境变量, cwd, 期望“能正常启动”)
CASES: list[tuple[str, list[str], dict[str, str], str, bool]] = [
    (
        "1) 直接执行文件 gui.py --selftest（曾报相对导入错误）",
        [PY, str(GUI), "--selftest"],
        {"QT_QPA_PLATFORM": "offscreen"},
        str(REPO),
        True,
    ),
    ("2) python -m zpymusic.gui --selftest", [PY, "-m", "zpymusic.gui", "--selftest"],
     {"PYTHONPATH": str(SRC), "QT_QPA_PLATFORM": "offscreen"}, str(REPO), True),
    ("3) 直接执行文件 __main__.py conversions", [PY, str(MAIN), "conversions"],
     {}, str(REPO), True),
    ("4) python -m zpymusic conversions", [PY, "-m", "zpymusic", "conversions"],
     {"PYTHONPATH": str(SRC)}, str(REPO), True),
    ("5) 反例：直接执行 core 子模块（应当失败，说明这是通用机制）",
     [PY, str(SRC / "zpymusic" / "cli.py"), "probe"], {}, str(REPO), False),
]


def main() -> int:
    print(f"仓库：{REPO}")
    print(f"解释器：{PY}\n")
    failures = 0
    for label, cmd, extra, cwd, should_work in CASES:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.update(extra)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace",
                timeout=60, env=env, cwd=cwd,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            started = proc.returncode == 0
            last = out.splitlines()[-1] if out else "(无输出)"
            verdict = "OK  " if started == should_work else "FAIL"
            if verdict == "FAIL":
                failures += 1
            print(f"{verdict} {label}")
            print(f"       退出码={proc.returncode}  末行={last[:110]}")
        except subprocess.TimeoutExpired:
            print(f"TIMEOUT {label}")
        print()
    print(f"共 {len(CASES)} 项，失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
