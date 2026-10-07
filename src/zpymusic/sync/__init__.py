"""``zpymusic.sync`` —— 播放同步层（时间轴、高亮、视图、播放器、控制器）。

本包不依赖：``..core`` 之外的业务代码；``ui`` 依赖本包，反之不成立。
WebEngine 后端（:mod:`web_view` / :mod:`local_server`）按需导入，避免拖慢启动。
"""

from __future__ import annotations

from .controller import PlaybackController, PlaybackStatus
from .highlight import HighlightPlan, HighlightRect, SystemGeometryIndex, build_css
from .player import MAX_RATE, MIN_RATE, PlaybackBackend, PlayerState, QtMediaPlayer
from .score_view import NativeScoreView, ScoreView, SystemRef, create_score_view
from .svg_geometry import Box, SvgGeometry, flatten_svg, parse_svg, verovio_units_per_px
from .timeline import FrameDiff, HighlightTracker, Timeline

__all__ = [
    # 时间轴
    "Timeline",
    "HighlightTracker",
    "FrameDiff",
    # 高亮
    "HighlightPlan",
    "HighlightRect",
    "SystemGeometryIndex",
    "build_css",
    # 几何
    "Box",
    "SvgGeometry",
    "parse_svg",
    "flatten_svg",
    "verovio_units_per_px",
    # 视图
    "ScoreView",
    "NativeScoreView",
    "SystemRef",
    "create_score_view",
    # 播放
    "PlaybackBackend",
    "QtMediaPlayer",
    "PlayerState",
    "MIN_RATE",
    "MAX_RATE",
    # 控制器
    "PlaybackController",
    "PlaybackStatus",
]
