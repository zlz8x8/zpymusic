"""MIDI → WAV（FluidSynth）与 WAV → MP3（ffmpeg）（需求 §5.2 F1.6 / D3 / D4）。

M0 实测结论（见 docs/requirements.md 附录 A）：

* ``pyfluidsynth`` 1.4.0 在 Windows 上即使把 DLL 目录加入搜索路径也**没有**
  ``Sequencer.play_midi_file``，且用音频设备驱动无法离线渲染。
* 因此采用 **``fluidsynth.exe -a file`` 离线渲染**：
  ``-ni -g <gain> -r <rate> -a file -O s16 -T wav -F <out.wav> <sf> <mid>``
  实测 canon（130 s）**4.67 s** 完成，比实时快约 28 倍，且 WAV 时长与
  ``sync.json.duration_ms`` 一致（130.2 s）。
* 渲染完成后用 **ffprobe 校验时长**，偏差 > 5% 视为失败并报告（防止静默产出错音频）。

本模块**不导入 Qt**。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..common.errors import AudioRenderError
from ..common.log import get_logger

log = get_logger(__name__)

__all__ = ["AudioTools", "RenderAudioResult", "midi_to_wav", "wav_to_mp3", "probe_audio"]

DurationFn = Callable[[float], None]


@dataclass
class AudioTools:
    """音频工具集合（由 ``common.deps`` 探测得到）。"""

    fluidsynth_exe: Path | None = None
    ffmpeg: Path | None = None
    ffprobe: Path | None = None

    @property
    def can_render_wav(self) -> bool:
        return self.fluidsynth_exe is not None and self.fluidsynth_exe.is_file()

    @property
    def can_encode_mp3(self) -> bool:
        return self.ffmpeg is not None and self.ffmpeg.is_file()

    @property
    def can_probe(self) -> bool:
        return self.ffprobe is not None and self.ffprobe.is_file()


@dataclass
class RenderAudioResult:
    wav: Path | None = None
    mp3: Path | None = None
    wav_duration_s: float = 0.0
    mp3_size: int = 0
    elapsed_s: float = 0.0
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def resolve_tools(ffmpeg: Path | None, fluidsynth_dir: Path | None) -> AudioTools:
    """根据配置解析出可用的音频工具路径。"""
    fs_exe: Path | None = None
    if fluidsynth_dir is not None and Path(fluidsynth_dir).is_dir():
        cand = Path(fluidsynth_dir) / "fluidsynth.exe"
        if cand.is_file():
            fs_exe = cand
    if fs_exe is None:
        found = shutil.which("fluidsynth")
        if found:
            fs_exe = Path(found)

    ff: Path | None = None
    if ffmpeg is not None and Path(ffmpeg).is_file():
        ff = Path(ffmpeg)
    if ff is None:
        found = shutil.which("ffmpeg")
        if found:
            ff = Path(found)

    probe: Path | None = None
    if ff is not None:
        cand = ff.with_name("ffprobe" + ff.suffix)
        if cand.is_file():
            probe = cand
    if probe is None:
        found = shutil.which("ffprobe")
        if found:
            probe = Path(found)

    return AudioTools(fluidsynth_exe=fs_exe, ffmpeg=ff, ffprobe=probe)


# --------------------------------------------------------------------------- 渲染
def midi_to_wav(
    midi: Path,
    wav: Path,
    soundfont: Path,
    tools: AudioTools,
    *,
    sample_rate: int = 44100,
    gain: float = 0.6,
    reverb: bool = True,
    chorus: bool = True,
    expected_duration_ms: int = 0,
    timeout_s: int = 1800,
) -> RenderAudioResult:
    """用 FluidSynth 把 MIDI 离线渲染成 WAV。

    Raises:
        AudioRenderError: FluidSynth 不可用、渲染失败、或产物时长明显不对。
    """
    import time  # noqa: PLC0415

    if not tools.can_render_wav:
        raise AudioRenderError(
            "FluidSynth 不可用：未找到 fluidsynth.exe。"
            "请下载 FluidSynth Windows 版并把 fluidsynth.exe / libfluidsynth-3.dll 等"
            "放入 tools/fluidsynth 目录"
        )
    if not Path(midi).is_file():
        raise AudioRenderError(f"MIDI 文件不存在：{midi}")
    if not Path(soundfont).is_file():
        raise AudioRenderError(f"音色库不存在：{soundfont}")

    wav = Path(wav)
    wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists():
        try:
            wav.unlink()
        except OSError as e:
            raise AudioRenderError(f"无法覆盖已存在的 WAV：{wav}（{e}）") from e

    cmd = [
        str(tools.fluidsynth_exe),
        "-ni",  # 不读 MIDI 输入、不进入 shell
        "-g", f"{gain:g}",
        "-r", str(sample_rate),
        "-a", "file",  # 文件音频驱动 = 离线渲染
        "-O", "s16",
        "-T", "wav",
        "-F", str(wav),
        "-R", "1" if reverb else "0",
        "-C", "1" if chorus else "0",
        str(soundfont),
        str(midi),
    ]
    log.debug("FluidSynth 渲染：%s", " ".join(cmd))

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            cwd=str(tools.fluidsynth_exe.parent),  # 便于它找到同目录的依赖 DLL
        )
    except subprocess.TimeoutExpired as e:
        raise AudioRenderError(f"FluidSynth 渲染超时（>{timeout_s}s）：{midi.name}") from e
    except OSError as e:
        raise AudioRenderError(f"无法启动 FluidSynth：{e}") from e
    elapsed = time.perf_counter() - t0

    if proc.returncode != 0 or not wav.is_file() or wav.stat().st_size == 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-600:]
        raise AudioRenderError(
            f"FluidSynth 渲染失败（returncode={proc.returncode}）：{tail}"
        )

    result = RenderAudioResult(wav=wav, elapsed_s=elapsed)
    dur = probe_audio(wav, tools)
    result.wav_duration_s = dur
    if dur and expected_duration_ms:
        # 注意：渲染音频比乐谱长是**正常**的 —— FluidSynth 会把混响 / 延音尾巴
        # 一并渲染出来（实测 tango 36.9s 的乐谱输出 40.0s 音频，多出的 3.1s 全是尾巴）。
        # 因此这里的阈值放宽，只用来抓"明显不对"的情况（例如渲染被截断）。
        expected = expected_duration_ms / 1000.0
        drift = (dur - expected) / expected
        if drift < -0.02:
            result.warnings.append(
                f"渲染出的 WAV 时长 {dur:.1f}s 短于乐谱时长 {expected:.1f}s（{drift*100:.1f}%），"
                "音频可能被截断"
            )
        elif drift > 0.25:
            result.warnings.append(
                f"渲染出的 WAV 时长 {dur:.1f}s 明显长于乐谱时长 {expected:.1f}s"
                f"（{drift*100:.1f}%），请检查音色库的循环设置"
            )
        else:
            log.debug(
                "WAV %.1f s vs 乐谱 %.1f s（相差 %+.1f%%，属混响/延音尾巴，正常）",
                dur, expected, drift * 100,
            )
    log.info(
        "WAV 完成：%s（%.1f s 音频，耗时 %.2f s，%.1f× 实时）",
        wav.name,
        dur,
        elapsed,
        (dur / elapsed) if elapsed > 0 else 0,
    )
    return result


def wav_to_mp3(
    wav: Path,
    mp3: Path,
    tools: AudioTools,
    *,
    bitrate_kbps: int = 192,
    timeout_s: int = 900,
) -> Path:
    """用 ffmpeg 把 WAV 编码为 MP3。

    Raises:
        AudioRenderError: ffmpeg 不可用或编码失败。
    """
    if not tools.can_encode_mp3:
        raise AudioRenderError("ffmpeg 不可用：无法把 WAV 编码为 MP3")
    if not Path(wav).is_file():
        raise AudioRenderError(f"WAV 文件不存在：{wav}")

    mp3 = Path(mp3)
    mp3.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(tools.ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(wav),
        "-codec:a", "libmp3lame",
        "-b:a", f"{bitrate_kbps}k",
        str(mp3),
    ]
    log.debug("ffmpeg 编码：%s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout_s
        )
    except subprocess.TimeoutExpired as e:
        raise AudioRenderError(f"ffmpeg 编码超时（>{timeout_s}s）") from e
    except OSError as e:
        raise AudioRenderError(f"无法启动 ffmpeg：{e}") from e

    if proc.returncode != 0 or not mp3.is_file() or mp3.stat().st_size == 0:
        tail = (proc.stderr or "").strip()[-600:]
        raise AudioRenderError(f"ffmpeg 编码失败（returncode={proc.returncode}）：{tail}")
    log.info("MP3 完成：%s（%.2f MB）", mp3.name, mp3.stat().st_size / 1e6)
    return mp3


def probe_audio(path: Path, tools: AudioTools) -> float:
    """用 ffprobe 读取音频时长（秒）；失败返回 0.0。"""
    if not tools.can_probe:
        return 0.0
    cmd = [
        str(tools.ffprobe),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=120
        )
        data = json.loads(proc.stdout or "{}")
        return float(data.get("format", {}).get("duration", 0.0))
    except Exception:  # noqa: BLE001 - 探测失败不影响主流程
        return 0.0
