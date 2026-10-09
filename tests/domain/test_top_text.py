"""Host tests for clippyme.domain.top_text — pure geometry + renderer."""
import os

import pytest

from clippyme.domain import top_text
from clippyme.domain.top_text import (
    _DEFAULT_BG_OPACITY,
    clamp_hex,
    clamp_opacity,
    top_text_plan,
)


def test_plan_size_fractions_scale_with_width():
    for size, expected_fraction in (("S", 0.035), ("M", 0.045), ("L", 0.055)):
        plan = top_text_plan(1080, size)
        assert plan["font_px"] == int(1080 * expected_fraction)
    assert plan["width"] == 1080
    assert plan["strip_height"] > plan["one_line_h"]


def test_plan_default_size_and_bounds():
    plan = top_text_plan(64, None)  # tiny width clamps up
    assert plan["width"] == 64
    assert plan["font_px"] >= 8
    wide = top_text_plan(20000, "L")  # absurd width clamps down
    assert wide["width"] == 8192
    assert wide["font_px"] <= 512


def test_plan_opacity_clamped():
    assert top_text_plan(1080, "M", opacity=2.0)["opacity"] == 1.0
    assert top_text_plan(1080, "M", opacity=-1)["opacity"] == 0.0
    assert top_text_plan(1080, "M", opacity=None)["opacity"] == _DEFAULT_BG_OPACITY
    assert top_text_plan(1080, "M", opacity="garbage")["opacity"] == _DEFAULT_BG_OPACITY


def test_clamp_hex():
    assert clamp_hex("#FFFFFF", "#000000") == "#FFFFFF"
    assert clamp_hex("  #a1b2c3  ", "#000000") == "#A1B2C3"
    assert clamp_hex("not-hex", "#000000") == "#000000"
    assert clamp_hex(None, "#111111") == "#111111"


def test_clamp_opacity():
    assert clamp_opacity(0.5) == 0.5
    assert clamp_opacity(3) == 1.0
    assert clamp_opacity(-1) == 0.0
    assert clamp_opacity(None) == _DEFAULT_BG_OPACITY


def test_render_top_text_png_real(tmp_path):
    """Exercise the real Pillow path (Pillow is a host-tests dep)."""
    out = str(tmp_path / "strip.png")
    path, w, h = top_text.render_top_text_png(
        "St. Johns BOCC Public comments - SB 686", 1080, out,
        params={"size": "M", "text_color": "#FFFFFF"},
        style={"bg_enabled": True, "bg_color": "#000000", "bg_opacity": 0.55},
    )
    assert path == out
    assert os.path.exists(out)
    assert os.path.getsize(out) > 0
    # Full-width canvas of a two-line strip height.
    assert w == 1080
    assert h == top_text.top_text_plan(1080, "M")["strip_height"]


def test_render_without_bg_still_writes_text(monkeypatch, tmp_path):
    """With the background strip off the PNG still renders (bare text)."""
    from PIL import Image

    out = str(tmp_path / "bare.png")
    path, w, h = top_text.render_top_text_png(
        "Single line", 720, out,
        params={"size": "S", "text_color": "#FFE000"},
        style={"bg_enabled": False, "bg_color": "#000000", "bg_opacity": 0.0},
    )
    assert path == out
    img = Image.open(out)
    # Fully transparent outside the drawn text stroke is expected; just assert
    # the strip canvas exists with the right dimensions and non-empty alpha
    # somewhere in the top band.
    assert img.size == (720, top_text.top_text_plan(720, "S")["strip_height"])
    # Some pixel must carry the yellow text (or black stroke) alpha.
    extrema = img.getextrema()
    assert extrema[3][1] > 0  # max alpha channel > 0 → something drawn


def test_add_top_text_requires_text(tmp_path):
    with pytest.raises(ValueError):
        top_text.add_top_text_to_video(
            str(tmp_path / "x.mp4"), {"text": "  "}, str(tmp_path / "o.mp4")
        )


def test_add_top_text_uses_distinct_temp_files(monkeypatch, tmp_path):
    import subprocess
    from clippyme.pipeline import media_probe

    video = tmp_path / "clip_1.mp4"
    video.write_bytes(b"video")
    seen = []
    pngs = []

    monkeypatch.setattr(media_probe, "probe_dimensions", lambda path: (1080, 1920))

    def fake_render(text, width, out_path, params=None, style=None):
        seen.append(out_path)
        pngs.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"png")
        return out_path, width, 200

    monkeypatch.setattr(top_text, "render_top_text_png", fake_render)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

    params = {"text": "BOCC Public comments", "size": "M", "bg_enabled": True}
    assert top_text.add_top_text_to_video(
        str(video), params, str(tmp_path / "out1.mp4")
    )
    assert top_text.add_top_text_to_video(
        str(video), params, str(tmp_path / "out2.mp4")
    )
    assert len(seen) == 2 and seen[0] != seen[1]
    assert all(not os.path.exists(path) for path in pngs)


def test_wrap_lines_respects_two_line_cap():
    from clippyme.domain.top_text import _wrap_lines
    from PIL import Image, ImageDraw, ImageFont
    from clippyme.domain.hooks import FONT_PATH

    font = ImageFont.truetype(FONT_PATH, 40)
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap_lines(
        draw, "one two three four five six seven eight nine ten eleven twelve",
        font, 400, 2,
    )
    assert len(lines) <= 2
