"""
video/src/renderer.py — Frame-by-frame video renderer.

Produces one PNG per video frame into output_dir, which ffmpeg then muxes
with the audio into the final mp4.

Visual layer stack (bottom → top):
  0. Background     — animated radial gradient, slow color drift + amp tint
  1. Glow rings     — blurred soft halos (Gaussian)
  2. Sharp rings    — crisp outlines pulsing with amplitude
  3. Arc bars       — 360° waveform bars radiating outward
  4. Spark particles — rotating dots orbiting the outer ring
  5. Logo           — centre PNG, alpha-pulses with amplitude

All dimensions are derived from min(width, height) so the layout scales
correctly from 720p to 4K without manual adjustment.

Tuning knobs (see DEFAULT_* constants and render_frames() kwargs):
  ring_scale  — overall ring size multiplier
  n_bars      — how many arc bars in the 360° waveform
  bar_height  — maximum bar height as fraction of canvas short-edge
  n_sparks    — number of rotating spark particles
  glow_blur   — Gaussian blur radius on the glow ring layer
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from video.src.palette import (
    BG_CENTER, BG_EDGE,
    RING_INNER, RING_MID, RING_OUTER, RING_BEAT,
    BAR_COLOR, SPARK_COLOR, GLOW_TINT,
    ACCENT_BG,
)

# ── Visual defaults — override via render_frames() kwargs ─────────────────────
DEFAULT_RING_SCALE = 1.0    # ring size multiplier; 0.5 = compact, 2.0 = huge
DEFAULT_N_BARS     = 120    # arc waveform bar count
DEFAULT_BAR_HEIGHT = 0.14   # bar_max as fraction of min(W, H)
DEFAULT_N_SPARKS   = 36     # rotating spark particle count
DEFAULT_GLOW_BLUR  = 4      # Gaussian blur radius (px) on glow layer


# ── Color helpers ─────────────────────────────────────────────────────────────

def _hex_to_rgba(rgb: tuple, alpha: float) -> tuple:
    """Convert (R, G, B) + 0–1 alpha float → (R, G, B, A) for Pillow."""
    r, g, b = rgb
    return (r, g, b, int(255 * max(0.0, min(1.0, alpha))))


def _lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    """Linear interpolation between two RGB tuples at position t (0→1)."""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ── Background ────────────────────────────────────────────────────────────────

def _make_bg(width: int, height: int, center_color: tuple = None) -> Image.Image:
    """
    Build a radial gradient image: center_color at origin → BG_EDGE at corners.
    Uses numpy for speed; called once per keyframe, not once per frame.
    """
    center = center_color if center_color is not None else BG_CENTER
    arr    = np.zeros((height, width, 3), dtype=np.float32)
    cx, cy = width / 2, height / 2
    max_r  = math.hypot(cx, cy)
    ys, xs = np.mgrid[0:height, 0:width]
    # t = 0 at centre, ≈1 at corner
    t = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max_r
    for ch, (a, b) in enumerate(zip(center, BG_EDGE)):
        arr[:, :, ch] = a * (1 - t) + b * t
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


# ── Ring drawing ──────────────────────────────────────────────────────────────

def _draw_ring(draw, cx, cy, radius, thickness, color, alpha):
    """Draw a single circle outline at (cx, cy) with the given colour and alpha."""
    fill = _hex_to_rgba(color, alpha)
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.ellipse(bbox, outline=fill, width=max(1, thickness))


# ── Arc waveform bars ─────────────────────────────────────────────────────────

def _draw_arc_bars(layer, cx, cy, window_amps, ring_r, bar_max, color):
    """
    Draw a circular array of radial bars, one per amplitude sample.

    Each bar starts just outside ring_r and extends outward by bar_max × amp.
    The trailing window (last N frames) creates a smooth wave that rotates
    with the audio — louder sections push bars further out.
    """
    draw = ImageDraw.Draw(layer)
    n    = len(window_amps)
    r, g, b = color
    for i, amp in enumerate(window_amps):
        angle    = (2 * math.pi * i / n) - math.pi / 2   # start at 12 o'clock
        inner_r  = ring_r + 8
        outer_r  = ring_r + 8 + bar_max * amp
        x0 = cx + inner_r * math.cos(angle)
        y0 = cy + inner_r * math.sin(angle)
        x1 = cx + outer_r * math.cos(angle)
        y1 = cy + outer_r * math.sin(angle)
        a  = int(170 + 85 * amp)   # bars get more opaque at higher amplitude
        draw.line([(x0, y0), (x1, y1)], fill=(r, g, b, a), width=2)


# ── Spark particles ───────────────────────────────────────────────────────────

def _draw_sparks(layer, cx, cy, radius, n_sparks, rotation, amp):
    """
    Draw evenly-spaced dot particles on a circle of given radius.

    rotation is the current cumulative angle (see _compute_rotation), so the
    whole ring of particles rotates smoothly over time.  Size and opacity both
    scale with amplitude so sparks flare on loud beats.
    """
    draw    = ImageDraw.Draw(layer)
    r, g, b = SPARK_COLOR
    for i in range(n_sparks):
        angle = rotation + (2 * math.pi * i / n_sparks)
        x     = cx + radius * math.cos(angle)
        y     = cy + radius * math.sin(angle)
        size  = 1.5 + amp * 3.5          # radius in px: 1.5 (quiet) → 5.0 (loud)
        alpha = int(100 + 155 * amp)     # opacity:        100      →   255
        bbox  = [x - size, y - size, x + size, y + size]
        draw.ellipse(bbox, fill=(r, g, b, alpha))


# ── Rotation ──────────────────────────────────────────────────────────────────

def _compute_rotation(amplitudes: list, frame_idx: int) -> float:
    """
    Derive spark rotation from cumulative audio energy up to this frame.

    Louder sections spin faster; silence barely moves.  Cumulative (not
    per-frame) angle guarantees smooth, monotonically increasing rotation —
    no snapping or backward jumps.

    Tune BASE_SPEED and AMP_SPEED to taste:
      BASE_SPEED  — rotation per frame at silence (radians)
      AMP_SPEED   — additional rotation per frame at full amplitude
    """
    BASE_SPEED = 0.004
    AMP_SPEED  = 0.040
    cumulative = sum(BASE_SPEED + AMP_SPEED * a for a in amplitudes[:frame_idx + 1])
    return cumulative % (2 * math.pi)


# ── Full renderer ─────────────────────────────────────────────────────────────

def render_frames(
    amplitude_file: Path,
    output_dir:     Path,
    width:          int,
    height:         int,
    logo_path:      Path = None,
    ring_scale:     float = DEFAULT_RING_SCALE,
    n_bars:         int   = DEFAULT_N_BARS,
    bar_height:     float = DEFAULT_BAR_HEIGHT,
    n_sparks:       int   = DEFAULT_N_SPARKS,
    glow_blur:      int   = DEFAULT_GLOW_BLUR,
):
    """
    Render all video frames to output_dir/frame_NNNNN.png.

    Each frame is composited from 5 RGBA layers (see module docstring).
    Background keyframes are pre-computed to avoid regenerating the numpy
    gradient on every frame — the per-frame cost is then just blending two
    adjacent keyframes.
    """
    with open(amplitude_file) as f:
        amplitudes = json.load(f)

    output_dir.mkdir(parents=True, exist_ok=True)

    cx, cy  = width / 2, height / 2
    min_dim = min(width, height)
    s       = ring_scale

    # ── Geometry — all derived from min_dim so layout scales with resolution ──
    r_logo  = min_dim * 0.14          # logo half-size (intentionally not scaled)
    r_inner = min_dim * 0.18 * s      # innermost pulsing ring
    r_mid   = min_dim * 0.24 * s      # middle ring
    r_outer = min_dim * 0.30 * s      # outer ring / arc-bar base
    bar_max = min_dim * bar_height    # arc bar max height in pixels

    # ── Animated background keyframes ─────────────────────────────────────────
    # Pre-compute N background images at different points in the colour drift
    # cycle.  Per-frame rendering just blends two adjacent keyframes, which is
    # much cheaper than rebuilding the numpy gradient.
    #
    # Colour drifts from BG_CENTER → ACCENT_BG (deep violet) → BG_CENTER over
    # the full video length, giving a slow breathing effect.
    _N_BG_KEYS    = 60   # one keyframe every ~0.5 s at 30 fps
    _bg_keyframes = []
    for k in range(_N_BG_KEYS):
        phase  = math.sin(math.pi * k / (_N_BG_KEYS - 1))   # 0 → 1 → 0
        center = _lerp_color(BG_CENTER, ACCENT_BG, phase * 0.55)
        _bg_keyframes.append(_make_bg(width, height, center))
    print(f"  Precomputed {_N_BG_KEYS} background keyframes")

    # ── Logo preparation ──────────────────────────────────────────────────────
    logo_img = _prepare_logo(logo_path, r_logo) if logo_path else None

    # Rolling amplitude history for beat detection (last N frames)
    history_len = 10
    amp_history = [0.0] * history_len

    for i, amp in enumerate(amplitudes):
        # Maintain rolling average for beat detection
        amp_history.pop(0)
        amp_history.append(amp)
        avg_amp = sum(amp_history) / history_len
        # Beat = this frame's amplitude is 50% above the recent average AND loud
        is_beat = amp > avg_amp * 1.5 and amp > 0.4

        rotation = _compute_rotation(amplitudes, i)

        # ── Background: interpolate between pre-computed keyframes ────────────
        bg_t      = (i / max(len(amplitudes) - 1, 1)) * (_N_BG_KEYS - 1)
        bg_lo     = _bg_keyframes[int(bg_t)]
        bg_hi     = _bg_keyframes[min(int(bg_t) + 1, _N_BG_KEYS - 1)]
        bg_frame  = Image.blend(bg_lo, bg_hi, bg_t - int(bg_t))

        # Amplitude tint: very subtle white brightening of the centre on loud frames
        if amp > 0.05:
            tint_alpha = int(amp * 18)    # max ≈ 7% opacity at full amplitude
            tint  = Image.new("RGB", (width, height), (0,0,0))
            mask  = Image.new("L",   (width, height), 0)
            md    = ImageDraw.Draw(mask)
            tr    = int(min_dim * 0.45)
            md.ellipse([cx - tr, cy - tr, cx + tr, cy + tr], fill=tint_alpha)
            mask  = mask.filter(ImageFilter.GaussianBlur(radius=min_dim * 0.15))
            bg_frame = Image.composite(tint, bg_frame, mask)

        frame = bg_frame.convert("RGBA")

        # ── Pulsing ring radii — grow outward with amplitude ──────────────────
        pulse_inner = r_inner + (r_mid   - r_inner)  * 0.35 * amp
        pulse_mid   = r_mid   + (r_outer - r_mid)    * 0.25 * amp
        pulse_outer = r_outer + min_dim * 0.04 * s   * amp
        ring_alpha  = 0.55 + 0.45 * amp
        beat_boost  = 0.40 if is_beat else 0.0
        hot_color   = RING_BEAT if is_beat else RING_MID

        # ── Layer 1: blurred glow rings ───────────────────────────────────────
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(glow)
        _draw_ring(gd, cx, cy, pulse_inner, 22, GLOW_TINT,  (ring_alpha + beat_boost) * 0.30)
        _draw_ring(gd, cx, cy, pulse_inner,  8, RING_INNER,  ring_alpha + beat_boost)
        _draw_ring(gd, cx, cy, pulse_mid,   16, hot_color,   ring_alpha * 0.18)
        _draw_ring(gd, cx, cy, pulse_mid,    5, hot_color,   ring_alpha * 0.88)
        _draw_ring(gd, cx, cy, pulse_outer,  3, RING_OUTER,  ring_alpha * 0.55)
        frame = Image.alpha_composite(frame, glow.filter(ImageFilter.GaussianBlur(glow_blur)))

        # ── Layer 2: sharp ring outlines ──────────────────────────────────────
        rings = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        rd    = ImageDraw.Draw(rings)
        _draw_ring(rd, cx, cy, pulse_inner, 2, RING_INNER, min(1.0, ring_alpha + beat_boost))
        _draw_ring(rd, cx, cy, pulse_mid,   2, hot_color,  min(1.0, ring_alpha * 0.92))
        _draw_ring(rd, cx, cy, pulse_outer, 1, RING_OUTER, ring_alpha * 0.65)
        frame = Image.alpha_composite(frame, rings)

        # ── Layer 3: arc waveform bars ────────────────────────────────────────
        # Trailing window: pull the last n_bars amplitude values (wrapping at
        # file boundaries) so the bar display looks like a rotating waveform
        # rather than a frozen snapshot.
        n           = len(amplitudes)
        window_amps = [amplitudes[(i - n_bars + 1 + j) % n] for j in range(n_bars)]
        bars        = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _draw_arc_bars(bars, cx, cy, window_amps, pulse_outer, bar_max, BAR_COLOR)
        frame = Image.alpha_composite(frame, bars)

        # ── Layer 4: spark particles ──────────────────────────────────────────
        # Orbit radius grows slightly beyond the bar tips on loud frames
        spark_r = pulse_outer + bar_max * amp * 0.5 + 8
        sparks  = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _draw_sparks(sparks, cx, cy, spark_r, n_sparks, rotation, amp)
        frame = Image.alpha_composite(frame, sparks)

        # ── Layer 5: logo ─────────────────────────────────────────────────────
        if logo_img:
            logo_alpha = int(195 + 60 * amp)   # pulses subtly 195 → 255
            lo         = logo_img.copy()
            r2, g2, b2, a2 = lo.split()
            a2 = a2.point(lambda p: int(p * logo_alpha / 255))
            lo = Image.merge("RGBA", (r2, g2, b2, a2))
            lw, lh = lo.size
            frame.paste(lo, (int(cx - lw / 2), int(cy - lh / 2)), lo)

        frame.convert("RGB").save(output_dir / f"frame_{i:05d}.png", optimize=False)

        if i % 50 == 0:
            print(f"  frame {i}/{len(amplitudes)}")

    print(f"  Done — {len(amplitudes)} frames written to {output_dir}")


def _prepare_logo(logo_path: Path, r_logo: float) -> Image.Image | None:
    """
    Load the logo PNG, invert it if it's designed for a light background
    (dark pixels on transparent), and resize to fit the ring centre.

    Dark-background detection: if the median luminance of opaque pixels is
    below 64, the logo is assumed to be dark-on-clear and gets inverted so
    it reads against the dark video canvas.
    """
    if not logo_path or not Path(logo_path).exists():
        return None

    raw  = Image.open(logo_path).convert("RGBA")
    arr  = np.array(raw)
    opq  = arr[:, :, 3] > 10            # mask of non-transparent pixels

    if opq.any():
        lum = 0.299 * arr[opq, 0] + 0.587 * arr[opq, 1] + 0.114 * arr[opq, 2]
        if lum.mean() < 64:
            # Dark logo on transparent background → invert RGB, keep alpha
            rgb = arr[:, :, :3].astype(np.int16)
            rgb[opq] = 255 - rgb[opq]
            arr[:, :, :3] = rgb.clip(0, 255).astype(np.uint8)
            raw = Image.fromarray(arr, "RGBA")

    logo_size = int(r_logo * 2)
    return raw.resize((logo_size, logo_size), Image.LANCZOS)


# ── Quick renderer ────────────────────────────────────────────────────────────

def render_frames_quick(
    amplitude_file:   Path,
    output_dir:       Path,
    width:            int,
    height:           int,
    background_image: Path = None,
):
    """
    Simplified renderer: gradient orb + optional background image.

    Much faster than the full renderer — good for drafts, CI previews, or
    low-powered machines.  Lacks rings, bars, sparks, and logo.

    Easing: the orb radius uses a critically-damped 20% lerp each frame so
    it responds smoothly without oscillating past the target.
    """
    with open(amplitude_file) as f:
        amplitudes = json.load(f)

    output_dir.mkdir(parents=True, exist_ok=True)

    cx, cy       = width // 2, height // 2
    base_radius  = int(min(width, height) * 0.12)
    max_radius   = int(min(width, height) * 0.35)

    # Prepare optional background image (aspect-correct fit)
    bg = None
    if background_image and background_image.exists():
        bg = Image.open(background_image).convert("RGB")
        bg_ratio     = bg.width / bg.height
        canvas_ratio = width / height
        if bg_ratio > canvas_ratio:
            new_h = height;          new_w = int(bg_ratio * new_h)
        else:
            new_w = width;           new_h = int(new_w / bg_ratio)
        bg = bg.resize((new_w, new_h), Image.LANCZOS)

    prev_radius = base_radius

    for i, amp in enumerate(amplitudes):
        target_radius = base_radius + (max_radius - base_radius) * amp
        # Critically-damped easing: move 20% of the remaining gap each frame
        radius      = prev_radius + (target_radius - prev_radius) * 0.2
        prev_radius = radius
        radius      = int(radius)

        # Base layer: background image or black
        if bg:
            frame = Image.new("RGB", (width, height))
            frame.paste(bg, ((width - bg.width) // 2, (height - bg.height) // 2))
        else:
            frame = Image.new("RGB", (width, height), "black")

        # Gradient orb: layered concentric ellipses, bright centre → transparent edge
        orb      = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        orb_draw = ImageDraw.Draw(orb)
        steps    = 8
        for step in range(steps, 0, -1):
            r     = int(radius * step / steps)
            alpha = int(255 * (step / steps) ** 2)   # quadratic falloff
            bbox  = [cx - r, cy - r, cx + r, cy + r]
            orb_draw.ellipse(bbox, fill=(255, 255, 255, alpha))

        # Soft glow layer behind the orb
        glow  = orb.filter(ImageFilter.GaussianBlur(radius=radius * 0.25))
        frame = Image.alpha_composite(frame.convert("RGBA"), glow)
        frame = Image.alpha_composite(frame, orb)

        frame.convert("RGB").save(output_dir / f"frame_{i:05d}.png")
