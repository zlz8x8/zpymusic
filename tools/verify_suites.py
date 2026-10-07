"""M1 验收脚本：对 `staff/suites` 下的全部套件做交叉一致性验证。

验证项（对应需求 §8.2 验收标准）：
  1. 套件完整性（MusicXML / SVG / MIDI / sync.json 齐备）
  2. `sync.json` 里每个音符 ID 都能在对应 SVG 中找到 —— **必须 100%**
  3. MIDI 时长 vs `sync.json` 时间轴时长 —— 同源校验（应 < 100 ms）
  4. MP3 / WAV 时长 >= 乐谱时长（允许混响尾巴，但不能更短）
  5. 用 `sync.json` 反算的高亮序列与 MIDI 的 note-on 时间序列比对（§8.2 标准 3）

用法::

    $env:PYTHONPATH=".../src"
    python tools/verify_suites.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import mido  # noqa: E402

from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.core.sync_model import SyncDoc  # noqa: E402
from zpymusic.sync.timeline import Timeline  # noqa: E402

SVG_ID_RE = re.compile(r'id="([^"]+)"')


def midi_timeline(path: Path) -> tuple[list[float], float]:
    """返回 ``(note-on 绝对时间列表(秒), 文件总时长(秒))``。

    三个坑（都踩过）：

    * **不能用 ``msg.time`` 直接累加** —— 它是 tick，需要按 tempo 换算。
    * **不能各轨道独立累加** —— 格式 1 的多条轨道各自从 0 开始，
      必须**先按绝对 tick 合并所有轨道**再换算时间（否则会把各轨时长相加）。
    * **不能用 ``MidiFile.play()``** —— 它是**按真实时间**产出事件的生成器，
      对 2 分钟的曲子会真的阻塞 2 分钟。
    """
    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat

    merged: list[tuple[int, object]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            merged.append((tick, msg))
    merged.sort(key=lambda x: x[0])

    tempo = 500_000  # 默认 120 BPM
    prev_tick = 0
    elapsed = 0.0
    onsets: list[float] = []
    for tick, msg in merged:
        if tick > prev_tick:
            elapsed += mido.tick2second(tick - prev_tick, tpb, tempo)
            prev_tick = tick
        if msg.type == "set_tempo":  # type: ignore[attr-defined]
            tempo = msg.tempo  # type: ignore[attr-defined]
        elif msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:  # type: ignore[attr-defined]
            onsets.append(elapsed)
    return sorted(onsets), elapsed


def _has_within(sorted_values: list[float], target: float, tol: float) -> bool:
    """``sorted_values`` 中是否存在 ``target ± tol`` 内的值（二分查找）。"""
    import bisect

    i = bisect.bisect_left(sorted_values, target - tol)
    return i < len(sorted_values) and sorted_values[i] <= target + tol


def main() -> int:
    import time

    root = REPO / "staff" / "suites"
    suites = discover_suites(root)
    if not suites:
        print(f"!! 未在 {root} 找到套件，请先运行 zpymusic generate")
        return 2

    only = sys.argv[1:] if len(sys.argv) > 1 else None
    if only:
        suites = [s for s in suites if any(o in s.base for o in only)]
        print(f"（按参数过滤到 {len(suites)} 个套件）")

    print(f"验证 {len(suites)} 个套件：{root}\n")
    header = f"{'套件':44s} {'元素':>6s} {'事件':>6s} {'SVG':>5s} {'ID一致':>7s} {'MIDI-Δt':>8s} {'音频-Δt':>8s} {'note-on匹配':>11s}"
    print(header)
    print("-" * len(header))

    failures: list[str] = []
    total_ids = 0

    for s in suites:
        t0 = time.perf_counter()
        problems = s.validate()
        if problems:
            failures.append(f"{s.base}: 完整性 {problems}")
            continue

        sync = s.load_sync()
        unique_ids = {n["id"] for n in sync.notes}
        total_ids += len(sync.notes)
        t_sync = time.perf_counter() - t0

        # --- 1) ID 与 SVG 一致性 ---
        missing_total = 0
        checked = 0
        for sysinfo in sync.systems:
            if not sysinfo.file:
                continue
            text = (s.dir / sysinfo.file).read_text(encoding="utf-8")
            present = set(SVG_ID_RE.findall(text))
            checked += len(sysinfo.note_ids)
            missing_total += sum(1 for i in sysinfo.note_ids if i not in present)
        id_ok = missing_total == 0
        if not id_ok:
            failures.append(f"{s.base}: {missing_total}/{checked} 个 ID 在 SVG 中缺失")

        # --- 2) MIDI 时长 vs 时间轴 ---
        ons, midi_sec = midi_timeline(s.layout.midi)
        midi_ms = int(round(midi_sec * 1000))
        dt_midi = abs(midi_ms - sync.duration_ms)
        if dt_midi > 100:
            failures.append(f"{s.base}: MIDI {midi_ms}ms vs 时间轴 {sync.duration_ms}ms")

        # --- 3) 音频时长 ---
        audio_dt = ""
        if s.has_mp3 or s.layout.wav.is_file():
            from zpymusic.core.audio_render import resolve_tools, probe_audio
            from zpymusic.common.deps import default_ffmpeg, default_fluidsynth_dir

            tools = resolve_tools(default_ffmpeg(), default_fluidsynth_dir())
            f = s.layout.mp3 if s.has_mp3 else s.layout.wav
            dur = probe_audio(f, tools)
            if dur:
                delta = dur * 1000 - sync.duration_ms
                audio_dt = f"{delta:+.0f}ms"
                if delta < -200:  # 音频比乐谱短 = 被截断
                    failures.append(f"{s.base}: 音频 {dur:.1f}s 短于乐谱 {sync.duration_ms/1000:.1f}s")

        # --- 4) 高亮序列 vs MIDI note-on 时间序（§8.2 标准 3） ---
        onsets_ms = sorted({n["onset_ms"] for n in sync.notes})
        ons_ms = sorted(t * 1000 for t in ons)
        # 两个序列都已排序 → 用二分查找（不能写成嵌套循环：four-seasons 是 43k × 29k）
        matched = sum(
            1 for o in onsets_ms if _has_within(ons_ms, o, 100)
        )
        ratio = matched / max(1, len(onsets_ms))
        match_txt = f"{ratio*100:.0f}%"
        if ratio < 0.98:
            failures.append(f"{s.base}: 高亮起点与 MIDI note-on 匹配率仅 {ratio*100:.1f}%")

        print(
            f"{s.base[:42]:44s} {len(unique_ids):6d} {len(sync.notes):6d} "
            f"{len(sync.systems):5d} {'OK' if id_ok else 'FAIL':>7s} "
            f"{dt_midi:6d}ms {audio_dt:>8s} {match_txt:>11s}  "
            f"[{time.perf_counter()-t0:.1f}s]",
            flush=True,
        )

    print()
    print(f"合计 {len(suites)} 个套件 / {total_ids} 个时间轴事件")
    if failures:
        print(f"\n!! {len(failures)} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("\n全部检查通过 ✓（ID 一致性 / MIDI 同源 / 音频未截断 / 高亮起点匹配）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
