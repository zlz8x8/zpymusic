"""WebEngine 曲谱视图（需求 §5.3 / D5）—— 生产环境的首选后端。

页面与 JS 见 :data:`zpymusic.sync.local_server.PAGE_HTML`，通过本地只读 HTTP 服务
提供 SVG 与音频。高亮就是给 ``<g class="note">`` 加一个 class，颜色/透明度用 CSS 变量控制，
因此渐晕、缩放、滚动、命中测试全部由浏览器负责，性能最好。

**GPU 策略（M6 起按实测重写）**

本模块**默认不再关闭 GPU**。历史版本在启动时无条件注入
``--disable-gpu`` + ``QT_OPENGL=software`` + ``QT_QUICK_BACKEND=software``，
并把随后出现的这两条报错归因于"机器没有 GPU / 驱动异常 / 远程桌面"：

.. code-block:: text

   ERROR:gpu_channel_manager.cc(956) Failed to create GLES3 context, fallback to GLES2.
   ERROR:gpu_channel_manager.cc(967) ContextResult::kFatalFailure:
                                     Failed to create shared context for virtualization.

对照实验（同一台机器、同一解释器、PySide6 6.9.3 / Qt 6.9.3）推翻了那个归因：

* 基线（不设任何 GL / WebEngine 变量，等同 pyqt6-tutorial 的 demo）→ **无报错**；
* 旧默认（``prepare_webengine_env()`` 原样）→ **有报错**；
* 只加 ``QT_QUICK_BACKEND=software`` → **有报错**；
* 只加 ``QT_OPENGL=software`` → 无报错；
* 只加 ``--disable-gpu`` → **有报错**；
* 只加其余 flag（``--disable-gpu-compositing`` / ``-rasterization`` /
  ``-accelerated-2d-canvas`` / ``-accelerated-video-decode`` /
  ``--disable-dev-shm-usage`` / ``--no-sandbox`` / ``QTWEBENGINE_DISABLE_SANDBOX=1``）
  → 全部无报错；
* 真实曲谱页在干净环境下 → 无报错，且 6/6 张 ``sys-*.svg`` 渲染成功。

结论：**这两条报错是"我们自己去关 GPU"造成的，而且是非致命的**（所有实验里页面都正常加载）；
本机 Qt 能建出 OpenGL 4.6 上下文，pyqt6-tutorial 的 ``QWebEngineView`` 也从不注入这些标志，
所以它们从不报错。删掉 ``--disable-gpu`` 之后报错消失、并且重新拿到了 GPU 合成。

只有在**默认配置探测失败且原因是"建不出 GL 上下文"**时，才降级到
:data:`_SOFTWARE_FLAGS`（软件渲染兜底），见 :func:`is_webengine_available`。

**运行环境限制**：Chromium 需要命名管道做子进程 IPC。在受限沙箱 / 受管环境里
（例如由自动化代理会话启动、禁止创建命名管道）会 **致命 abort**：
``FATAL:named-platform-channel-pipe ... 拒绝访问 (0x5)``。这与 GPU 无关，
普通桌面环境（含本机）不受影响，:func:`zpymusic.sync.score_view.create_score_view`
会自动回退到原生后端。

**安全**：不再注入 ``--no-sandbox`` / ``QTWEBENGINE_DISABLE_SANDBOX``，
保留 Chromium 默认沙箱。页面只来自 ``127.0.0.1`` 上的只读本地服务
（见 :mod:`zpymusic.sync.local_server`）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from ..common.log import get_logger
from .highlight import HighlightPlan, build_css
from .local_server import LocalAssetServer

log = get_logger(__name__)

__all__ = [
    "WebScoreView",
    "PROFILE_DEFAULT",
    "PROFILE_SOFTWARE",
    "active_profile",
    "clamp_zoom",
    "classify_probe_failure",
    "content_width_of",
    "fit_zoom_for",
    "is_webengine_available",
    "last_probe_reason",
    "prepare_webengine_env",
    "webengine_environment_hint",
]

#: 默认 profile：**不关闭 GPU**。
#: 加上 ``--disable-gpu`` 会稳定触发 ``gpu_channel_manager.cc`` 那两条报错（见模块文档），
#: 所以这里只保留对上下文/渲染无害的 ``--disable-dev-shm-usage``
#: （它本是为 Linux 容器里 /dev/shm 过小准备的，Windows 上是空操作）。
_DEFAULT_FLAGS = "--disable-dev-shm-usage"

#: 兜底 profile：真正"没有可用 GPU"时才用（关闭 GPU 合成 / 光栅化，退回软件渲染）。
#:
#: 注意：**不要**加 ``--disable-software-rasterizer``！它连软件路径一起禁掉，
#: 会让"没有 GPU"的机器彻底建不出上下文。历史上踩过这个坑。
#: 也**不要**加 ``--no-sandbox``：实测本机不需要它，而需要它的受限沙箱里
#: Chromium 早就死在命名管道上了，加它只降低安全性。
_SOFTWARE_FLAGS = " ".join(
    (
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-gpu-rasterization",
        "--disable-accelerated-2d-canvas",
        "--disable-accelerated-video-decode",
        "--disable-dev-shm-usage",
    )
)

#: 与 :data:`_SOFTWARE_FLAGS` 配套的 Qt 侧变量。
#:
#: ⚠ **绝不能无条件设置**：``QT_QUICK_BACKEND=software`` 会让 QtWebEngine 的合成器
#: 拿不到可共享的 GL 上下文，**同样**打印那两条 ``gpu_channel_manager`` 报错（实测）。
#: 它只作为兜底 profile 的一部分、在探测确认需要时才生效。
#: （``QT_OPENGL=software`` 单独设置是无害的，但也同样只在兜底里出现。）
_SOFTWARE_QT_ENV = {"QT_OPENGL": "software", "QT_QUICK_BACKEND": "software"}

PROFILE_DEFAULT = "default"
"""默认（GPU）配置。"""

PROFILE_SOFTWARE = "software"
"""软件渲染兜底配置。"""

#: 当前生效的 profile，由 :func:`is_webengine_available` 探测后确定。
_ACTIVE_PROFILE = PROFILE_DEFAULT

#: 环境变量：显式覆盖探测结果（``1`` 强制认为可用，``0`` 强制不可用）
AVAIL_ENV = "ZPYMUSIC_WEBENGINE"

#: ``_probe`` 的失败分类：命名管道被拒 / 建不出 GL 上下文 / 超时 / 其它
REASON_PIPE = "pipe"
REASON_GL = "gl"
REASON_TIMEOUT = "timeout"
REASON_OTHER = "other"

_PIPE_MARKERS = (
    "named-platform-channel-pipe",
    "拒绝访问",
    "Access is denied",
    "0x5)",
)
_GL_MARKERS = (
    "gpu_channel_manager",
    "Failed to create GLES",
    "Failed to create shared context",
    "kFatalFailure",
)


def active_profile() -> str:
    """当前生效的 profile（``default`` / ``software``）。"""
    return _ACTIVE_PROFILE


def clamp_zoom(factor: float) -> float:
    """把缩放系数夹到**与原生后端相同的区间**（30%–400%）。

    共用 ``score_view`` 里的常量，避免两个后端各自维护一份上下限而悄悄分叉。
    延迟导入是为了不让 ``web_view`` 在导入期就拖进整套 QtWidgets。
    """
    from .score_view import MAX_ZOOM, MIN_ZOOM  # noqa: PLC0415

    return max(MIN_ZOOM, min(MAX_ZOOM, float(factor)))


def content_width_of(systems) -> float:  # noqa: ANN001
    """一批 :class:`~zpymusic.sync.score_view.SystemRef` 里最宽的 CSS 宽度。

    就是 Verovio 的 ``page_width``（默认 2100），与原生后端用
    ``sceneRect().width()`` 等价。抽成纯函数是为了能在**不启动 Chromium** 的前提下
    单测"适应宽度"的输入（CI 里没有可用的 WebEngine）。
    """
    return max((float(getattr(s, "width", 0) or 0) for s in systems), default=0.0)


def fit_zoom_for(content_width: float, avail_width: float) -> float:
    """铺满 ``avail_width`` 所需的缩放系数（已夹到合法区间）。"""
    if content_width <= 0 or avail_width <= 0:
        return 1.0
    return clamp_zoom(avail_width / content_width)


def _profile_env(profile: str) -> tuple[str, dict[str, str]]:
    """profile → ``(QTWEBENGINE_CHROMIUM_FLAGS, 额外的 Qt 环境变量)``。"""
    if profile == PROFILE_SOFTWARE:
        return _SOFTWARE_FLAGS, dict(_SOFTWARE_QT_ENV)
    return _DEFAULT_FLAGS, {}

_PROBE_SOURCE = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", {flags!r})
for _k, _v in {qt_env!r}.items():
    os.environ.setdefault(_k, _v)
from PySide6.QtWidgets import QApplication
app = QApplication([])
from PySide6.QtWebEngineWidgets import QWebEngineView
v = QWebEngineView()
v.setHtml("<html><body>ok</body></html>")
ok = {{"v": False}}
def done(loaded):
    ok["v"] = bool(loaded)
    app.quit()
v.loadFinished.connect(done)
from PySide6.QtCore import QTimer
QTimer.singleShot(12000, app.quit)
app.exec()
sys.exit(0 if ok["v"] else 3)
'''


def webengine_environment_hint(reason: str = "") -> str:
    """给用户看的"为什么 WebEngine 不可用"提示（按探测到的原因分类）。"""
    if reason == REASON_PIPE:
        return (
            "Chromium 无法创建命名管道 IPC（named-platform-channel-pipe ... 拒绝访问）。"
            "这是**运行环境限制**（受限沙箱 / 受管环境，或由自动化代理会话启动）造成的，"
            "与显卡无关；程序会自动改用 QtSvg 原生渲染后端。"
            "在普通桌面（自己的终端 / IDE 运行）下浏览器引擎是可用的。"
        )
    if reason == REASON_GL:
        return (
            "Chromium 在默认配置与软件渲染配置下都建不出 GL 上下文。"
            "这才是真正的“没有可用 GPU / 驱动异常”场景；"
            "程序会自动改用 QtSvg 原生渲染后端。"
        )
    if reason == REASON_TIMEOUT:
        return "WebEngine 子进程探测超时；程序自动改用 QtSvg 原生渲染后端。"
    return (
        "QtWebEngine 在这台机器上无法完成一次最小页面加载；"
        "程序会自动改用 QtSvg 原生渲染后端（功能等价，只是高亮用覆盖框实现）。"
    )


def prepare_webengine_env(profile: str | None = None) -> None:
    """在创建 :class:`~PySide6.QtWebEngineWidgets.QWebEngineView` **之前**设置环境变量。

    与旧版本的区别（重要）：

    * **默认不再关闭 GPU**（不注入 ``--disable-gpu``），也不再把软件渲染强加给主进程；
    * **不再设置** ``--no-sandbox`` / ``QTWEBENGINE_DISABLE_SANDBOX``，保留 Chromium 沙箱；
    * 只有 :func:`is_webengine_available` 探测出"必须软件渲染"时，
      ``profile`` 才会是 :data:`PROFILE_SOFTWARE`，此时才注入
      ``QT_OPENGL`` / ``QT_QUICK_BACKEND``。

    用户若已自行设置这些变量，则**尊重用户设置**（只在缺失时补默认值）。
    """
    prof = profile or _ACTIVE_PROFILE
    flags, qt_env = _profile_env(prof)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", flags)
    for key, value in qt_env.items():
        os.environ.setdefault(key, value)


_AVAILABLE_CACHE: bool | None = None
_LAST_REASON = ""


def classify_probe_failure(text: str) -> str:
    """把探测子进程的输出归类成 :data:`REASON_PIPE` / :data:`REASON_GL` / 其它。

    分类的意义：这两种失败**处置方式完全不同** ——
    命名管道被拒是运行环境限制（换环境即可用），
    建不出 GL 上下文才是显卡侧问题（可用软件渲染兜底）。旧版本把它们都说成"沙箱"，
    会把排障方向带偏。
    """
    low = (text or "").lower()
    if any(m.lower() in low for m in _PIPE_MARKERS):
        return REASON_PIPE
    if any(m.lower() in low for m in _GL_MARKERS):
        return REASON_GL
    return REASON_OTHER


def _run_probe(profile: str, timeout_s: float) -> tuple[bool, str, str]:
    """在子进程里用指定 profile 跑一次最小页面加载。

    Returns:
        ``(是否成功, 失败分类, 失败详情)``；成功时后两项为空串。
    """
    flags, qt_env = _profile_env(profile)
    src = _PROBE_SOURCE.format(flags=flags, qt_env=qt_env)
    # 注意：**不能**用 tempfile.TemporaryDirectory() —— 它落在系统 TEMP，
    # 在受限沙箱里会 PermissionError（实测 WinError 5）。改用项目内的临时目录。
    tmp_root = _probe_temp_root()
    work = tmp_root / f"probe-{profile}-{os.getpid()}"
    try:
        work.mkdir(parents=True, exist_ok=True)
        script = work / "probe.py"
        script.write_text(src, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_s,
                cwd=str(tmp_root),
            )
            if proc.returncode == 0:
                return True, "", ""
            text = (proc.stderr or "") + (proc.stdout or "")
            lines = text.strip().splitlines()
            detail = lines[-1] if lines else f"returncode={proc.returncode}"
            return False, classify_probe_failure(text), detail
        except subprocess.TimeoutExpired:
            return False, REASON_TIMEOUT, f"超过 {timeout_s:.0f}s"
        except OSError as e:
            return False, REASON_OTHER, f"无法启动探测子进程：{e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def is_webengine_available(*, timeout_s: float = 25.0, force: bool | None = None) -> bool:
    """**在子进程里**探测 QtWebEngine 是否真的可用，并决定用哪个 profile。

    为什么必须用子进程：QtWebEngine 初始化失败是 **致命错误**（Chromium 直接 abort），
    没有 Python 异常可以捕获 —— 实测会打印
    ``FATAL:named-platform-channel-pipe(89) Check failed: ... 拒绝访问`` 并终止进程。
    因此只能在独立子进程里试，把结果缓存下来，主进程再决定用哪个后端。

    两段式（M6 起）：

    1. **先用默认配置（开着 GPU）探测** —— 这才是用户真实运行时的配置；
    2. 只有第 1 步失败、且失败原因是"建不出 GL 上下文"（:data:`REASON_GL`）时，
       才用软件渲染配置（:data:`PROFILE_SOFTWARE`）重试。

    旧版本把软件渲染当成默认、并且只探测一次，于是"探测通过"从来不代表
    "默认环境能用"，还顺手把 GPU 关掉了。
    """
    global _AVAILABLE_CACHE, _ACTIVE_PROFILE, _LAST_REASON
    if force is not None:
        return force
    env_flag = os.environ.get(AVAIL_ENV, "").strip()
    if env_flag in ("0", "false", "no"):
        log.info("环境变量 %s 指定禁用 WebEngine 后端", AVAIL_ENV)
        _LAST_REASON = "disabled"
        return False
    if env_flag in ("1", "true", "yes"):
        log.debug("环境变量 %s 指定强制使用 WebEngine 后端（默认 profile）", AVAIL_ENV)
        return True
    if _AVAILABLE_CACHE is not None:
        return _AVAILABLE_CACHE

    ok, reason, detail = _run_probe(PROFILE_DEFAULT, timeout_s)
    if ok:
        _ACTIVE_PROFILE = PROFILE_DEFAULT
        log.debug("WebEngine 探测通过（默认/GPU 配置）")
    else:
        log.info("WebEngine 探测失败（默认配置，%s）：%s", reason, detail[:160])
        if reason == REASON_GL:
            log.info("默认配置建不出 GL 上下文，改用软件渲染配置重试…")
            ok2, reason2, detail2 = _run_probe(PROFILE_SOFTWARE, timeout_s)
            if ok2:
                ok = True
                _ACTIVE_PROFILE = PROFILE_SOFTWARE
                log.info("软件渲染配置下 WebEngine 可用（已降级，谱面滚动/重绘会变慢）")
            else:
                reason, detail = reason2, detail2
                log.info("软件渲染配置同样失败（%s）：%s", reason, detail[:160])

    _AVAILABLE_CACHE = ok
    _LAST_REASON = "" if ok else reason
    if not ok:
        log.info("%s", webengine_environment_hint(reason))
    return ok


def last_probe_reason() -> str:
    """最近一次探测失败的原因分类（成功或未探测时为空串）。"""
    return _LAST_REASON


def _probe_temp_root() -> Path:
    """探测用的临时目录根（优先项目内，回退系统 TEMP）。"""
    from ..common.paths import PROJECT_ROOT  # noqa: PLC0415

    for cand in (PROJECT_ROOT / ".zpy-tmp", Path(tempfile.gettempdir())):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / f".wtest-{os.getpid()}"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return cand
        except OSError:
            continue
    return Path.cwd()


class WebScoreView(QObject):
    """基于 ``QWebEngineView`` 的曲谱视图（与 :class:`ScoreView` 同接口）。

    注意：这里**不继承** ``ScoreView``（那会要求 QtWebEngine 在导入期就可用），
    而是继承 ``QObject`` 以获得信号能力，并由 ``create_score_view`` 按鸭子类型使用。

    ⚠ **接口一致性**：因为不继承 ``ScoreView``，少写一个方法不会报错 ——
    ``ui/score_host.py`` 用 ``hasattr`` 探测能力，缺方法就**静默降级**。
    真实事故：本类曾经没有 ``set_fit_width`` / ``zoom_changed``，
    于是缩放工具条上的「适应宽度」按钮虽然是勾选状态，但什么也不做，
    谱面永远停在 100%（M6 默认后端改为 ``auto`` 后立刻暴露）。
    现在这里的缩放/适应宽度接口与原生后端一一对应，
    并由 ``tests/test_webengine_env.py`` 的接口一致性用例守住。
    """

    element_clicked = Signal(str)
    #: 实际缩放系数变化（缩放工具条的百分比显示依赖它；缺了数字会停在旧值上）
    zoom_changed = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: PLC0415

        prepare_webengine_env()

        self.backend_ready = False
        self.unavailable_reason = ""
        self._systems: list = []
        self._root: Path | None = None
        self._system_of: dict[str, int] = {}
        self._measure_of: dict[str, list[int]] = {}
        self._system_map_cache: dict[str, int] | None = None
        self._follow = True
        self._color = "#FF8C00"
        self._opacity = 0.35
        self._page_loaded = False
        self._pending: list[str] = []
        self._pending_base = ""
        # 缩放 / 适应宽度（与 NativeScoreView 同名同义）
        self._fit_width = True
        self._fit_view_size: tuple[int, int] | None = None
        self._zoom = 1.0

        self._widget = QWidget()
        # 后端不是 QWidget，收不到 resizeEvent —— 用事件过滤器在宿主尺寸变化时重算适应宽度
        self._widget.installEventFilter(self)
        self._layout = QVBoxLayout(self._widget)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._server = LocalAssetServer(Path("."))

        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: PLC0415
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: PLC0415
        except ImportError as e:
            self.unavailable_reason = f"无法导入 QtWebEngine（{e}）"
            log.warning("%s", self.unavailable_reason)
            return

        try:
            self._view = QWebEngineView(self._widget)
        except Exception as e:  # noqa: BLE001 - 沙箱/权限问题都在这里暴露
            self.unavailable_reason = f"QWebEngineView 创建失败：{e}"
            log.warning("%s", self.unavailable_reason)
            return

        s = self._view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        from PySide6.QtCore import Qt  # noqa: PLC0415

        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._layout.addWidget(self._view)
        self._view.loadFinished.connect(self._on_load_finished)

        self.backend_ready = True
        log.debug("WebEngine 后端就绪")
        # 把实际生效的 profile 与标志记进日志：出 GPU / 上下文问题时便于自查。
        # （旧版本只打印 QT_OPENGL，恰好漏掉了真正会造成 GPU 报错的
        #   QT_QUICK_BACKEND，导致用户按日志自查时看不到关键变量。）
        log.info(
            "WebEngine 环境：profile=%s | QTWEBENGINE_CHROMIUM_FLAGS=%s | "
            "QT_OPENGL=%s | QT_QUICK_BACKEND=%s",
            active_profile(),
            os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "(未设置)"),
            os.environ.get("QT_OPENGL", "(未设置)"),
            os.environ.get("QT_QUICK_BACKEND", "(未设置)"),
        )

    # ------------------------------------------------------------------ 属性
    @property
    def widget(self):  # noqa: ANN201
        """实际的 QWidget（供布局使用）。"""
        return self._widget

    @property
    def systems(self) -> list:
        return self._systems

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def follow(self) -> bool:
        return self._follow

    # ------------------------------------------------------------------ 载入
    def load(self, root: Path, systems: list) -> None:
        self._root = Path(root)
        self._systems = list(systems)
        self._system_map_cache = None
        self._system_of.clear()
        self._measure_of.clear()
        for s in self._systems:
            for eid in s.note_ids:
                self._system_of.setdefault(eid, s.index)
            for mid in getattr(s, "measure_ids", None) or ():
                # -rendN 变体一起登记（反复记号会把同一小节展开成两个 ID，
                # 而 SVG 里只画其中一个）—— 与原生后端同样处理
                for key in {mid, re.sub(r"-rend\d*$", "", mid)}:
                    if not key:
                        continue
                    systems = self._measure_of.setdefault(key, [])
                    if s.index not in systems:
                        systems.append(s.index)

        # 服务只启动一次：切换套件只换根目录，**不重启、不换端口**。
        # 见 LocalAssetServer 的说明 —— 换端口会让长期存活的页面在运行中改 origin。
        self._server.set_root(self._root)
        self._pending_base = self._server.start()
        # 每次都重新加载页面：给新套件一个干净的 DOM 状态。
        # 页面只含 JS、没有外部资源，重载成本极低，却能彻底避免
        # "旧页面的 blocks / 观察器 / base 残留"导致后续套件加载失败。
        self._page_loaded = False
        self._pending.clear()
        self._view.load(QUrl(self._pending_base + "/"))
        # 首屏也要"适应宽度"：此时 system 宽度已知（来自 sync.json），
        # 不必等页面加载完；若宿主还没布局（宽 0），_maybe_fit_width 会自行跳过，
        # 之后由 Resize / Show 事件与 _on_load_finished 补上。
        self._maybe_fit_width(force=True)

    def fetch_page_errors(self, callback) -> None:  # noqa: ANN001
        """异步读取页面里"加载失败的行"及其原因（排障用）。"""
        if not self.backend_ready or not self._page_loaded:
            callback([])
            return
        self._view.page().runJavaScript(
            "window.zpyErrors ? JSON.stringify(window.zpyErrors()) : '[]'", callback
        )

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self.unavailable_reason = "页面加载失败（本地服务或 Chromium 初始化异常）"
            log.warning("%s", self.unavailable_reason)
            return
        self._page_loaded = True
        self._push_all()
        # 新页面加载完成后重新套用一次缩放（"适应宽度"必须在这里也生效一次：
        # load() 时页面还是空的，setZoomFactor 虽然会保留，但强制重算最稳妥）
        self._maybe_fit_width(force=True)
        # 延迟查一次失败行，把真实原因写进日志（用户报告问题时有用）
        QTimer.singleShot(4000, self._report_page_errors)

    def _report_page_errors(self) -> None:
        def done(res: object) -> None:
            import json  # noqa: PLC0415

            try:
                items = json.loads(str(res)) if res else []
            except (ValueError, TypeError):
                return
            if not items:
                return
            log.warning("曲谱页面有 %d 行加载失败：", len(items))
            for it in items[:5]:
                log.warning("  行 %s  原因=%s  URL=%s", it.get("index"), it.get("err"), it.get("url"))

        self.fetch_page_errors(done)

    def _push_all(self) -> None:
        base = getattr(self, "_pending_base", "")
        self._run_js(f"window.zpy.setBase({_js(base)});")
        self.set_highlight_style(self._color, self._opacity)
        payload = [
            {
                "index": s.index,
                "file": s.file,
                "width": s.width,
                "height": s.height,
            }
            for s in self._systems
        ]
        import json  # noqa: PLC0415

        self._run_js(f"window.zpy.setSystems({json.dumps(payload, ensure_ascii=False)});")
        for js in self._pending:
            self._run_js(js)
        self._pending.clear()

    # ------------------------------------------------------------------ 高亮
    def set_highlight(self, plan: HighlightPlan) -> None:
        if plan.measure_id:
            self._set_measure_highlight(plan)
            return
        ids = [r.element_id for r in plan.rects if not r.element_id.startswith("+")]
        if not ids:
            # plan 可能是合并后的（element_id 形如 "a+b"），拆开还原
            ids = []
            for r in plan.rects:
                ids.extend(x for x in r.element_id.split("+") if x)
        import json  # noqa: PLC0415

        # 从"小节高亮"退回"逐音符高亮"时要清掉小节覆盖框
        js = "window.zpy.clearMeasure();"
        js += f"window.zpy.setActive({json.dumps(ids, ensure_ascii=False)});"
        if self._follow and plan.rects:
            first = None
            for r in plan.rects:
                first = r.element_id.split("+")[0]
                break
            if first:
                # followTo 只在音符离开舒适区时才滚动（与原生后端一致），
                # 不再每帧强行居中 —— 否则用户滚不动、也回不到顶端。
                js += f"window.zpy.followTo({_js(first)});"
        self._run_js(js)

    def _set_measure_highlight(self, plan: HighlightPlan) -> None:
        """小节高亮：只把小节 ID 交给页面，矩形由 DOM 的 ``getBBox()`` 算。

        SVG 的坐标换算（viewBox / 缩放 / transform）全部由浏览器负责，
        Python 侧不需要解析 SVG —— 与原生后端"用解析出的几何画覆盖框"等价。
        页面里找不到该小节时，JS 会自动退回逐音符高亮（见 ``zpy.setMeasure``）。
        """
        import json  # noqa: PLC0415

        ids = json.dumps(list(plan.ids), ensure_ascii=False)
        follow = json.dumps(bool(self._follow and plan.rects))
        self._run_js(f"window.zpy.setMeasure({_js(plan.measure_id)}, {ids}, {follow});")

    def measure_systems(self, measure_id: str) -> list[int]:
        """包含该小节的 system 序号（来自 ``systems[].measure_ids``，无需解析 SVG）。"""
        return list(self._measure_of.get(measure_id, ()))

    def clear_highlight(self) -> None:
        self._run_js("window.zpy.clearHighlight();")

    def set_highlight_style(self, color: str, opacity: float) -> None:
        self._color = color
        self._opacity = float(opacity)
        self._run_js(f"window.zpy.setHighlightStyle({_js(color)}, {self._opacity});")
        # 同时注入一份 <style>，便于用户查看/覆盖
        css = build_css(color, opacity).replace("\n", " ")
        self._run_js(
            "(function(){var s=document.getElementById('zpy-style');"
            "if(!s){s=document.createElement('style');s.id='zpy-style';"
            "document.head.appendChild(s);} s.textContent=" + _js(css) + ";})();"
        )

    # ------------------------------------------------------------------ 导航
    def scroll_to_element(self, element_id: str, *, center: bool = True) -> bool:
        if element_id not in self._system_of:
            if re.sub(r"-rend\d*$", "", element_id) not in self._system_of:
                return False
        self._run_js(f"window.zpy.scrollToId({_js(element_id)}, {str(bool(center)).lower()});")
        return True

    def scroll_to_system(self, system: int, *, center: bool = True) -> None:
        self._run_js(f"window.zpy.scrollToSystem({int(system)}, {str(bool(center)).lower()});")

    def set_follow(self, follow: bool) -> None:
        self._follow = bool(follow)

    # ------------------------------------------------------------------ 缩放
    def set_zoom(self, factor: float) -> float:
        """设置缩放系数（``1.0`` = 原始大小）。返回实际生效的系数。"""
        f = clamp_zoom(factor)
        self._zoom = f
        if self.backend_ready:
            self._view.setZoomFactor(f)
            self._zoom = float(self._view.zoomFactor())
        self.zoom_changed.emit(self._zoom)
        return self._zoom

    def zoom(self) -> float:
        """当前缩放系数（缩放工具条的百分比显示要用；缺失会让 _apply_zoom 抛错）。"""
        if not self.backend_ready:
            return self._zoom
        return float(self._view.zoomFactor())

    def fit_width(self) -> float:
        """把谱面缩放到**刚好铺满视口宽度**，返回实际生效的系数。

        ``QWebEngineView.setZoomFactor()`` 是"页面缩放"：它按比例放大 CSS 像素，
        所以 2100 CSS px 宽的 system 在 0.4 倍下只占 840 物理像素 ——
        与原生后端用 ``QGraphicsView.scale()`` 的效果一致，横向滚动条与两侧留白随之消失。
        """
        content_w = self._content_width()
        if content_w <= 0:
            return self.zoom()
        avail = max(100, self._widget.width() - self.overlay_right_inset() - 16)
        # 先记账再缩放：Resize 事件重入时就不会再算一次（与原生后端同一手法）
        self._fit_view_size = (self._widget.width(), self._widget.height())
        return self.set_zoom(fit_zoom_for(content_w, avail))

    def _maybe_fit_width(self, *, force: bool = False) -> None:
        """尺寸真的变了才重算（避免每次 Resize 都抖动，与原生后端同策略）。"""
        if not self._fit_width or not self._systems:
            return
        size = (self._widget.width(), self._widget.height())
        if size[0] <= 0:
            return
        if not force and size == self._fit_view_size:
            return
        self.fit_width()

    def set_fit_width(self, on: bool) -> None:
        """开启/关闭「适应宽度」。开启后窗口尺寸变化会自动重算。"""
        self._fit_width = bool(on)
        if self._fit_width:
            self.fit_width()

    @property
    def fit_width_enabled(self) -> bool:
        return self._fit_width

    def _content_width(self) -> float:
        """页面内容的 CSS 宽度（= 最宽的一个 system），见 :func:`content_width_of`。"""
        return content_width_of(self._systems)

    def overlay_right_inset(self) -> int:
        """让浮层避开页面自身的纵向滚动条（Chromium 在 Windows 上约 16 px）。

        浏览器内部的滚动条宽度无法直接查询，这里取一个保守的估计值：
        宁可多留几像素，也不要压住滚动条。
        """
        return 16 if self.backend_ready else 0

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        """宿主尺寸变化 / 首次显示时重算"适应宽度"。

        本类不是 ``QWidget``（拿不到 ``resizeEvent``），所以用事件过滤器。
        """
        from PySide6.QtCore import QEvent  # noqa: PLC0415

        if obj is self._widget and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._maybe_fit_width()
        return super().eventFilter(obj, event)

    def loaded_system_count(self) -> int:
        return -1  # 由页面内部维护；如需精确值可异步查询

    def build_system_map(self, plan_system_of: dict[str, int]) -> None:
        if self._system_map_cache is None:
            mapping: dict[str, int] = {}
            for s in self._systems:
                for eid in s.note_ids:
                    mapping.setdefault(eid, s.index)
            self._system_map_cache = mapping
        plan_system_of.clear()
        plan_system_of.update(self._system_map_cache)

    # ------------------------------------------------------------------ 内部
    def _run_js(self, script: str) -> None:
        if not self.backend_ready:
            return
        if not self._page_loaded:
            self._pending.append(script)
            return
        self._view.page().runJavaScript(script)

    def shutdown(self) -> None:
        """停止本地资源服务（程序退出/切换后端时调用）。"""
        self._server.stop()

    def destroy(self) -> None:
        """彻底清理（会话内不再使用时调用）。"""
        self.shutdown()
        self.backend_ready = False
        self._widget.deleteLater()


def _js(value: str) -> str:
    """把 Python 字符串安全地嵌进 JS 源码。"""
    import json  # noqa: PLC0415

    return json.dumps(value, ensure_ascii=False)
