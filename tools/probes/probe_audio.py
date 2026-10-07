"""M0 探针 3：验证 MIDI → WAV 两条渲染路径。

路径 A：pyfluidsynth（需先让 ctypes 找到 libfluidsynth-3.dll）
路径 B：tools/fluidsynth/fluidsynth.exe 离线渲染（-a file 音频驱动，不依赖实时播放）
"""

from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
FS_DIR = ROOT / "tools" / "fluidsynth"
SF2 = ROOT / "sound" / "MuseScore_General.sf3"
SAMPLE = ROOT / "staff" / "musicxml" / "canon-in-d-easy.mxl"


def make_midi() -> Path:
    import verovio

    tk = verovio.toolkit()
    tk.loadFile(str(SAMPLE))
    data = base64.b64decode(tk.renderToMIDI())
    p = OUT / "canon.mid"
    p.write_bytes(data)
    print(f"MIDI written: {p.name} ({len(data)} bytes)")
    return p


def path_a(midi: Path) -> None:
    print("\n--- path A: pyfluidsynth ---")
    if not FS_DIR.is_dir():
        print("SKIP: tools/fluidsynth missing")
        return
    os.add_dll_directory(str(FS_DIR))
    os.environ["PATH"] = str(FS_DIR) + os.pathsep + os.environ.get("PATH", "")
    lib = ctypes.WinDLL(str(FS_DIR / "libfluidsynth-3.dll"))
    major, minor, micro = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    lib.fluid_version(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(micro))
    print(f"libfluidsynth {major.value}.{minor.value}.{micro.value}")
    try:
        import fluidsynth
    except Exception as e:  # noqa: BLE001
        print("import fluidsynth FAILED:", type(e).__name__, e)
        return
    print("fluidsynth imported OK, version:", getattr(fluidsynth, "__version__", "?"))
    t0 = time.perf_counter()
    fs = fluidsynth.Synth(samplerate=44100.0, gain=0.6)
    sfid = fs.sfload(str(SF2))
    print("sfload id:", sfid)
    fs.program_select(0, sfid, 0, 0)
    fs.start(driver="dsound")
    seq = fluidsynth.Sequencer()
    seq.register_fluidsynth(fs)
    play_id = seq.play_midi_file(str(midi))
    print("play_midi_file id:", play_id)
    wav = OUT / "canon_pyfs.wav"
    fs.setting("audio.file.name", str(wav))
    fs.setting("audio.file.type", "wav")
    # 用 sequencer 的 tick 回调驱动离线渲染
    try:
        import time as _t

        deadline = _t.time() + 20
        while seq.get_tick() is not None and _t.time() < deadline:
            _t.sleep(0.05)
            if seq.get_tick() is None:
                break
    except Exception as e:  # noqa: BLE001
        print("sequencer loop issue:", e)
    fs.delete()
    print("elapsed %.2fs" % (time.perf_counter() - t0), "wav exists:", wav.exists())


def path_b(midi: Path) -> None:
    print("\n--- path B: fluidsynth.exe offline render ---")
    exe = FS_DIR / "fluidsynth.exe"
    if not exe.exists():
        print("SKIP: fluidsynth.exe missing")
        return
    wav = OUT / "canon_cli.wav"
    if wav.exists():
        wav.unlink()
    cmd = [
        str(exe),
        "-ni",
        "-g", "0.6",
        "-r", "44100",
        "-a", "file",
        "-O", "s16",
        "-T", "wav",
        "-F", str(wav),
        str(SF2),
        str(midi),
    ]
    print("cmd:", " ".join(cmd))
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    dt = time.perf_counter() - t0
    print("returncode:", r.returncode, f"elapsed {dt:.2f}s")
    if r.stdout:
        print("stdout tail:", r.stdout.strip()[-400:])
    if r.stderr:
        print("stderr tail:", r.stderr.strip()[-400:])
    if wav.exists():
        print(f"WAV: {wav.stat().st_size} bytes")
    else:
        print("WAV NOT CREATED; trying -a file with -o audio.file.name")
        wav2 = OUT / "canon_cli2.wav"
        cmd2 = [
            str(exe), "-ni", "-g", "0.6", "-r", "44100", "-a", "file",
            "-o", f"audio.file.name={wav2}",
            "-o", "audio.file.type=wav",
            str(SF2), str(midi),
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=180)
        print("fallback returncode:", r2.returncode)
        print("stderr tail:", (r2.stderr or "").strip()[-500:])
        print("wav2 exists:", wav2.exists(), wav2.stat().st_size if wav2.exists() else "")


def main() -> int:
    midi = make_midi()
    path_b(midi)
    path_a(midi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
