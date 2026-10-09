"""Top-edge text banner — burns a full-width operator text strip onto a clip.

Renders the operator's meeting/series title (e.g. "St. Johns BOCC Public
comments - SB 686") as one or two centred lines on a translucent strip running
the full width of the video's top edge, present for the clip's whole duration.

Mirrors domain/banner.py: a Pillow-rendered RGBA PNG is composited with a
single ffmpeg overlay pass. The geometry mapping (strip height / font pixels /
padding from a size preset + video width) is pure and host-unit-testable via
``top_text_plan``; the actual pixel rendering and ffmpeg call sit behind it.
Fonts resolve through the same allow-listed bundled/user font directories the
hook + subtitle layers use.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Font-size presets → text cap height as a fraction of the video width. The
# default M matches the hook's base font scale so a title strip reads as title
# chrome, not speech.
_SIZE_FRACTIONS = {"S": 0.035, "M": 0.045, "L": 0.055}

_DEFAULT_SIZE = "M"
_DEFAULT_TEXT_COLOR = "#FFFFFF"
_DEFAULT_BG_ENABLED = True
_DEFAULT_BG_COLOR = "#000000"
_DEFAULT_BG_OPACITY = 0.55

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_MAX_LINES = 2


def clamp_hex(value, default: str) -> str:
    """Normalize a ``#RRGGBB`` hex colour, falling back to ``default``."""
    if isinstance(value, str) and _HEX_RE.match(value.strip()):
        return value.strip().upper()
    return default


def clamp_opacity(value, default: float = _DEFAULT_BG_OPACITY) -> float:
    """Clamp a strip opacity to [0, 1]."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def top_text_plan(width: int, size: str | None = None,
                  opacity: float | None = None) -> dict:
    """Pure geometry for the top-text strip (host-testable, no Pillow/ffmpeg).

    Returns ``{width, strip_height, font_px, pad_y}`` where ``strip_height`` is
    the full-width canvas height for a strip holding up to two lines, centred
    in ``width``. Sizes scale to the video width; every value is clamped into a
    safe range so a pathological width can never build an absurd canvas.
    """
    width = max(64, min(int(width), 8192))
    fraction = _SIZE_FRACTIONS.get(str(size or "").upper(), _SIZE_FRACTIONS[_DEFAULT_SIZE])
    font_px = max(8, min(512, int(width * fraction)))
    line_px = max(8, int(font_px * 1.2))
    pad_y = max(6, int(font_px * 0.45))
    two_line_h = line_px * _MAX_LINES + pad_y * 2
    one_line_h = line_px + pad_y * 2
    # Always size for the full two-line strip so the layout is stable whether
    # the text wraps or not (avoids a strip that grows between clips of the
    # same job when a title happens to wrap on one clip only).
    strip_height = two_line_h
    opacity = clamp_opacity(opacity)
    return {
        "width": width,
        "strip_height": strip_height,
        "one_line_h": one_line_h,
        "line_px": line_px,
        "font_px": font_px,
        "pad_y": pad_y,
        "opacity": opacity,
    }


def _split_overlong_word(draw, word, font, max_width, stroke_width):
    """Split a no-whitespace token so it cannot overflow the strip."""
    from clippyme.domain.hooks import _text_width

    if _text_width(draw, word, font, stroke_width) <= max_width:
        return [word]
    chunks = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_width(draw, candidate, font, stroke_width) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [word]


def _wrap_lines(draw, text: str, font, max_width: int, stroke_width: int) -> list[str]:
    """Wrap ``text`` into at most ``_MAX_LINES`` centred lines (best effort).

    Lines longer than the strip after two passes are truncated with an
    ellipsis rather than overflowing the top edge. The overlong-word splitter
    guarantees no single token exceeds ``max_width``.
    """
    from clippyme.domain.hooks import _text_width

    raw_lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        if not paragraph.strip():
            raw_lines.append("")
            continue
        current_line: list[str] = []
        words: list[str] = []
        for word in paragraph.split():
            words.extend(_split_overlong_word(draw, word, font, max_width, stroke_width))
        for word in words:
            test_line = " ".join(current_line + [word])
            if _text_width(draw, test_line, font, stroke_width) <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    raw_lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            raw_lines.append(" ".join(current_line))

    out: list[str] = []
    for line in raw_lines:
        if len(out) < _MAX_LINES:
            out.append(line)
            continue
        # Over the limit: append to the last line with an ellipsis once.
        if out[-1] and not out[-1].endswith("…"):
            out[-1] = out[-1][: max(1, len(out[-1]) - 1)] + "…"
        else:
            out.append(line[:_MAX_LINES] + "…")  # pragma: no cover — defensive
        break
    return out or [""]


def render_top_text_png(text: str, width: int, out_path: str, params: dict | None = None,
                        style: dict | None = None) -> tuple[str, int, int]:
    """Render the full-width top-text strip PNG (transparent canvas, no bg).

    ``params`` carries ``{size, text_color}``; ``style`` carries
    ``{bg_enabled, bg_color, bg_opacity}`` (kept separate so callers can reuse
    the same strip style across compose/export). Returns
    ``(out_path, box_w, box_h)``.
    """
    from PIL import Image, ImageDraw, ImageFont

    from clippyme.domain.hooks import _resolve_hook_font_path

    params = params or {}
    style = style or {}
    width = max(64, min(int(width), 8192))

    text_color = clamp_hex(params.get("text_color"), _DEFAULT_TEXT_COLOR)
    bg_enabled = bool(style.get("bg_enabled", _DEFAULT_BG_ENABLED))
    bg_color = clamp_hex(style.get("bg_color"), _DEFAULT_BG_COLOR)
    bg_opacity = clamp_opacity(style.get("bg_opacity"))

    font_name = params.get("font")  # optional font override (allow-listed name)
    plan = top_text_plan(width, params.get("size"))
    font_path = _resolve_hook_font_path(font_name)
    try:
        font = ImageFont.truetype(font_path, plan["font_px"])
    except Exception:
        try:
            from clippyme.domain.hooks import FONT_PATH
            font = ImageFont.truetype(FONT_PATH, plan["font_px"])
        except Exception:
            font = ImageFont.load_default()

    stroke_w = max(1, plan["font_px"] // 24)
    canvas_w = plan["width"]
    canvas_h = plan["strip_height"]

    # Measure + wrap in a throwaway 1x1 context, then draw for real.
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_text_width = max(1, canvas_w - 2 * int(canvas_w * 0.03))
    lines = _wrap_lines(dummy, text, font, max_text_width, stroke_w)

    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if bg_enabled:
        bg_rgba = _hex_to_rgba(bg_color, round(bg_opacity * 255))
        # Full-width translucent band behind the text lines.
        draw.rectangle([(0, 0), (canvas_w - 1, canvas_h - 1)], fill=bg_rgba)

    line_heights: list[int] = []
    for line in lines[:_MAX_LINES]:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_w)
        line_heights.append(bbox[3] - bbox[1])
    # Vertically centre the (up to two) text lines within the strip.
    content_h = sum(line_heights) + (len(line_heights) - 1) * max(4, plan["line_px"] // 5)
    y0 = max(plan["pad_y"], (canvas_h - content_h) // 2)
    cursor_y = y0
    for i, line in enumerate(lines[:_MAX_LINES]):
        if not line:
            continue
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_w)
        line_w = bbox[2] - bbox[0]
        x = max(1, (canvas_w - line_w) // 2)
        draw.text(
            (x, cursor_y - bbox[1]), line, font=font, fill=_hex_to_rgba(text_color),
            stroke_width=stroke_w, stroke_fill=(0, 0, 0, 255),
        )
        cursor_y += line_heights[i] + max(4, plan["line_px"] // 5)

    img.save(out_path)
    return out_path, canvas_w, canvas_h


def _hex_to_rgba(hex_str, alpha=255, default=(0, 0, 0)):
    if isinstance(hex_str, str) and _HEX_RE.match(hex_str):
        h = hex_str.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha))
    return (*default, int(alpha))


def add_top_text_to_video(video_path: str, params: dict, out_path: str) -> bool:
    """Overlay a full-width top-text strip onto a clip.

    ``params`` keys: ``text``, ``size`` (S/M/L), ``text_color`` (hex),
    ``bg_enabled`` (bool), ``bg_color`` (hex), ``bg_opacity`` (0..1). The text
    must be non-blank or the call raises ValueError (the compose caller gates
    on blank text first).
    """
    from clippyme.domain.encode import ffmpeg_timeout, x264_video_args
    from clippyme.pipeline.media_probe import probe_dimensions

    params = dict(params or {})
    text = str(params.get("text") or "").strip()
    if not text:
        raise ValueError("top text requires non-blank text")

    width, _height = probe_dimensions(video_path)
    fd, strip_png = tempfile.mkstemp(prefix="clippyme-toptext-", suffix=".png")
    os.close(fd)
    try:
        render_top_text_png(
            text,
            width,
            strip_png,
            params=params,
            style={
                "bg_enabled": params.get("bg_enabled", _DEFAULT_BG_ENABLED),
                "bg_color": params.get("bg_color"),
                "bg_opacity": params.get("bg_opacity"),
            },
        )
        overlay_y = 0
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", strip_png,
            "-filter_complex", f"[0:v][1:v]overlay=0:{overlay_y}",
            "-c:a", "copy",
            *x264_video_args(),
            out_path,
        ]
        subprocess.run(
            ffmpeg_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=ffmpeg_timeout(),
        )
        logger.info("✅ Top text banner added → %s", os.path.basename(out_path))
        return True
    except subprocess.TimeoutExpired:
        logger.error("❌ Top-text ffmpeg timed out after %ss", ffmpeg_timeout())
        raise
    except subprocess.CalledProcessError as exc:
        logger.error(
            "❌ Top-text ffmpeg error: %s",
            exc.stderr.decode() if exc.stderr else "unknown",
        )
        raise
    finally:
        with contextlib.suppress(OSError):
            os.remove(strip_png)
