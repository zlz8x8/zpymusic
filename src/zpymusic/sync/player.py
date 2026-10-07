"""播放后端（需求 §5.3 F2.3–F2.5 / D6）。

Phase 1 用 ``QMediaPlayer``（零额外依赖、开发快），位置精度约 ±100 ms；
因此需求把 Phase 1 的同步目标定在 ≤100 ms，并保留 :class:`PlaybackBackend`
抽象，Phase 2 可换成 ``sounddevice``（对 WAV 精确 seek）冲击 ≤30 ms。

速率调节（F2.5 / Q1 决策）：用播放速率 50%–200%，**不是**重渲染 BPM。
`QMediaPlayer.setPlaybackRate` 在 Windows（DirectShow）上支持 0.5–2.0。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal

from ..common.log import get_logger

log = get_logger(__name__)

__all__ = ["PlayerState", "PlaybackBackend", "QtMediaPlayer", "MIN_RATE", "MAX_RATE"]

MIN_RATE = 0.5
MAX_RATE = 2.0


class PlayerState(str, Enum):
    """播放器状态。

    值使用 QMediaPlayer 的**枚举成员名**（``StoppedState`` 等），
    而不是它的小写字符串值 —— 因为 PySide6 的
    ``str(QMediaPlayer.PlaybackState.PlayingState)`` 得到的是
    ``"PlaybackState.PlayingState"``，取最后一段是 ``"PlayingState"``（大写 P）。

    ⚠ 早期版本这里写的是小写 ``"playingState"``，导致
    ``self.state != PlayerState.PLAYING`` **永远成立**，``_tick()`` 每帧直接 return，
    表现为"播放时完全不高亮、不跟随"（M4 实测定位）。
    """

    STOPPED = "StoppedState"
    PLAYING = "PlayingState"
    PAUSED = "PausedState"


@dataclass
class PlayerInfo:
    """当前媒体的基本信息。"""

    path: Path | None = None
    duration_ms: int = 0
    rate: float = 1.0


class PlaybackBackend(QObject):
    """播放后端接口。"""

    position_changed = Signal(int)
    duration_changed = Signal(int)
    state_changed = Signal(str)
    error = Signal(str)

    def load(self, path: Path) -> None:
        raise NotImplementedError

    def play(self) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def toggle(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def seek(self, ms: int) -> None:
        raise NotImplementedError

    def position(self) -> int:
        raise NotImplementedError

    def duration(self) -> int:
        raise NotImplementedError

    def state(self) -> PlayerState:
        raise NotImplementedError

    def set_rate(self, rate: float) -> float:
        raise NotImplementedError

    def set_volume(self, volume: float) -> None:
        raise NotImplementedError


class QtMediaPlayer(PlaybackBackend):
    """基于 ``QMediaPlayer`` + ``QAudioOutput`` 的实现。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: PLC0415

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self._player.setAudioOutput(self._audio)
        self._info = PlayerInfo()
        self._rate = 1.0
        self._last_error = ""

        # 注意：QMediaPlayer.positionChanged 携带 qlonglong，不能直接连到本类的
        # Signal(int)（PySide6 会报 "Failed to connect signal"），必须经一个普通方法转发。
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_media_status)

    # ------------------------------------------------------------------ 状态
    @property
    def qt_player(self):
        """暴露底层 player，便于测试与高级用法。"""
        return self._player

    @property
    def info(self) -> PlayerInfo:
        return self._info

    @property
    def rate(self) -> float:
        return self._rate

    # ------------------------------------------------------------------ 控制
    def load(self, path: Path) -> None:
        p = Path(path)
        if not p.is_file():
            self._emit_error(f"音频文件不存在：{p}")
            return
        self._info = PlayerInfo(path=p, duration_ms=0, rate=self._rate)
        self._last_error = ""
        self._player.setSource(QUrl.fromLocalFile(str(p)))
        log.info("已载入音频：%s", p.name)

    def play(self) -> None:
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def toggle(self) -> None:
        if self.state() == PlayerState.PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._player.stop()

    def seek(self, ms: int) -> None:
        target = max(0, int(ms))
        if self._info.duration_ms:
            target = min(target, self._info.duration_ms)
        self._player.setPosition(target)
        self.position_changed.emit(target)

    def position(self) -> int:
        return int(self._player.position())

    def duration(self) -> int:
        return int(self._player.duration())

    @staticmethod
    def map_qt_state(raw) -> PlayerState:  # noqa: ANN001
        """把 ``QMediaPlayer.PlaybackState`` 映射成本类枚举。

        抽成静态纯函数是为了可单测：``QMediaPlayer.setPlaybackState`` 并不存在
        （状态由播放器内部驱动），因此只能直接测这个映射本身。
        """
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: PLC0415

        if raw == QMediaPlayer.PlaybackState.PlayingState:
            return PlayerState.PLAYING
        if raw == QMediaPlayer.PlaybackState.PausedState:
            return PlayerState.PAUSED
        return PlayerState.STOPPED

    def state(self) -> PlayerState:
        """当前状态。

        直接按 Qt 枚举做映射，**不做字符串解析** —— 字符串形式依赖 PySide6 的
        ``repr``（``"PlaybackState.PlayingState"``），换版本就可能变，
        早期版本正是栽在这里（详见 :class:`PlayerState` 的说明）。
        """
        return self.map_qt_state(self._player.playbackState())

    def set_rate(self, rate: float) -> float:
        """设置播放速率，返回实际生效的值（钳制到 0.5–2.0）。"""
        clamped = max(MIN_RATE, min(MAX_RATE, float(rate)))
        self._player.setPlaybackRate(clamped)
        actual = float(self._player.playbackRate())
        # 某些平台/编解码器会拒绝速率；以实际生效值为准
        self._rate = actual if actual > 0 else clamped
        if abs(self._rate - clamped) > 1e-6:
            log.warning("后端不支持速率 %.2f，实际为 %.2f", clamped, self._rate)
        return self._rate

    def set_volume(self, volume: float) -> None:
        self._audio.setVolume(max(0.0, min(1.0, float(volume))))

    # ------------------------------------------------------------------ 内部
    def _on_position(self, position: int) -> None:
        """转发播放位置（QMediaPlayer 用 qlonglong，需转成 int 再发本类信号）。"""
        self.position_changed.emit(int(position))

    def _on_duration(self, duration: int) -> None:
        self._info.duration_ms = int(duration)
        self.duration_changed.emit(int(duration))

    def _on_state(self, state) -> None:
        self.state_changed.emit(self.state().value)

    def _on_media_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: PLC0415

        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._emit_error(
                f"无法播放该音频（不支持的格式或缺少解码器）："
                f"{self._info.path.name if self._info.path else '?'}"
            )
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            log.debug("播放结束")
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            log.debug("媒体已加载，时长 %d ms", self.duration())

    def _on_error(self, error, message: str) -> None:
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: PLC0415

        if error == QMediaPlayer.Error.NoError:
            return
        self._emit_error(message or f"播放器错误：{error}")

    def _emit_error(self, text: str) -> None:
        if text == self._last_error:
            return
        self._last_error = text
        log.error("%s", text)
        self.error.emit(text)
