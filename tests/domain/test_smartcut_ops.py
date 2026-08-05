"""Host tests for domain.smartcut_ops — the pure half of Smart Cut.

Focuses on _build_v3_timeline, which had ZERO coverage before the smartcut
decomposition: it maps keep-segments (seconds) onto an auto-editor v3 JSON
timeline (frames), and a rounding/offset bug there would silently produce a
mis-synced or empty render. The transcript/drop-range logic is exercised by
test_smartcut_manual_trim.py (which imports the same names via smartcut).
"""
import json

import pytest

from clippyme.domain import smartcut_ops as ops


_PROBE = {"fps_num": 30, "fps_den": 1, "width": 1080, "height": 1920, "samplerate": 48000}


# --- build_crossfade_graph -----------------------------------------------------

def test_crossfade_graph_two_segments():
    graph, v_out, a_out, total = ops.build_crossfade_graph([4.0, 3.0], 0.15)
    assert v_out == "vfinal" and a_out == "afinal"
    assert abs(total - 6.85) < 1e-6  # 7.0s minus one 0.15s overlap
    assert "[0:v][1:v]xfade=transition=fade:duration=0.1500:offset=3.8500[vout]" in graph
    assert "[0:a][1:a]acrossfade=d=0.1500:c1=tri:c2=tri[aout]" in graph
    assert "[aout]afade=t=in:st=0:d=0.03" in graph
    assert "[vout]format=yuv420p[vfinal]" in graph


def test_crossfade_graph_offsets_accumulate():
    # Three segments: join 1 at 4.0-0.15, join 2 at (4+3-0.15)-0.15 = 6.7.
    graph, _, _, total = ops.build_crossfade_graph([4.0, 3.0, 2.0], 0.15)
    assert abs(total - (9.0 - 2 * 0.15)) < 1e-6
    assert "offset=3.8500" in graph
    assert "offset=6.7000" in graph
    assert "vx1" in graph and "vx2" not in graph  # last join outputs to vout


def test_crossfade_graph_clamps_fade_to_shortest_segment():
    # A 0.5s request against a 0.2s segment clamps to 0.4 * 0.2 = 0.08s.
    graph, _, _, total = ops.build_crossfade_graph([1.0, 0.2], 0.5)
    assert "duration=0.0800" in graph
    assert abs(total - (1.2 - 0.08)) < 1e-6


def test_crossfade_graph_skips_tail_fade_when_total_tiny():
    # total <= 0.06 → no tail fade (would have a negative start timestamp).
    graph, _, a_out, _ = ops.build_crossfade_graph([0.02, 0.02], 0.02)
    assert a_out == "aout"
    assert "afade=t=out" not in graph


def test_crossfade_graph_rejects_invalid_input():
    with pytest.raises(ValueError):
        ops.build_crossfade_graph([1.0], 0.15)  # one segment
    with pytest.raises(ValueError):
        ops.build_crossfade_graph([0.0, 1.0], 0.15)  # zero-length segment
    with pytest.raises(ValueError):
        ops.build_crossfade_graph(["x", 1.0], 0.15)  # non-numeric
    with pytest.raises(ValueError):
        ops.build_crossfade_graph([1.0, 1.0], 0.0)  # no fade


# --- _build_v3_timeline -------------------------------------------------------

def test_v3_timeline_top_level_shape():
    tl = ops._build_v3_timeline("/clips/a.mp4", [(0.0, 1.0)], _PROBE)
    assert tl["version"] == "3"
    assert tl["timebase"] == "30/1"
    assert tl["resolution"] == [1080, 1920]
    assert tl["samplerate"] == 48000
    # v/a are each a single track (list of one clip-list).
    assert len(tl["v"]) == 1 and len(tl["a"]) == 1
    # Must be JSON-serialisable (it's written to a temp file for auto-editor).
    json.loads(json.dumps(tl))


def test_v3_timeline_frame_conversion_and_offsets():
    # 30fps: [0.5s, 1.5s) → offset 15 frames, dur 30 frames, output starts at 0.
    tl = ops._build_v3_timeline("/clips/a.mp4", [(0.5, 1.5)], _PROBE)
    clip = tl["v"][0][0]
    assert clip["offset"] == 15          # 0.5 * 30, source-relative
    assert clip["dur"] == 30             # 1.0s * 30
    assert clip["start"] == 0            # first clip lands at output frame 0
    assert clip["name"] == "video"
    assert clip["src"].endswith("a.mp4") and clip["src"].startswith("/")


def test_v3_timeline_output_positions_are_cumulative():
    # Two kept spans → the second clip's output `start` = first clip's dur,
    # while each `offset` stays source-relative (the bug this guards against is
    # output frames drifting or overlapping).
    tl = ops._build_v3_timeline("/clips/a.mp4", [(0.0, 1.0), (2.0, 2.5)], _PROBE)
    v = tl["v"][0]
    assert [c["start"] for c in v] == [0, 30]     # 0, then after a 30-frame clip
    assert [c["dur"] for c in v] == [30, 15]
    assert [c["offset"] for c in v] == [0, 60]    # 0s and 2.0s at 30fps
    # Audio track mirrors video frame-for-frame, only the name differs.
    a = tl["a"][0]
    assert [c["start"] for c in a] == [0, 30]
    assert all(c["name"] == "audio" for c in a)


def test_v3_timeline_drops_subframe_segments():
    # A span shorter than one frame (<1/30s here) rounds to 0 duration and must
    # be skipped, not emitted as a zero-length clip that auto-editor rejects.
    tl = ops._build_v3_timeline("/clips/a.mp4", [(0.0, 0.01), (0.0, 1.0)], _PROBE)
    assert len(tl["v"][0]) == 1
    assert tl["v"][0][0]["dur"] == 30


def test_v3_timeline_empty_segments_yield_empty_tracks():
    # The renderer keys on `timeline["v"][0]` being falsy to bail out before
    # spawning auto-editor — so no segments must give an empty track list.
    tl = ops._build_v3_timeline("/clips/a.mp4", [], _PROBE)
    assert tl["v"] == [[]]
    assert tl["a"] == [[]]


def test_v3_timeline_respects_fractional_fps():
    # 30000/1001 (29.97) timebase must be carried verbatim, and frame counts
    # computed from the derived float fps.
    probe = {**_PROBE, "fps_num": 30000, "fps_den": 1001}
    tl = ops._build_v3_timeline("/clips/a.mp4", [(0.0, 1.0)], probe)
    assert tl["timebase"] == "30000/1001"
    assert tl["v"][0][0]["dur"] == 30   # round(1.0 * 29.97)
