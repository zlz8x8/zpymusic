"""人工验收：源文件「预览」按钮与两类非模态预览窗口（M5 需求变更 / F3.8）。

自动化测试（``tests/test_m5_preview.py``）已经覆盖了状态机与逻辑；
本脚本用于**目视验收**：跑起真实主窗口，对四种源文件逐个点「预览」，
打印状态并把窗口截图存盘 —— 想确认"窗口长得对不对、乐谱有没有画出来"时用它。

用法::

    $env:QT_QPA_PLATFORM = "offscreen"      # 无显示环境（截图仍可用）
    python tools/check_preview_windows.py [套件名] [输出目录]

Note:
    离屏平台下 Qt 常常拿不到中文字体，界面文字会显示成方框（本机实测整个程序
    都如此，与预览窗口无关）；乐谱本身是矢量路径，不受影响。要看真实字体，
    在有显示的桌面上不加 ``QT_QPA_PLATFORM`` 直接跑。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.common.config import AppConfig  # noqa: E402
from zpymusic.ui.main_window import MainWindow  # noqa: E402

MUSICXML_DIR = REPO / "staff" / "musicxml"
SUITES = REPO / "staff" / "suites"


def _pump(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "canon-in-d-easy"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / ".zpy-tmp" / "preview-shots"
    out_dir.mkdir(parents=True, exist_ok=True)

    suite = SUITES / base
    musicxml = next(
        (p for p in MUSICXML_DIR.glob(f"{base}.*") if p.suffix.lower() in {".mxl", ".xml", ".musicxml"}),
        None,
    )
    files = {
        "musicxml": musicxml,
        "svg": next(iter(sorted((suite / "svg").glob("sys-*.svg"))), None),
        "mp3": next(iter(sorted(suite.glob("*.mp3"))), None),
        "midi": next(iter(sorted(suite.glob("*.mid"))), None),
    }
    if not all(files.values()):
        print(f"!! 套件 {base} 缺少可用于验收的文件：{ {k: str(v) for k, v in files.items()} }")
        return 2

    app = QApplication(sys.argv)
    win = MainWindow(AppConfig.load())
    win.resize(1280, 880)
    win.show()
    app.processEvents()
    tab = win.tab_convert
    win.tabs.setCurrentWidget(tab)
    _pump(app, 0.3)

    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{'OK ' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures += 1

    print("=== 1) 按钮位置与初始状态 ===")
    gv = tab.btn_preview.parentWidget().layout()
    row = next(
        gv.itemAt(i).layout()
        for i in range(gv.count())
        if gv.itemAt(i).layout() is not None and gv.itemAt(i).layout().indexOf(tab.btn_preview) >= 0
    )
    idx_preview = row.indexOf(tab.btn_preview)
    idx_pick = next(
        row.indexOf(row.itemAt(i).widget())
        for i in range(row.count())
        if row.itemAt(i).widget() is not None and row.itemAt(i).widget().text() == "选择…"
    )
    check("「预览」在「选择…」左侧", idx_preview < idx_pick, f"index {idx_preview} < {idx_pick}")
    check("未选文件时不可用", not tab.btn_preview.isEnabled(), tab.btn_preview.toolTip())

    print("\n=== 2) 不支持预览的格式（应置灰并说明原因）===")
    for suffix in (".wav", ".pdf"):
        tab.ed_source.setText(str(suite / f"demo{suffix}"))
        app.processEvents()
        check(f"{suffix} 置灰", not tab.btn_preview.isEnabled(), tab.btn_preview.toolTip())

    print("\n=== 3) 四种可预览格式 ===")
    for kind, path in files.items():
        assert path is not None
        tab.ed_source.setText(str(path))
        app.processEvents()
        check(f"{kind} 可预览", tab.btn_preview.isEnabled(), path.name)
        tab.btn_preview.click()
        _pump(app, 12.0 if kind == "midi" else 3.0)  # MIDI 首次要合成，等久一点
        w = tab._audio_preview if kind in ("mp3", "midi") else tab._score_preview
        assert w is not None
        check("非模态", not w.isModal() and w.isVisible(), type(w).__name__)
        if kind in ("mp3", "midi"):
            check("播放/停止可用", w.btn_play.isEnabled() and w.btn_stop.isEnabled(),
                  f"{w.btn_play.text()} / {w.btn_stop.text()} · {w.lbl_total.text()}")
            print(f"       状态：{w.lbl_status.text()[:80]}")
        else:
            check("显示乐谱页（不是文本回退）", w.stack.currentIndex() == 0,
                  f"已载入 {w.score_view.loaded_system_count()} 行")
            print(f"       状态：{w.lbl_status.text()[:80]}")
        shot = out_dir / f"preview-{kind}.png"
        w.grab().save(str(shot))
        print(f"       截图 → {shot}")

    print("\n=== 4) 非模态 + 复用 ===")
    check("两个窗口可同时存在且主窗口仍可用",
          tab._score_preview.isVisible() and tab._audio_preview.isVisible() and win.isEnabled())
    before = (id(tab._score_preview), id(tab._audio_preview))
    tab.ed_source.setText(str(files["svg"]))
    tab.btn_preview.click()
    _pump(app, 0.5)
    check("同类型复用同一窗口", before == (id(tab._score_preview), id(tab._audio_preview)))

    print("\n=== 5) 退出收尾 ===")
    win.close()
    _pump(app, 0.5)
    check("关闭主窗口无异常", True)

    print(f"\n{'全部通过' if not failures else f'{failures} 项失败'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
