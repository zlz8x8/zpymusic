"""M4 前置探针：验证 QtWebEngine 能在本机离屏运行并执行 JS。

QtWebEngine 在无 GPU / 受限环境里常见的坑：
  * 需要 ``--disable-gpu --disable-software-rasterizer`` 之类标志
  * 需要 ``QTWEBENGINE_CHROMIUM_FLAGS``、``QTWEBENGINE_DISABLE_SANDBOX=1``
  * ``QApplication`` 必须在导入 QtWebEngine 之前创建，且需 ``QtWebEngineQuick.initialize`` 风格的初始化

同时验证 QMediaPlayer 的后端与可用播放速率。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --disable-dev-shm-usage --no-sandbox",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

HTML = """<!DOCTYPE html><html><body>
<div id="systems"></div>
<script>
window.__ready = false;
window.zpy = {
  loadSystem: function (i, url) {
    var d = document.createElement('div');
    d.id = 'sys-' + i;
    d.innerHTML = '<img src="' + url + '">';
    document.getElementById('systems').appendChild(d);
    return true;
  },
  setActive: function (ids) { window.__last = ids; return ids.length; },
  selftest: function () { return typeof window.zpy.loadSystem; }
};
window.__ready = true;
</script></body></html>"""

results: dict[str, object] = {}


def main() -> int:
    view = QWebEngineView()
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    view.resize(900, 600)

    def on_load(ok: bool) -> None:
        results["load_ok"] = ok
        view.page().runJavaScript("window.__ready", lambda v: step2(v))

    def step2(ready: object) -> None:
        results["ready"] = ready
        view.page().runJavaScript("window.zpy.selftest()", lambda v: step3(v))

    def step3(selftest: object) -> None:
        results["selftest"] = selftest
        view.page().runJavaScript(
            "window.zpy.setActive(['a','b']); JSON.stringify(window.__last)",
            lambda v: step4(v),
        )

    def step4(last: object) -> None:
        results["setActive"] = last
        view.page().runJavaScript(
            "window.zpy.loadSystem(1, 'about:blank'); document.querySelectorAll('#systems > div').length",
            lambda v: finish(v),
        )

    def finish(count: object) -> None:
        results["dom_nodes"] = count
        app.quit()

    view.loadFinished.connect(on_load)
    view.setHtml(HTML, QUrl("file:///"))
    view.show()

    QTimer.singleShot(20000, app.quit)  # 超时保护
    app.exec()

    print("=== QtWebEngine 结果 ===")
    for k, v in results.items():
        print(f"  {k:12s} = {v!r}")
    ok = results.get("ready") is True and results.get("selftest") == "function"
    print("QtWebEngine:", "OK" if ok else "FAILED")

    # --- QtMultimedia ---
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    print("\n=== QtMultimedia 结果 ===")
    p = QMediaPlayer()
    out = QAudioOutput()
    p.setAudioOutput(out)
    print("  playbackRate =", p.playbackRate())
    print("  mediaStatus  =", p.mediaStatus())
    try:
        p.setPlaybackRate(1.5)
        print("  setPlaybackRate(1.5) ->", p.playbackRate())
        p.setPlaybackRate(1.0)
    except Exception as e:  # noqa: BLE001
        print("  setPlaybackRate FAILED:", e)
    print("  error        =", p.error(), p.errorString() or "(none)")

    # 实际加载一个 mp3 试试解码
    from pathlib import Path

    mp3 = Path(__file__).resolve().parents[1] / "staff" / "suites" / "canon-in-d-easy" / "canon-in-d-easy.mp3"
    if mp3.is_file():
        state: dict[str, object] = {}

        def on_status(st) -> None:
            state["status"] = str(st)
            if st == QMediaPlayer.MediaStatus.LoadedMedia:
                state["duration"] = p.duration()
                app.quit()

        p.mediaStatusChanged.connect(on_status)
        p.setSource(QUrl.fromLocalFile(str(mp3)))
        p.play()
        QTimer.singleShot(15000, app.quit)
        app.exec()
        print(f"  load {mp3.name}: status={state.get('status')} duration={state.get('duration')}ms")
    else:
        print("  (未找到 canon mp3，跳过解码测试)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
