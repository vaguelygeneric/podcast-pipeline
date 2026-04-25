# src/palette.py
#
# Color palette derived from The Generic's CSS custom properties.
# Edit hex values here to retheme the entire video output.
#
# Dark theme tokens  →  video role
# ─────────────────────────────────────────────────────────────────────────────

def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# ── Site tokens (dark theme) ──────────────────────────────────────────────────
BG          = _hex("#141210")   # --bg          : deepest background
BG_ALT      = _hex("#1e1b18")   # --bg-alt      : background gradient midpoint
SURFACE     = _hex("#252118")   # --surface     : elevated surface (unused directly)
BORDER      = _hex("#352f27")   # --border      : subtle dividers / quiet rings
TEXT        = _hex("#f0ece3")   # --text        : full-brightness text → spark color
TEXT_MUTED  = _hex("#9e9588")   # --text-muted  : subdued → dim glow tint
ACCENT      = _hex("#6366f1")   # --accent      : primary indigo → inner ring
ACCENT_ALT  = _hex("#818cf8")   # --accent-alt  : lighter indigo → mid ring / bars
ACCENT_BG   = _hex("#2e1d4f")   # --accent-bg   : deep violet → glow halo tint

# ── Derived / composed video roles ───────────────────────────────────────────

# Background gradient: BG_ALT at center → BG at edges
BG_CENTER   = BG_ALT            # radial gradient inner color
BG_EDGE     = BG                # radial gradient outer color

# Ring colors
RING_INNER  = ACCENT            # innermost ring: #6366f1 (indigo-500)
RING_MID    = ACCENT_ALT        # middle ring:    #818cf8 (indigo-400)
RING_OUTER  = BORDER            # outermost ring: #352f27 (muted warm)

# Beat flash: brighten mid ring toward near-white on transient hits
RING_BEAT   = _hex("#c7d2fe")   # indigo-200 — hot beat flash color

# Arc waveform bars
BAR_COLOR   = ACCENT            # same indigo as inner ring

# Rotating spark particles
SPARK_COLOR = TEXT              # near-white: #f0ece3

# Glow halo behind rings (semi-transparent ACCENT_BG tint)
GLOW_TINT   = ACCENT_BG         # #2e1d4f deep violet
