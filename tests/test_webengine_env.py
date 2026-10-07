"""WebEngine / Qt 环境策略的回归测试（M6）。

这一组测试盯的是**一个被实测推翻的旧结论**：旧版本在启动时无条件注入
``--disable-gpu`` / ``QT_OPENGL=software`` / ``QT_QUICK_BACKEND=software``，
并把随后出现的

.. code-block:: text

   ERROR:gpu_channel_manager.cc(956) Failed to create GLES3 context, fallback to GLES2.
   ERROR:gpu_channel_manager.cc(967) ContextResult::kFatalFailure:
                                     Failed to create shared context for virtualization.

归因于"机器没有独显 / 驱动异常 / 远程桌面"。对照实验（同一台机器、同一解释器）证明：

* 基线（什么都不设，等同 pyqt6-tutorial 的 ``QWebEngineView`` demo）→ **无报错**；
* 只加 ``--disable-gpu`` → **有报错**；
* 只加 ``QT_QUICK_BACKEND=software`` → **有报错**；
* 只加 ``QT_OPENGL=software`` → 无报错；
* 干净环境加载真实曲谱页 → 无报错且 6/6 张 SVG 正常渲染。

因此这些测试要求：**默认 profile 绝不包含关闭 GPU 或关闭沙箱的标志**，
软件渲染只在"默认配置因 GL 上下文失败"时才作为兜底出现。

这些用例全部是纯环境变量 / 字符串级别的，**不会真的启动 Chromium**
（在受限沙箱里启动它会让进程直接 abort）。
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

import pytest

from zpymusic.common.config import UiConfig
from zpymusic.sync import web_view


def _called_names(func: Any) -> set[str]:
    """函数体里被调用的函数/方法名（**按 AST 取**，不受注释与文档字符串影响）。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _function_body(func: Any) -> list[ast.stmt]:
    """函数的语句列表（跳过 docstring；``inspect.getsource`` 的结果是 Module→FunctionDef）。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0] if tree.body else None
    body = list(node.body) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # 跳过 docstring
    return body


def _string_constants(func: Any) -> list[str]:
    """函数体里出现的字符串**字面量**（不含注释、不含文档字符串）。"""
    return [
        n.value
        for stmt in _function_body(func)
        for n in ast.walk(stmt)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


@pytest.fixture(autouse=True)
def _isolate_probe_state(monkeypatch: pytest.MonkeyPatch):
    """每个用例都从"未探测"的干净状态开始，并从环境里摘掉相关变量。"""
    monkeypatch.setattr(web_view, "_AVAILABLE_CACHE", None)
    monkeypatch.setattr(web_view, "_ACTIVE_PROFILE", web_view.PROFILE_DEFAULT)
    monkeypatch.setattr(web_view, "_LAST_REASON", "")
    for key in (
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "QTWEBENGINE_DISABLE_SANDBOX",
        "QT_OPENGL",
        "QT_QUICK_BACKEND",
        web_view.AVAIL_ENV,
    ):
        monkeypatch.delenv(key, raising=False)


# --------------------------------------------------------------------- 标志集合
def test_default_flags_do_not_disable_gpu() -> None:
    """默认 profile 绝不能再出现那两个会自己制造 GPU 报错的开关。"""
    flags = web_view._DEFAULT_FLAGS
    assert "--disable-gpu" not in flags
    assert "--disable-gpu-compositing" not in flags
    assert "--disable-accelerated-2d-canvas" not in flags


def test_no_sandbox_flags_anywhere() -> None:
    """不再注入 ``--no-sandbox`` / ``QTWEBENGINE_DISABLE_SANDBOX``（保留 Chromium 沙箱）。"""
    assert "--no-sandbox" not in web_view._DEFAULT_FLAGS
    assert "--no-sandbox" not in web_view._SOFTWARE_FLAGS
    assert "QTWEBENGINE_DISABLE_SANDBOX" not in web_view._PROBE_SOURCE
    # 按字面量检查（注释里提到这个名字不算违规）
    literals = " ".join(_string_constants(web_view.prepare_webengine_env))
    assert "SANDBOX" not in literals.upper()
    assert "no-sandbox" not in literals


def test_software_flags_keep_software_rasterizer() -> None:
    """兜底 profile 仍**不能**带 ``--disable-software-rasterizer``。

    它会连软件路径一起禁掉，在"没有 GPU"的机器上让上下文创建彻底失败 ——
    这是项目早期踩过并已移除的坑，这里防止它被重新加回来。
    """
    assert "--disable-gpu" in web_view._SOFTWARE_FLAGS
    assert "--disable-software-rasterizer" not in web_view._SOFTWARE_FLAGS


# --------------------------------------------------------------------- 环境注入
def test_default_profile_sets_no_software_gl(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setattr(web_view, "_ACTIVE_PROFILE", web_view.PROFILE_DEFAULT)
    web_view.prepare_webengine_env()

    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == web_view._DEFAULT_FLAGS
    assert "QT_OPENGL" not in os.environ
    assert "QT_QUICK_BACKEND" not in os.environ
    assert "QTWEBENGINE_DISABLE_SANDBOX" not in os.environ


def test_software_profile_sets_qt_software(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    web_view.prepare_webengine_env(web_view.PROFILE_SOFTWARE)

    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == web_view._SOFTWARE_FLAGS
    assert os.environ["QT_OPENGL"] == "software"
    assert os.environ["QT_QUICK_BACKEND"] == "software"


def test_user_env_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户显式设置的值必须保留（``setdefault`` 语义）。"""
    import os

    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--my-own-flag")
    monkeypatch.setenv("QT_OPENGL", "desktop")
    web_view.prepare_webengine_env(web_view.PROFILE_SOFTWARE)

    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--my-own-flag"
    assert os.environ["QT_OPENGL"] == "desktop"


# --------------------------------------------------------------------- 探测分类
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "[123:456:1006/172755.160:FATAL:named-platform-channel-pipe(89)] "
            "Check failed: . : 拒绝访问。 (0x5)",
            web_view.REASON_PIPE,
        ),
        (
            "ERROR:gpu_channel_manager.cc(956) Failed to create GLES3 context, "
            "fallback to GLES2.\nContextResult::kFatalFailure: "
            "Failed to create shared context for virtualization.",
            web_view.REASON_GL,
        ),
        ("Traceback: ImportError: cannot import name 'QWebEngineView'", web_view.REASON_OTHER),
    ],
)
def test_classify_probe_failure(text: str, expected: str) -> None:
    assert web_view.classify_probe_failure(text) == expected


def test_hint_distinguishes_pipe_from_gl() -> None:
    pipe = web_view.webengine_environment_hint(web_view.REASON_PIPE)
    gl = web_view.webengine_environment_hint(web_view.REASON_GL)
    assert "命名管道" in pipe
    assert "显卡" in pipe  # 明确说与显卡无关
    assert "GL 上下文" in gl
    assert pipe != gl


# --------------------------------------------------------------------- 两段式探测
def test_gl_failure_falls_back_to_software(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认配置因 GL 失败 → 才允许用软件渲染兜底，并记录 profile。"""
    calls: list[str] = []

    def fake_probe(profile: str, timeout_s: float) -> tuple[bool, str, str]:
        calls.append(profile)
        if profile == web_view.PROFILE_DEFAULT:
            return False, web_view.REASON_GL, "gpu_channel_manager ..."
        return True, "", ""

    monkeypatch.setattr(web_view, "_run_probe", fake_probe)
    assert web_view.is_webengine_available() is True
    assert calls == [web_view.PROFILE_DEFAULT, web_view.PROFILE_SOFTWARE]
    assert web_view.active_profile() == web_view.PROFILE_SOFTWARE
    assert web_view.last_probe_reason() == ""


def test_pipe_failure_does_not_try_software(monkeypatch: pytest.MonkeyPatch) -> None:
    """命名管道被拒是运行环境限制，重试软件渲染毫无意义 —— 不许重试、不许降 profile。"""
    calls: list[str] = []

    def fake_probe(profile: str, timeout_s: float) -> tuple[bool, str, str]:
        calls.append(profile)
        return False, web_view.REASON_PIPE, "named-platform-channel-pipe ... 拒绝访问"

    monkeypatch.setattr(web_view, "_run_probe", fake_probe)
    assert web_view.is_webengine_available() is False
    assert calls == [web_view.PROFILE_DEFAULT]
    assert web_view.active_profile() == web_view.PROFILE_DEFAULT
    assert web_view.last_probe_reason() == web_view.REASON_PIPE


def test_default_success_keeps_gpu_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(profile: str, timeout_s: float) -> tuple[bool, str, str]:
        assert profile == web_view.PROFILE_DEFAULT
        return True, "", ""

    monkeypatch.setattr(web_view, "_run_probe", fake_probe)
    assert web_view.is_webengine_available() is True
    assert web_view.active_profile() == web_view.PROFILE_DEFAULT


def test_env_flag_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(profile: str, timeout_s: float):  # noqa: ANN201
        raise AssertionError("不应真正探测")

    monkeypatch.setattr(web_view, "_run_probe", boom)
    monkeypatch.setenv(web_view.AVAIL_ENV, "0")
    assert web_view.is_webengine_available() is False
    monkeypatch.setenv(web_view.AVAIL_ENV, "1")
    assert web_view.is_webengine_available() is True


def test_probe_source_formats_for_both_profiles() -> None:
    """两个 profile 都能格式化出可编译的探测脚本（``{}`` 转义没写错）。"""
    for profile in (web_view.PROFILE_DEFAULT, web_view.PROFILE_SOFTWARE):
        flags, qt_env = web_view._profile_env(profile)
        src = web_view._PROBE_SOURCE.format(flags=flags, qt_env=qt_env)
        compile(src, f"<probe:{profile}>", "exec")


# --------------------------------------------------------------------- 其它
def test_default_backend_is_auto() -> None:
    """默认后端改为 ``auto``（浏览器引擎优先，失败回退原生）。"""
    assert UiConfig().score_backend == "auto"


def test_run_gui_does_not_inject_qt_gl_env() -> None:
    """``run_gui()`` 不许再预注入 Qt/GPU 环境变量。

    旧实现在 ``QApplication`` 之前无条件调用 ``prepare_webengine_env()``，
    于是软件渲染变量会随 ``os.environ`` 泄漏给所有子进程
    （MuseScore 继承 ``QT_QPA_PLATFORM`` 崩溃就是同类问题的另一例）。
    """
    from zpymusic.ui import main_window

    assert "prepare_webengine_env" not in _called_names(main_window.run_gui)


# =====================================================================
# 两个后端的接口一致性
#
# 真实事故（M6）：默认后端从 native 改成 auto 之后，用户发现「同步播放」页的
# 「适应宽度」失效、谱面永远停在 100%。原因不是缩放逻辑坏了，而是
# WebScoreView **根本没有实现** set_fit_width / fit_width_enabled / zoom_changed ——
# 而 ui/score_host.py 是用 hasattr 探测能力的，缺了就**静默降级**：
# 按钮显示为已勾选，却什么都不做。
#
# 下面这组用例就是那次事故的护栏：因为 WebScoreView 不继承 ScoreView，
# 少写一个方法不会有任何报错，只能靠"逐名对照"来发现。
# =====================================================================

#: 界面（ui/score_host.py、tab_play.py、preview.py）与控制器真正会调用的接口。
#: 新增后端方法时如果只加在 NativeScoreView 上，这里会失败。
REQUIRED_VIEW_API = (
    "load",
    "systems",
    "root",
    "element_clicked",
    "set_highlight",
    "clear_highlight",
    "set_highlight_style",
    "set_follow",
    "follow",
    "scroll_to_element",
    "scroll_to_system",
    "measure_systems",
    "build_system_map",
    "loaded_system_count",
    "overlay_right_inset",
    # —— 缩放 / 适应宽度（事故就出在这一组）——
    "set_zoom",
    "zoom",
    "zoom_changed",
    "set_fit_width",
    "fit_width",
    "fit_width_enabled",
)


@pytest.mark.parametrize("name", REQUIRED_VIEW_API)
def test_both_backends_expose_name(name: str) -> None:
    from zpymusic.sync.score_view import NativeScoreView
    from zpymusic.sync.web_view import WebScoreView

    for cls in (NativeScoreView, WebScoreView):
        assert hasattr(cls, name), f"{cls.__name__} 缺少 {name}（会导致界面静默降级）"


def test_fit_width_defaults_are_the_same() -> None:
    """两个后端的"适应宽度"默认值必须一致，且都是开。

    宿主用 ``getattr(score_view, "_fit_width", True)`` 决定缩放条上
    「适应宽度」按钮的初始勾选状态 —— 若后端没有这个属性，按钮会显示"已勾选"
    但实际不生效（正是事故现象）。
    """
    import inspect

    from zpymusic.sync.score_view import NativeScoreView
    from zpymusic.sync.web_view import WebScoreView

    for cls in (NativeScoreView, WebScoreView):
        src = inspect.getsource(cls)
        assert "self._fit_width = True" in src, f"{cls.__name__} 的 _fit_width 默认值不是 True"


def test_zoom_range_is_shared() -> None:
    """两个后端共用 30%–400% 的区间（不要各写一份上下限）。"""
    from zpymusic.sync.score_view import MAX_ZOOM, MIN_ZOOM

    assert web_view.clamp_zoom(0.01) == MIN_ZOOM
    assert web_view.clamp_zoom(99.0) == MAX_ZOOM
    assert MIN_ZOOM <= web_view.clamp_zoom(0.41) <= MAX_ZOOM


def test_content_width_and_fit_math() -> None:
    """「适应宽度」的输入与算式（纯函数，不需要启动 Chromium）。"""
    from zpymusic.sync.score_view import SystemRef

    systems = [
        SystemRef(index=1, file="a.svg", width=2100, height=1440),
        SystemRef(index=2, file="b.svg", width=1800, height=1440),
    ]
    assert web_view.content_width_of(systems) == 2100
    assert web_view.content_width_of([]) == 0.0

    # 1200 宽的宿主：减去滚动条 16 与两边各 8 的留白 → 1168 可用
    assert web_view.fit_zoom_for(2100, 1168) == pytest.approx(0.5562, abs=1e-4)
    # 窄窗口会被夹到 MIN_ZOOM，宽窗口会被夹到 MAX_ZOOM
    assert web_view.fit_zoom_for(2100, 400) == web_view.clamp_zoom(400 / 2100)
    assert web_view.fit_zoom_for(2100, 100000) == web_view.clamp_zoom(100000 / 2100)
    # 退化输入不应抛异常
    assert web_view.fit_zoom_for(0, 1168) == 1.0
    assert web_view.fit_zoom_for(2100, 0) == 1.0


def test_page_css_keeps_left_edge_reachable() -> None:
    """页面在"谱面比视口宽"时不能把左边裁掉。

    实测（2100px 谱面 / 1200px 视口）：``align-items: center`` 会让 .sys 的
    left = -458，而 scrollWidth 只有 1642 —— 最左边 458px 永远滚不到。
    修复是 ``align-items: safe center``（溢出时退回 start）。
    """
    from zpymusic.sync.local_server import PAGE_HTML

    assert "align-items: safe center" in PAGE_HTML
    # 必须保留普通 center 作为不支持 safe 关键字的浏览器的回退
    assert "align-items:center" in PAGE_HTML.replace(" ", "") or "align-items: center" in PAGE_HTML
