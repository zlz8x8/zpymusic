"""人工验收：「格式转换」页的批量链路（M5 / F3.1–F3.7）。

自动化测试（``tests/test_m5_convert.py``，75 项）覆盖逻辑；本脚本用于**目视验收**：
真的把文件加进列表、跑一遍批量、把窗口截图存盘，并打印依赖自检 / 结果区的文案。
想确认"界面看起来对不对、状态与产物显示是否清楚"时用它。

用法::

    $env:QT_QPA_PLATFORM = "offscreen"     # 无显示环境（截图仍可用）
    python tools/check_convert_tab.py [套件名] [输出目录]

Note:
    离屏平台下 Qt 常常拿不到中文字体，界面文字会显示成方框（本机实测整个程序都如此）；
    乐谱本身是矢量路径，不受影响。要看真实字体，在有显示的桌面上不加
    ``QT_QPA_PLATFORM`` 直接跑。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.common.config import AppConfig  # noqa: E402
from zpymusic.core.convert import ConvertOptions, check_environment, plan_output  # noqa: E402
from zpymusic.ui.tab_convert import ConvertTab  # noqa: E402

SUITES = REPO / "staff" / "suites"
MUSICXML = REPO / "staff" / "musicxml"
FAILURES = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  [{'OK ' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILURES += 1


def pump(app, seconds: float) -> None:  # noqa: ANN001
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_batch(app, tab, limit: float = 300.0) -> bool:  # noqa: ANN001
    end = time.time() + limit
    while time.time() < end:
        app.processEvents()
        if tab._runner is None:
            return True
        time.sleep(0.05)
    return False


class _LogDock:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def add_note(self, level: str, text: str) -> None:
        self.notes.append((level, text))


def _new_tab(app, out_dir: Path):  # noqa: ANN001, ANN202
    cfg = AppConfig.load()
    cfg.ui.score_backend = "native"
    tab = ConvertTab(cfg, _LogDock())
    tab.ed_out.setText(str(out_dir))
    tab.cmb_overwrite.setCurrentIndex(1)  # 覆盖
    app.processEvents()
    return tab


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "canon-in-d-easy"
    out_root = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / ".zpy-tmp" / "convert-shots"
    out_dir = out_root / "out"
    if out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    suite = SUITES / base
    mxl = next(
        (p for p in MUSICXML.glob(f"{base}.*") if p.suffix.lower() in {".mxl", ".xml", ".musicxml"}),
        None,
    )
    mid = next(iter(sorted(suite.glob("*.mid"))), None)
    mp3 = next(iter(sorted(suite.glob("*.mp3"))), None)
    svg = next(iter(sorted((suite / "svg").glob("sys-*.svg"))), None)
    if not all((mxl, mid, mp3, svg)):
        print(f"!! 套件 {base} 缺少可用于验收的文件")
        return 2

    app = QApplication(sys.argv)

    print("=== 1) 依赖自检（F3.2）===")
    env = check_environment(AppConfig.load())
    for req in env.values():
        print("  " + req.describe())
    tab = _new_tab(app, out_dir)
    tab.add_source(mxl, select=False)
    tab.cmb_target.setCurrentText("mp3")
    app.processEvents()
    print("  依赖自检面板：", tab.lbl_deps.text().replace("<br>", " | ")[:200])
    print("  目标路径示例：", tab.lbl_out_preview.text()[:160])
    check("mxl → mp3 可以开始", tab.btn_convert.isEnabled())

    print("\n=== 2) 命名模板（F3.7）===")
    tab.ed_template.setText("{base}_{format}")
    app.processEvents()
    check("模板反映到目标路径", "_mp3" in tab.lbl_out_preview.text(), tab.lbl_out_preview.text()[-40:])
    tab.ed_template.setText("{base}")
    app.processEvents()

    print("\n=== 3) 批量：mxl → midi（套件形态）===")
    tab.cmb_target.setCurrentText("midi")
    tab._skipped_pairs.add(("musicxml", "midi"))  # 跳过保真度对话框
    app.processEvents()
    tab._start()
    check("批量已启动（QThread）", tab._runner is not None and tab.btn_cancel.isEnabled())
    if not wait_batch(app, tab):
        check("批量在限时内完成", False, "超时")
    print("  汇总：", tab.lbl_summary.text())
    check("状态=成功", tab.table.item(0, 1).text() == "成功", tab.table.item(0, 3).text()[:60])
    check("产物列是绝对路径", str(out_dir) in tab.table.item(0, 2).text())
    tab.table.selectRow(0)
    app.processEvents()
    check("「打开所在文件夹」可用", tab.btn_reveal.isEnabled())
    check("「载入播放界面」禁用（midi 无音频）", not tab.btn_load_play.isEnabled())
    tab.grab().save(str(out_root / "convert-midi.png"))

    print("\n=== 4) 批量：mxl → wav（可载入播放）===")
    tab = _new_tab(app, out_dir)
    tab.add_source(mxl, select=False)
    tab.cmb_target.setCurrentText("wav")
    tab._skipped_pairs.add(("musicxml", "wav"))
    app.processEvents()
    tab._start()
    wait_batch(app, tab)
    print("  汇总：", tab.lbl_summary.text())
    check("状态=成功", tab.table.item(0, 1).text() == "成功", tab.table.item(0, 3).text()[:60])
    tab.table.selectRow(0)
    app.processEvents()
    check("「载入播放界面」可用", tab.btn_load_play.isEnabled())
    tab.grab().save(str(out_root / "convert-wav.png"))

    print("\n=== 5) 混合格式 → 目标取交集（F3.1）===")
    tab = _new_tab(app, out_dir)
    for p in (mxl, mid, mp3, svg):
        assert p is not None
        tab.add_source(p, select=False)
    tab.cmb_target.setCurrentText("mp3")
    app.processEvents()
    print("  列表：", tab.table.rowCount(), "个文件；目标下拉可选：", sorted(tab._allowed_targets()))
    print("  依赖自检：", tab.lbl_deps.text().replace("<br>", " | ")[:220])
    check("没有共同目标时不做死角（仍有目标可选）", bool(tab._allowed_targets()))
    check("不兼容的文件被标出来并禁止开始", not tab.btn_convert.isEnabled())
    check("给出了分批转换的提示", "分批" in tab.lbl_deps.text())

    print("\n=== 6) 预览按钮仍在（F3.8 回归）===")
    tab.ed_source.setText(str(svg))
    app.processEvents()
    check("SVG 可预览", tab.btn_preview.isEnabled(), tab.btn_preview.toolTip())

    print("\n=== 7) 主窗口：转换产物 → 载入播放界面（F3.6）===")
    from zpymusic.ui.main_window import MainWindow

    win = MainWindow(AppConfig.load())
    win.resize(1280, 880)
    win.show()
    pump(app, 0.5)
    suite_dirs = [p for p in out_dir.glob("*") if p.is_dir() and (p / f"{p.name}.sync.json").is_file()]
    playable = [
        d for d in suite_dirs if any(d.glob("*.wav")) or any(d.glob("*.mp3"))
    ]
    if playable:
        win._on_convert_suite_ready(playable[0])
        pump(app, 1.0)
        current = win.tab_play.current_suite
        check("已切到播放页", win.tabs.currentWidget() is win.tab_play)
        check(
            "播放页已载入转换产物",
            current is not None and Path(current.dir) == playable[0],
            str(playable[0].name),
        )
    else:
        check("找到可播放的转换产物", False, f"{out_dir} 下没有带音频的套件")
    win.close()
    pump(app, 0.3)

    print("\n=== 8) 退出收尾 ===")
    tab.shutdown_previews()
    check("无异常", True)
    print(f"\n{'全部通过' if not FAILURES else f'{FAILURES} 项失败'}；截图目录：{out_root}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
