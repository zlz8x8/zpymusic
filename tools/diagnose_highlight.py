"""排障：真实播放时高亮跟随为什么不生效？

与冒烟测试的区别：``tools/smoke_play.py`` 用的是 ``controller.preview_at()``
（直接按乐谱时间刷新），而**真实播放**走的是
``play() → QTimer(33ms) → _tick() → _apply_highlight(player.position())`` 这条链。
本脚本走真实链路，逐帧检查：

  * 播放器位置是否在推进
  * 当前小节（高亮对象，M7 起跟随小节）是否在推进
  * tracker 的发声集合是否变化
  * 视图的 overlay 是否真的拿到矩形
  * 视图是否发生滚动（跟随）

Note:
    "active 有变化"看的是**发声元素集合**而不是元素个数 —— 早期版本比较的是
    个数，于是"一直有 2 个音在响"会被误判成"没有变化"（实测踩过）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.common.config import AppConfig  # noqa: E402
from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "waltz-in-a-minorchopin"
    app = QApplication(sys.argv)
    win = MainWindow(AppConfig())
    win.show()
    app.processEvents()

    play = win.tab_play
    ctrl = play.controller
    suites = discover_suites(REPO / "staff" / "suites")
    suite = next((s for s in suites if s.base == target), None)
    if suite is None:
        print(f"!! 找不到套件 {target}")
        return 2
    print(f"套件 = {suite.base}  后端 = {type(play.score_view).__name__}")
    play.ed_root.setText(str(REPO / "staff" / "suites"))
    play.reload()
    play._select_suite(suite)
    app.processEvents()

    print(f"时间轴事件 = {len(ctrl.timeline.entries) if ctrl.timeline else 0}")
    print(f"音频 = {play.player.info.path}")
    # 等音频时长就绪
    deadline = time.time() + 8
    while play.player.duration() == 0 and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    print(f"音频时长 = {play.player.duration()} ms")
    print(f"跟随开关 = {ctrl.view.follow}")

    native = play.score_view
    has_view = hasattr(native, "view")
    vbar = native.view.verticalScrollBar() if has_view else None
    print(f"滚动条范围 = {vbar.minimum()}..{vbar.maximum()}" if vbar else "  无滚动条")

    samples: list[dict] = []

    def probe() -> None:
        pos = play.player.position()
        score = ctrl.score_time()
        active = tuple(ctrl.tracker.active) if ctrl.tracker else ()
        rects = len(native._overlay.rects) if has_view else -1
        scroll = vbar.value() if vbar else -1
        mounted = native.loaded_system_count() if has_view else -1
        samples.append(
            {
                "pos": pos,
                "active": active,
                "measure": ctrl._plan.measure_id,
                "rects": rects,
                "scroll": scroll,
            }
        )
        print(
            f"  player={pos:6d}ms score={score:7.0f}ms "
            f"measure={(ctrl._plan.measure_id or '-'):10s} active={len(active)} "
            f"overlay={rects} scroll={scroll} mounted={mounted} {list(active)[:2]}"
        )

    print("\n=== 启动真实播放 ===")
    # 先跳到曲子中段，这样当前 system 会跨行，才能真正检验"跟随滚动"
    mid = int(ctrl.duration_ms * 0.4)
    ctrl.seek_score(mid)
    app.processEvents()
    print(f"  已 seek 到 {mid} ms，当前 system 归属检查：")
    ctrl.play()
    for _ in range(16):
        QTimer.singleShot(0, probe)
        app.processEvents()
        time.sleep(0.35)
    ctrl.pause()

    moved = len({s["pos"] for s in samples}) > 1
    active_changed = len({s["active"] for s in samples}) > 1
    measure_changed = len({s["measure"] for s in samples}) > 1
    nonzero = any(s["rects"] > 0 for s in samples)
    scrolled = len({s["scroll"] for s in samples}) > 1
    measures = sorted({s["measure"] for s in samples if s["measure"]})

    print("\n=== 结论 ===")
    print(f"  播放器位置在推进        : {moved}")
    print(f"  发声集合有变化          : {active_changed}")
    print(f"  高亮小节有变化（M7）    : {measure_changed}  走过的小节 = {measures[:6]}")
    print(f"  overlay 拿到矩形        : {nonzero}")
    print(f"  视图发生滚动（跟随）    : {scrolled}")
    win.close()
    return 0 if (moved and nonzero and (active_changed or measure_changed)) else 1


if __name__ == "__main__":
    sys.exit(main())
