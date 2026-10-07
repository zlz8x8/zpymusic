"""时间轴与高亮追踪的单测（需求 §5.3 的同步算法）。

这些测试是 M4 播放界面的正确性基础，且完全不依赖 Qt / Verovio。
"""

from __future__ import annotations

import pytest

from zpymusic.sync.timeline import HighlightTracker, Timeline


def make_timeline() -> Timeline:
    """三个音符 + 一个和弦（同时发声）：
        a: 0–300      b: 300–600      c: 600–900
        d,e（和弦）: 900–1200
    """
    notes = [
        {"id": "a", "onset_ms": 0, "dur_ms": 300},
        {"id": "b", "onset_ms": 300, "dur_ms": 300},
        {"id": "c", "onset_ms": 600, "dur_ms": 300},
        {"id": "d", "onset_ms": 900, "dur_ms": 300},
        {"id": "e", "onset_ms": 900, "dur_ms": 300},
    ]
    measures = [
        {"id": "m1", "onset_ms": 0},
        {"id": "m2", "onset_ms": 600},
    ]
    return Timeline(notes, measures=measures, duration_ms=1200)


class TestTimeline:
    def test_len_and_note_count(self) -> None:
        tl = make_timeline()
        assert len(tl) == 5
        assert tl.note_count == 5
        assert tl.duration_ms == 1200

    @pytest.mark.parametrize(
        "ms,expected",
        [
            (0, ("a",)),
            (150, ("a",)),
            (299, ("a",)),
            (300, ("b",)),
            (599, ("b",)),
            (600, ("c",)),
            (900, ("d", "e")),
            (1199, ("d", "e")),
            (1200, ()),  # 末尾开区间
            (5000, ()),
            (-1, ()),
        ],
    )
    def test_active_at(self, ms: int, expected: tuple[str, ...]) -> None:
        assert make_timeline().active_at(ms) == expected

    def test_active_at_empty_timeline(self) -> None:
        assert Timeline([]).active_at(100) == ()

    def test_measure_at(self) -> None:
        tl = make_timeline()
        assert tl.measure_at(0) == "m1"
        assert tl.measure_at(599) == "m1"
        assert tl.measure_at(600) == "m2"
        assert tl.measure_at(10_000) == "m2"

    def test_measure_at_without_measures(self) -> None:
        assert Timeline([{"id": "a", "onset_ms": 0, "dur_ms": 10}]).measure_at(5) == ""

    def test_onset_of_and_rend_suffix(self) -> None:
        tl = Timeline([{"id": "x-rend2", "onset_ms": 250, "dur_ms": 100}])
        # 展开 ID 应归一为可在 SVG 中找到的 base
        assert tl.by_id == {"x": (250, 100)}
        assert tl.onset_of("x") == 250
        assert tl.onset_of("x-rend2") == 250
        assert tl.onset_of("不存在") is None

    def test_zero_duration_is_playable(self) -> None:
        """装饰音等零时长事件不能变成"永不命中"。"""
        tl = Timeline([{"id": "g", "onset_ms": 100, "dur_ms": 0}])
        assert tl.active_at(100) == ("g",)

    def test_rest_excluded_by_default(self) -> None:
        tl = Timeline([{"id": "r", "onset_ms": 0, "dur_ms": 100, "is_rest": True}])
        assert len(tl) == 0
        tl2 = Timeline(
            [{"id": "r", "onset_ms": 0, "dur_ms": 100, "is_rest": True}], include_rests=True
        )
        assert tl2.active_at(50) == ("r",)

    def test_compact_keys_supported(self) -> None:
        """sync.json 用紧凑键（s/d），Timeline 必须能吃。"""
        tl = Timeline([{"id": "a", "s": 0, "d": 300}])
        assert tl.active_at(100) == ("a",)

    def test_unsorted_input_is_sorted(self) -> None:
        tl = Timeline(
            [
                {"id": "z", "onset_ms": 900, "dur_ms": 100},
                {"id": "a", "onset_ms": 0, "dur_ms": 100},
                {"id": "m", "onset_ms": 450, "dur_ms": 100},
            ]
        )
        assert [e[2] for e in tl.entries] == ["a", "m", "z"]
        assert tl.active_at(0) == ("a",)
        assert tl.active_at(450) == ("m",)
        assert tl.active_at(900) == ("z",)

    def test_overlapping_notes_across_width(self) -> None:
        """一个长音符下面叠着多个短音符（钢琴常见）。"""
        tl = Timeline(
            [
                {"id": "long", "onset_ms": 0, "dur_ms": 3000},
                {"id": "s1", "onset_ms": 0, "dur_ms": 500},
                {"id": "s2", "onset_ms": 500, "dur_ms": 500},
                {"id": "s3", "onset_ms": 1000, "dur_ms": 500},
            ]
        )
        assert tl.active_at(0) == ("long", "s1")
        assert tl.active_at(700) == ("long", "s2")
        assert tl.active_at(1200) == ("long", "s3")
        assert tl.active_at(2500) == ("long",)

    def test_from_sync_like_object(self) -> None:
        class FakeSync:
            notes = [{"id": "a", "onset_ms": 0, "dur_ms": 100}]
            measures: list = []
            duration_ms = 100

        tl = Timeline.from_sync(FakeSync())  # type: ignore[arg-type]
        assert tl.active_at(50) == ("a",)


class TestHighlightTracker:
    def test_first_frame_highlights(self) -> None:
        tr = HighlightTracker(make_timeline())
        diff = tr.update(0)
        assert diff.added == ("a",)
        assert diff.removed == ()
        assert diff.current == ("a",)
        assert tr.cursor_id == "a"

    def test_incremental_diff_removes_previous(self) -> None:
        tr = HighlightTracker(make_timeline())
        tr.update(0)
        diff = tr.update(350)
        assert diff.added == ("b",)
        assert diff.removed == ("a",)

    def test_no_change_returns_same_state(self) -> None:
        tr = HighlightTracker(make_timeline())
        tr.update(0)
        diff = tr.update(100)
        assert not diff.changed
        assert diff.current == ("a",)

    def test_same_frame_is_skipped(self) -> None:
        """同一毫秒重复调用应直接返回，不做多余计算（30 fps 下的常见情形）。"""
        tr = HighlightTracker(make_timeline())
        tr.update(1000)
        before = tr.active
        diff = tr.update(1000)
        assert diff.added == () and diff.removed == ()
        assert tr.active == before

    def test_reset(self) -> None:
        tr = HighlightTracker(make_timeline())
        tr.update(0)
        tr.reset()
        assert tr.active == ()
        assert tr.cursor_id == ""
        assert tr.update(0).added == ("a",)

    @pytest.mark.parametrize("rate", [0.5, 1.0, 1.5, 2.0])
    def test_rate_scaling(self, rate: float) -> None:
        """速率改变时，高亮必须跟着变（F2.5 的核心）：查询时间 = position × rate。"""
        tr = HighlightTracker(make_timeline(), rate=rate)
        # 播放在 300/rate 毫秒时，乐谱时间正好是 300ms → 应高亮 b
        diff = tr.update_at(tr.query_time(300 / rate))
        assert diff.current == ("b",)

    def test_rate_round_trip(self) -> None:
        tr = HighlightTracker(make_timeline(), rate=1.5, offset_ms=40.0)
        for score_ms in (0, 250, 900, 1150):
            assert tr.query_time(tr.player_time(score_ms)) == pytest.approx(score_ms)

    @pytest.mark.parametrize("offset", [-500, -100, 0, 100, 500])
    def test_offset_calibration(self, offset: float) -> None:
        """F2.8：offset 用于补偿音频设备缓冲延迟。"""
        tr = HighlightTracker(make_timeline(), offset_ms=offset)
        t = tr.query_time(1000)  # 乐谱时间 = 1000 + offset
        assert t == 1000 + offset

    def test_offset_shifts_highlight(self) -> None:
        # 音频比谱面晚 100ms → 播放器在 200ms 时，谱面实际在 300ms（b）
        tr = HighlightTracker(make_timeline(), offset_ms=100.0)
        assert tr.update(200).current == ("b",)

    def test_effective_bpm(self) -> None:
        tr = HighlightTracker(make_timeline(), rate=0.5)
        assert tr.effective_bpm(100) == 50
        assert tr.effective_bpm(None) is None
        assert tr.effective_bpm(0) is None

    def test_chord_highlights_all_members(self) -> None:
        tr = HighlightTracker(make_timeline())
        diff = tr.update(950)
        assert set(diff.added) == {"d", "e"}

    def test_seek_backwards_recomputes(self) -> None:
        """拖动进度条回到前面时，必须能重新点亮（force 语义）。"""
        tr = HighlightTracker(make_timeline())
        tr.update(950)
        diff = tr.update_at(0, force=True)
        assert diff.current == ("a",)
        assert "a" in diff.added
