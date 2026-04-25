# src/renderer.py

from pathlib import Path
import json
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from palette import (
    BG_CENTER, BG_EDGE,
    RING_INNER, RING_MID, RING_OUTER, RING_BEAT,
    BAR_COLOR, SPARK_COLOR, GLOW_TINT,
)

# ── Defaults (all overridable via render_frames kwargs) ───────────────────────
DEFAULT_RING_SCALE   = 1.0    # multiplier on all ring radii (0.5–2.0)
DEFAULT_N_BARS       = 120    # number of arc waveform bars
DEFAULT_BAR_HEIGHT   = 0.14  # bar_max as fraction of min(width, height)  ← was 0.08
DEFAULT_N_SPARKS     = 36    # number of rotating spark particles          ← was 24
DEFAULT_GLOW_BLUR    = 4     # Gaussian blur radius on glow rings


def _hex_to_rgba(rgb: tuple, alpha: float) -> tuple:
    r, g, b = rgb
    return (r, g, b, int(255 * max(0.0, min(1.0, alpha))))


def _make_bg(width: int, height: int) -> Image.Image:
    """Radial gradient: BG_CENTER at origin → BG_EDGE at corners."""
    arr = np.zeros((height, width, 3), dtype=np.float32)
    cx, cy = width / 2, height / 2
    max_r = math.hypot(cx, cy)
    ys, xs = np.mgrid[0:height, 0:width]
    t = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max_r   # 0 (center) → ~1 (corner)
    for ch, (a, b) in enumerate(zip(BG_CENTER, BG_EDGE)):
        arr[:, :, ch] = a * (1 - t) + b * t
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


def _draw_ring(draw, cx, cy, radius, thickness, color, alpha):
    fill = _hex_to_rgba(color, alpha)
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.ellipse(bbox, outline=fill, width=max(1, thickness))


def _draw_arc_bars(layer, cx, cy, window_amps, ring_r, bar_max, color):
    draw = ImageDraw.Draw(layer)
    n = len(window_amps)
    r, g, b = color
    for i, amp in enumerate(window_amps):
        angle = (2 * math.pi * i / n) - math.pi / 2
        inner_r = ring_r + 8
        outer_r = ring_r + 8 + bar_max * amp
        x0 = cx + inner_r * math.cos(angle)
        y0 = cy + inner_r * math.sin(angle)
        x1 = cx + outer_r * math.cos(angle)
        y1 = cy + outer_r * math.sin(angle)
        a = int(170 + 85 * amp)
        draw.line([(x0, y0), (x1, y1)], fill=(r, g, b, a), width=2)


def _draw_sparks(layer, cx, cy, radius, n_sparks, rotation, amp):
    draw = ImageDraw.Draw(layer)
    r, g, b = SPARK_COLOR
    for i in range(n_sparks):
        angle = rotation + (2 * math.pi * i / n_sparks)
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        size = 1.5 + amp * 3.5
        alpha = int(100 + 155 * amp)
        bbox = [x - size, y - size, x + size, y + size]
        draw.ellipse(bbox, fill=(r, g, b, alpha))


def _compute_rotation(amplitudes: list, frame_idx: int) -> float:
    """
    Drive rotation from the *cumulative energy* of the audio up to this frame.
    Loud sections spin faster; quiet sections barely move.
    Result is in radians, always increasing → smooth, never snapping back.
    """
    BASE_SPEED  = 0.004   # radians/frame at silence
    AMP_SPEED   = 0.040   # additional radians/frame at full amplitude
    cumulative  = sum(BASE_SPEED + AMP_SPEED * a for a in amplitudes[:frame_idx + 1])
    return cumulative % (2 * math.pi)


def render_frames(
    amplitude_file: Path,
    output_dir: Path,
    width: int,
    height: int,
    logo_path: Path = None,
    # ── visual knobs ──────────────────────────────────────────────
    ring_scale:   float = DEFAULT_RING_SCALE,
    n_bars:       int   = DEFAULT_N_BARS,
    bar_height:   float = DEFAULT_BAR_HEIGHT,
    n_sparks:     int   = DEFAULT_N_SPARKS,
    glow_blur:    int   = DEFAULT_GLOW_BLUR,
):
    with open(amplitude_file) as f:
        amplitudes = json.load(f)

    output_dir.mkdir(parents=True, exist_ok=True)

    cx, cy  = width / 2, height / 2
    min_dim = min(width, height)
    s       = ring_scale

    # Ring radii — scaled, but logo size is intentionally NOT scaled
    r_logo   = min_dim * 0.14          # logo half-size (fixed, feels right)
    r_inner  = min_dim * 0.18 * s      # first pulsing ring
    r_mid    = min_dim * 0.24 * s      # second ring
    r_outer  = min_dim * 0.30 * s      # third ring / arc-bar base
    bar_max  = min_dim * bar_height    # arc bar height in pixels

    bg_base = _make_bg(width, height)

    logo_img = None
    if logo_path and Path(logo_path).exists():
        raw = Image.open(logo_path).convert("RGBA")

        # If the logo has dark/black pixels on a transparent background
        # (designed for light backgrounds), invert the RGB so it reads on
        # the dark video canvas. Detection: median luminance of opaque pixels < 64.
        import numpy as np
        arr = np.array(raw)
        opaque = arr[:, :, 3] > 10
        if opaque.any():
            lum = 0.299 * arr[opaque, 0] + 0.587 * arr[opaque, 1] + 0.114 * arr[opaque, 2]
            if lum.mean() < 64:
                # Invert RGB, preserve alpha
                rgb = arr[:, :, :3].astype(np.int16)
                rgb[opaque] = 255 - rgb[opaque]
                arr[:, :, :3] = rgb.clip(0, 255).astype(np.uint8)
                raw = Image.fromarray(arr, "RGBA")

        logo_size = int(r_logo * 2)
        logo_img  = raw.resize((logo_size, logo_size), Image.LANCZOS)

    history_len = 10
    amp_history = [0.0] * history_len

    for i, amp in enumerate(amplitudes):
        amp_history.pop(0)
        amp_history.append(amp)
        avg_amp  = sum(amp_history) / history_len
        is_beat  = amp > avg_amp * 1.5 and amp > 0.4

        # ── Rotation driven by cumulative audio energy ────────────────────
        rotation = _compute_rotation(amplitudes, i)

        frame = bg_base.copy().convert("RGBA")

        pulse_inner = r_inner + (r_mid - r_inner)   * 0.35 * amp
        pulse_mid   = r_mid   + (r_outer - r_mid)   * 0.25 * amp
        pulse_outer = r_outer + min_dim * 0.04 * s  * amp
        ring_alpha  = 0.55 + 0.45 * amp
        beat_boost  = 0.40 if is_beat else 0.0
        hot_color   = RING_BEAT if is_beat else RING_MID

        # ── Layer 1: blurred glow rings ───────────────────────────────────
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(glow)
        # Wide soft halo (thick, low alpha)
        _draw_ring(gd, cx, cy, pulse_inner, 22, GLOW_TINT,  (ring_alpha + beat_boost) * 0.30)
        _draw_ring(gd, cx, cy, pulse_inner,  8, RING_INNER,  ring_alpha + beat_boost)
        _draw_ring(gd, cx, cy, pulse_mid,   16, hot_color,   ring_alpha * 0.18)
        _draw_ring(gd, cx, cy, pulse_mid,    5, hot_color,   ring_alpha * 0.88)
        _draw_ring(gd, cx, cy, pulse_outer,  3, RING_OUTER,  ring_alpha * 0.55)
        frame = Image.alpha_composite(frame, glow.filter(ImageFilter.GaussianBlur(glow_blur)))

        # ── Layer 2: sharp rings ──────────────────────────────────────────
        rings = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        rd    = ImageDraw.Draw(rings)
        _draw_ring(rd, cx, cy, pulse_inner, 2, RING_INNER, min(1.0, ring_alpha + beat_boost))
        _draw_ring(rd, cx, cy, pulse_mid,   2, hot_color,  min(1.0, ring_alpha * 0.92))
        _draw_ring(rd, cx, cy, pulse_outer, 1, RING_OUTER, ring_alpha * 0.65)
        frame = Image.alpha_composite(frame, rings)

        # ── Layer 3: arc waveform bars ────────────────────────────────────
        # Trailing window: last n_bars frames up to now, wrapping at boundaries.
        # Avoids clamping artifact where bars vanish at start/end of file.
        n = len(amplitudes)
        window_amps = [amplitudes[(i - n_bars + 1 + j) % n] for j in range(n_bars)]
        bars = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _draw_arc_bars(bars, cx, cy, window_amps, pulse_outer, bar_max, BAR_COLOR)
        frame = Image.alpha_composite(frame, bars)

        # ── Layer 4: spark particles ──────────────────────────────────────
        spark_r     = pulse_outer + bar_max * amp * 0.5 + 8
        sparks      = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _draw_sparks(sparks, cx, cy, spark_r, n_sparks, rotation, amp)
        frame = Image.alpha_composite(frame, sparks)

        # ── Layer 5: logo (fixed size, alpha-pulses with amplitude) ───────
        if logo_img:
            logo_alpha = int(195 + 60 * amp)
            lo         = logo_img.copy()
            r2, g2, b2, a2 = lo.split()
            a2 = a2.point(lambda p: int(p * logo_alpha / 255))
            lo = Image.merge("RGBA", (r2, g2, b2, a2))
            lw, lh = lo.size
            frame.paste(lo, (int(cx - lw / 2), int(cy - lh / 2)), lo)

        frame.convert("RGB").save(output_dir / f"frame_{i:05d}.png", optimize=False)

        if i % 50 == 0:
            print(f"  frame {i}/{len(amplitudes)}")

    print(f"  Done — {len(amplitudes)} frames → {output_dir}")

def render_frames_quick(
    amplitude_file: Path,
    output_dir: Path,
    width: int,
    height: int,
    background_image: Path = None
):
    """
    Render frames with:
    - eased motion
    - gradient orb
    - glow
    - optional background (aspect-correct)
    """

    with open(amplitude_file) as f:
        amplitudes = json.load(f)

    output_dir.mkdir(parents=True, exist_ok=True)

    center = (width // 2, height // 2)

    base_radius = int(min(width, height) * 0.12)
    max_radius = int(min(width, height) * 0.35)

    # --- Load background if provided ---
    bg = None
    if background_image and background_image.exists():
        bg = Image.open(background_image).convert("RGB")

        # Fit while preserving aspect ratio
        bg_ratio = bg.width / bg.height
        canvas_ratio = width / height

        if bg_ratio > canvas_ratio:
            # wider → fit height
            new_height = height
            new_width = int(bg_ratio * new_height)
        else:
            # taller → fit width
            new_width = width
            new_height = int(new_width / bg_ratio)

        bg = bg.resize((new_width, new_height), Image.LANCZOS)

    prev_radius = base_radius

    for i, amp in enumerate(amplitudes):

        # --- Map amplitude to radius ---
        target_radius = base_radius + (max_radius - base_radius) * amp

        # --- Easing (critically damped feel) ---
        radius = prev_radius + (target_radius - prev_radius) * 0.2
        prev_radius = radius
        radius = int(radius)

        # --- Base image ---
        if bg:
            frame = Image.new("RGB", (width, height))
            x = (width - bg.width) // 2
            y = (height - bg.height) // 2
            frame.paste(bg, (x, y))
        else:
            frame = Image.new("RGB", (width, height), "black")

        draw = ImageDraw.Draw(frame)

        # --- Gradient orb ---
        orb = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        orb_draw = ImageDraw.Draw(orb)

        steps = 8
        for s in range(steps, 0, -1):
            r = int(radius * s / steps)

            alpha = int(255 * (s / steps) ** 2)

            bbox = [
                center[0] - r,
                center[1] - r,
                center[0] + r,
                center[1] + r,
            ]

            orb_draw.ellipse(bbox, fill=(255, 255, 255, alpha))

        # --- Glow layer ---
        glow = orb.filter(ImageFilter.GaussianBlur(radius=radius * 0.25))

        # --- Composite ---
        frame = Image.alpha_composite(frame.convert("RGBA"), glow)
        frame = Image.alpha_composite(frame, orb)

        # --- Save ---
        frame = frame.convert("RGB")
        frame_path = output_dir / f"frame_{i:05d}.png"
        frame.save(frame_path)