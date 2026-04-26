# video/src/renderer_v2.py
#
# Visual design:
#   - Freeform drifting particles — scattered across the full canvas, each with
#     independent velocity, size, lifetime, and opacity. Emission rate scales
#     with amplitude so loud sections fill with life.
#   - Bottom-anchored waveform — bars rise from the bottom edge, 180 bars across
#     80% of canvas width, with rounded caps and a faint reflection.
#   - Animated radial background — drifts between BG_CENTER and ACCENT_BG over
#     the video length, with a subtle amplitude-driven brightness on loud frames.
#   - Logo centered, auto-inverted for readability on the chosen background mode.
#   - Watermark bottom-right, fades in over first 2s, fades out over last 2s.
#   - All colors driven by a Palette object — theme and mode are CLI flags.

from pathlib import Path
import json
import math
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from video.src.palette import load_theme

# # ── Defaults ──────────────────────────────────────────────────────────────────
# DEFAULT_RING_SCALE       = 1.0
# DEFAULT_N_BARS           = 180
# DEFAULT_BAR_HEIGHT       = 0.28   # fraction of canvas height
# DEFAULT_N_SPARKS         = 60
# DEFAULT_GLOW_BLUR        = 4
# DEFAULT_WATERMARK_OPACITY = 0.35
# DEFAULT_WATERMARK_SIZE   = 0.08   # fraction of canvas height
# DEFAULT_WATERMARK_MARGIN = 24     # px inset from corner


# ── Color helpers ─────────────────────────────────────────────────────────────

def _lerp(a, b, t):
    return a + (b - a) * t

def _lerp_color(a, b, t):
    return tuple(int(_lerp(a[i], b[i], t)) for i in range(3))

def _rgba(rgb, alpha):
    r, g, b = rgb
    return (r, g, b, int(255 * max(0.0, min(1.0, alpha))))


# ── Background ────────────────────────────────────────────────────────────────

def _make_bg(width, height, center_color, edge_color):
    arr    = np.zeros((height, width, 3), dtype=np.float32)
    cx, cy = width / 2, height / 2
    max_r  = math.hypot(cx, cy)
    ys, xs = np.mgrid[0:height, 0:width]
    t      = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max_r
    for ch, (a, b) in enumerate(zip(center_color, edge_color)):
        arr[:, :, ch] = a * (1 - t) + b * t
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


def _precompute_bg_keyframes(width, height, bg_center, accent_bg, n=60):
    """Sine-wave drift: bg_center -> accent_bg -> bg_center over the video."""
    frames = []
    for k in range(n):
        phase  = math.sin(math.pi * k / (n - 1))
        center = _lerp_color(bg_center, accent_bg, phase * 0.55)
        frames.append(_make_bg(width, height, center, bg_center))
    return frames


def _sample_bg(keyframes, frame_idx, total_frames):
    n   = len(keyframes)
    t   = (frame_idx / max(total_frames - 1, 1)) * (n - 1)
    lo  = keyframes[int(t)]
    hi  = keyframes[min(int(t) + 1, n - 1)]
    return Image.blend(lo, hi, t - int(t))


def _amp_tint(bg_frame, amp, width, height, min_dim, cx, cy):
    if amp <= 0.05:
        return bg_frame
    alpha = int(amp * 20)
    tint  = Image.new("RGB", (width, height), (255, 255, 255))
    mask  = Image.new("L",   (width, height), 0)
    md    = ImageDraw.Draw(mask)
    tr    = int(min_dim * 0.45)
    md.ellipse([cx - tr, cy - tr, cx + tr, cy + tr], fill=alpha)
    mask  = mask.filter(ImageFilter.GaussianBlur(radius=min_dim * 0.15))
    return Image.composite(tint, bg_frame, mask)


# ── Logo / watermark loading ──────────────────────────────────────────────────

def _auto_invert(arr, opaque, invert_dark):
    """
    Invert opaque pixel colors if the image won't read on the background:
      invert_dark=True  (dark bg)  → invert dark logos  (mean lum < 96)
      invert_dark=False (light bg) → invert light logos (mean lum > 180)
    Returns modified array.
    """
    if not opaque.any():
        return arr
    lum = (0.299 * arr[opaque, 0] +
           0.587 * arr[opaque, 1] +
           0.114 * arr[opaque, 2])
    should_invert = (invert_dark     and lum.mean() < 96) or \
                    (not invert_dark and lum.mean() > 180)
    if should_invert:
        rgb              = arr[:, :, :3].astype(np.int16)
        rgb[opaque]      = 255 - rgb[opaque]
        arr[:, :, :3]    = rgb.clip(0, 255).astype(np.uint8)
    return arr


def _load_logo(logo_path, logo_size, invert_dark=True):
    raw    = Image.open(logo_path).convert("RGBA")
    arr    = np.array(raw)
    opaque = arr[:, :, 3] > 10
    arr    = _auto_invert(arr, opaque, invert_dark)
    raw    = Image.fromarray(arr, "RGBA")
    return raw.resize((logo_size, logo_size), Image.LANCZOS)


def _load_watermark(wm_path, target_h, invert_dark=True):
    raw    = Image.open(wm_path).convert("RGBA")
    arr    = np.array(raw)
    opaque = arr[:, :, 3] > 10
    arr    = _auto_invert(arr, opaque, invert_dark)
    raw    = Image.fromarray(arr, "RGBA")
    aspect   = raw.width / raw.height
    target_w = int(target_h * aspect)
    return raw.resize((target_w, target_h), Image.LANCZOS)


# ── Rings ─────────────────────────────────────────────────────────────────────

def _draw_ring(draw, cx, cy, radius, thickness, color, alpha):
    fill = _rgba(color, alpha)
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.ellipse(bbox, outline=fill, width=max(1, thickness))


# ── Waveform ──────────────────────────────────────────────────────────────────

def _draw_bottom_waveform(layer, width, height, window_amps, bar_max_px, color):
    draw    = ImageDraw.Draw(layer)
    n       = len(window_amps)
    r, g, b = color
    total_w = width * 0.80
    bar_w   = total_w / n
    x_start = (width - total_w) / 2
    baseline = height
    gap      = 1

    for i, amp in enumerate(window_amps):
        bar_h   = bar_max_px * amp
        x_left  = x_start + i * bar_w
        x_right = x_left + bar_w - gap

        if bar_h < 1:
            continue

        bar_alpha = int(140 + 115 * amp)

        # Bar body
        draw.rectangle(
            [x_left, baseline - bar_h, x_right, baseline],
            fill=(r, g, b, bar_alpha)
        )
        # Rounded cap
        cap_h = min(bar_w * 0.8, 3)
        draw.ellipse(
            [x_left, baseline - bar_h - cap_h * 0.5,
             x_right, baseline - bar_h + cap_h * 0.5],
            fill=(r, g, b, bar_alpha)
        )
        # Reflection
        refl_h = bar_h * 0.25
        if refl_h >= 1:
            draw.rectangle(
                [x_left, baseline, x_right, baseline + refl_h],
                fill=(r, g, b, int(bar_alpha * 0.20))
            )


# ── Particle system ───────────────────────────────────────────────────────────

class Particle:
    __slots__ = ('x', 'y', 'vx', 'vy', 'size', 'age', 'lifetime',
                 'base_alpha', 'color')

    def __init__(self, width, height, amp, rng, palette):
        cx, cy = width / 2, height / 2
        self.x = cx + rng.gauss(0, width  * 0.22)
        self.y = cy + rng.gauss(0, height * 0.22)
        self.x = max(0, min(width,  self.x))
        self.y = max(0, min(height, self.y))

        speed     = rng.uniform(0.2, 0.8 + amp * 1.2)
        angle     = rng.gauss(-math.pi / 2, 0.6)
        self.vx   = math.cos(angle) * speed
        self.vy   = math.sin(angle) * speed

        self.size       = rng.uniform(1.0, 2.5 + amp * 2.5)
        self.lifetime   = int(rng.uniform(40, 120 + amp * 60))
        self.age        = 0
        self.base_alpha = rng.uniform(0.4, 0.9)

        tint = rng.random()
        if tint > 0.8:
            self.color = palette.RING_MID
        elif tint > 0.6:
            self.color = palette.RING_INNER
        else:
            self.color = palette.SPARK_COLOR

    @property
    def alive(self):
        return self.age < self.lifetime

    def update(self):
        self.x  += self.vx
        self.y  += self.vy
        self.vy -= 0.005
        self.age += 1

    @property
    def alpha(self):
        t = self.age / self.lifetime
        if t < 0.10:
            fade = t / 0.10
        elif t > 0.70:
            fade = 1.0 - (t - 0.70) / 0.30
        else:
            fade = 1.0
        return self.base_alpha * fade


def _draw_particles(layer, particles):
    draw = ImageDraw.Draw(layer)
    for p in particles:
        a       = int(255 * p.alpha)
        r, g, b = p.color
        size    = p.size
        bbox    = [p.x - size, p.y - size, p.x + size, p.y + size]
        draw.ellipse(bbox, fill=(r, g, b, a))


# ── Rotation LUT ──────────────────────────────────────────────────────────────

def _build_rotation_lut(amplitudes):
    BASE  = 0.004
    SCALE = 0.040
    lut   = [0.0] * len(amplitudes)
    acc   = 0.0
    for i, a in enumerate(amplitudes):
        acc    += BASE + SCALE * a
        lut[i]  = acc % (2 * math.pi)
    return lut


# ── Watermark fade ────────────────────────────────────────────────────────────

def _watermark_alpha(frame_idx, n_frames, fps, steady_opacity):
    fade_frames = fps * 2
    if frame_idx < fade_frames:
        t = frame_idx / fade_frames
        t = t * t * (3 - 2 * t)   # smoothstep ease-in
    elif frame_idx > n_frames - fade_frames:
        t = (n_frames - frame_idx) / fade_frames
        t = max(0.0, t * t * (3 - 2 * t))
    else:
        t = 1.0
    return steady_opacity * t


# ── Main entry point ──────────────────────────────────────────────────────────

def render_frames(
    amplitude_file:    Path,
    output_dir:        Path,
    width:             int,
    height:            int,
    logo_path:         Path ,
    ring_scale:        float,
    n_bars:            int  ,
    bar_height:        float,
    n_sparks:          int  ,
    glow_blur:         int  ,
    watermark_path:    Path ,
    watermark_opacity: float,
    watermark_size:    float,
    watermark_margin:  int  ,
    fps:               int  ,
    theme:             str  ,
    mode:              str  ,
):
    rng = random.Random(42)

    # ── Palette ───────────────────────────────────────────────────────────────
    p = load_theme(theme, mode)
    print(f"  Theme: {p}")

    # ── Amplitude data ────────────────────────────────────────────────────────
    with open(amplitude_file) as f:
        amplitudes = json.load(f)
    n_frames = len(amplitudes)

    output_dir.mkdir(parents=True, exist_ok=True)

    cx, cy  = width / 2, height / 2
    min_dim = min(width, height)
    s       = ring_scale

    r_logo  = min_dim * 0.14
    r_inner = min_dim * 0.18 * s
    r_mid   = min_dim * 0.24 * s
    r_outer = min_dim * 0.30 * s

    bar_max_px = height * bar_height

    # ── Pre-computation ───────────────────────────────────────────────────────
    print("  Building background keyframes…")
    bg_keys = _precompute_bg_keyframes(
        width, height, p.BG_CENTER, p.ACCENT_BG, n=60
    )

    print("  Building rotation LUT…")
    rot_lut = _build_rotation_lut(amplitudes)

    # ── Logo ──────────────────────────────────────────────────────────────────
    logo_img = None
    if logo_path and Path(logo_path).exists():
        logo_img = _load_logo(logo_path, int(r_logo * 2),
                              invert_dark=p.LOGO_INVERT_DARK)

    # ── Watermark ─────────────────────────────────────────────────────────────
    wm_img = None
    if watermark_path and Path(watermark_path).exists():
        wm_h   = int(height * watermark_size)
        wm_img = _load_watermark(watermark_path, wm_h,
                                 invert_dark=p.LOGO_INVERT_DARK)
        wm_x   = width  - wm_img.width  - watermark_margin
        wm_y   = height - wm_img.height - watermark_margin
        print(f"  Watermark: {wm_img.size} → ({wm_x}, {wm_y})")

    # ── Particle pool ─────────────────────────────────────────────────────────
    particles = []

    # ── Beat detection state ──────────────────────────────────────────────────
    history_len = 10
    amp_history = [0.0] * history_len

    print(f"  Rendering {n_frames} frames…")
    for i, amp in enumerate(amplitudes):

        # Beat detection
        amp_history.pop(0)
        amp_history.append(amp)
        avg_amp    = sum(amp_history) / history_len
        is_beat    = amp > avg_amp * 1.5 and amp > 0.4
        beat_boost = 0.40 if is_beat else 0.0
        hot_color  = p.RING_BEAT if is_beat else p.RING_MID

        # ── Background ────────────────────────────────────────────────────
        bg    = _sample_bg(bg_keys, i, n_frames)
        bg    = _amp_tint(bg, amp, width, height, min_dim, cx, cy)
        frame = bg.convert("RGBA")

        # ── Rings ─────────────────────────────────────────────────────────
        rotation   = rot_lut[i]
        pulse_in   = r_inner + (r_mid   - r_inner) * 0.35 * amp
        pulse_mid  = r_mid   + (r_outer - r_mid)   * 0.25 * amp
        pulse_out  = r_outer + min_dim * 0.04 * s  * amp
        ring_alpha = 0.55 + 0.45 * amp

        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(glow)
        _draw_ring(gd, cx, cy, pulse_in,  22, p.GLOW_TINT,  (ring_alpha + beat_boost) * 0.30)
        _draw_ring(gd, cx, cy, pulse_in,   8, p.RING_INNER,  ring_alpha + beat_boost)
        _draw_ring(gd, cx, cy, pulse_mid, 16, hot_color,     ring_alpha * 0.18)
        _draw_ring(gd, cx, cy, pulse_mid,  5, hot_color,     ring_alpha * 0.88)
        _draw_ring(gd, cx, cy, pulse_out,  3, p.RING_OUTER,  ring_alpha * 0.55)
        frame = Image.alpha_composite(frame,
                    glow.filter(ImageFilter.GaussianBlur(glow_blur)))

        rings = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        rd    = ImageDraw.Draw(rings)
        _draw_ring(rd, cx, cy, pulse_in,  2, p.RING_INNER, min(1.0, ring_alpha + beat_boost))
        _draw_ring(rd, cx, cy, pulse_mid, 2, hot_color,    min(1.0, ring_alpha * 0.92))
        _draw_ring(rd, cx, cy, pulse_out, 1, p.RING_OUTER, ring_alpha * 0.65)
        frame = Image.alpha_composite(frame, rings)

        # ── Waveform ──────────────────────────────────────────────────────
        n      = n_frames
        window = [amplitudes[(i - n_bars + 1 + j) % n] for j in range(n_bars)]
        wf     = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _draw_bottom_waveform(wf, width, height, window, bar_max_px, p.BAR_COLOR)
        frame = Image.alpha_composite(frame, wf)

        # ── Particles ─────────────────────────────────────────────────────
        spawn_base  = amp * 2.5
        spawn_burst = 8 if is_beat else 0
        to_spawn    = int(spawn_base) + spawn_burst
        if rng.random() < (spawn_base % 1.0):
            to_spawn += 1
        to_spawn = min(to_spawn, max(0, n_sparks - len(particles)))
        for _ in range(to_spawn):
            particles.append(Particle(width, height, amp, rng, p))
        for part in particles:
            part.update()
        particles = [part for part in particles if part.alive]

        sparks = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _draw_particles(sparks, particles)
        frame = Image.alpha_composite(frame, sparks)

        # ── Logo ──────────────────────────────────────────────────────────
        if logo_img:
            logo_alpha = int(195 + 60 * amp)
            lo         = logo_img.copy()
            r2, g2, b2, a2 = lo.split()
            a2 = a2.point(lambda px: int(px * logo_alpha / 255))
            lo = Image.merge("RGBA", (r2, g2, b2, a2))
            lw, lh = lo.size
            frame.paste(lo, (int(cx - lw / 2), int(cy - lh / 2)), lo)

        # ── Watermark ─────────────────────────────────────────────────────
        if wm_img:
            wm_a = _watermark_alpha(i, n_frames, fps, watermark_opacity)
            if wm_a > 0.005:
                wm = wm_img.copy()
                r2, g2, b2, a2 = wm.split()
                a2 = a2.point(lambda px: int(px * wm_a))
                wm = Image.merge("RGBA", (r2, g2, b2, a2))
                frame.paste(wm, (wm_x, wm_y), wm)

        frame.convert("RGB").save(
            output_dir / f"frame_{i:05d}.png", optimize=False
        )

        if i % 50 == 0:
            print(f"  frame {i}/{n_frames}  particles={len(particles)}")

    print(f"  Done — {n_frames} frames → {output_dir}")
