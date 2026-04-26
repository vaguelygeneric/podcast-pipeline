"""
video/src/palette.py — Color palette for the video renderer.

All colors are derived from the site's CSS custom properties (dark theme),
mapped to their video roles here.  To retheme the entire video output, edit
only the hex values in the "Site tokens" block below.

Colors are stored as plain (R, G, B) tuples so they can be passed directly
to Pillow's drawing functions.
"""


def _hex(h: str) -> tuple[int, int, int]:
    """Parse a CSS hex color string ('#rrggbb') into an (R, G, B) tuple."""
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ── Site tokens (dark theme) ─────────────────────────────────────────────────
#    CSS variable       →  value        →  description
BG          = _hex("#141210")   # --bg          : deepest background
BG_ALT      = _hex("#1e1b18")   # --bg-alt      : background gradient midpoint
SURFACE     = _hex("#252118")   # --surface     : elevated surface (unused directly)
BORDER      = _hex("#352f27")   # --border      : subtle dividers / quiet rings
TEXT        = _hex("#f0ece3")   # --text        : full-brightness text → spark color
TEXT_MUTED  = _hex("#9e9588")   # --text-muted  : subdued → dim glow tint
ACCENT      = _hex("#6366f1")   # --accent      : primary indigo → inner ring
ACCENT_ALT  = _hex("#818cf8")   # --accent-alt  : lighter indigo → mid ring / bars
ACCENT_BG   = _hex("#2e1d4f")   # --accent-bg   : deep violet → glow halo tint

# ── Derived video roles ───────────────────────────────────────────────────────

# Background radial gradient
BG_CENTER   = BG_ALT            # warmer dark at centre
BG_EDGE     = BG                # deeper black at corners

# Concentric ring colors (inner → outer)
RING_INNER  = ACCENT            # #6366f1 — indigo-500
RING_MID    = ACCENT_ALT        # #818cf8 — indigo-400
RING_OUTER  = BORDER            # #352f27 — muted warm brown

# Beat flash: middle ring turns near-white on loud transients
RING_BEAT   = _hex("#c7d2fe")   # indigo-200

# Arc waveform bars radiating from the outer ring
BAR_COLOR   = ACCENT            # same indigo as inner ring

# Rotating spark particles orbiting the outer ring
SPARK_COLOR = TEXT              # near-white #f0ece3

# Semi-transparent glow halo behind the rings
GLOW_TINT   = ACCENT_BG         # deep violet #2e1d4f
