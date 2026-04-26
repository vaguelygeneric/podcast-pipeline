# video/src/palette.py
#
# Theme registry — mirrors the site's CSS custom property system.
# Each theme has a light and dark mode. The renderer always uses dark mode
# tokens for video (better contrast), but light mode is available for
# future use (e.g. light-background export formats).
#
# Usage:
#   from palette import load_theme
#   p = load_theme("slate-blue", "dark")
#   p.RING_INNER  →  (99, 102, 241)
#
# CLI flags:  --theme slate-blue  --mode dark

from __future__ import annotations
from dataclasses import dataclass


# ── Conversion helpers ────────────────────────────────────────────────────────

def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _lighten(rgb: tuple, amount: float) -> tuple:
    """Blend rgb toward white by amount (0–1). Used for beat-flash color."""
    return tuple(int(rgb[i] + (255 - rgb[i]) * amount) for i in range(3))


# ── Theme definition ──────────────────────────────────────────────────────────

@dataclass
class _Mode:
    bg:         str
    bg_alt:     str
    surface:    str
    border:     str
    text:       str
    text_muted: str
    accent:     str
    accent_alt: str
    accent_bg:  str


@dataclass
class _Theme:
    name:  str
    label: str
    light: _Mode
    dark:  _Mode


# ── Theme registry ────────────────────────────────────────────────────────────
# Shared light base (all themes use the same warm-parchment light base)
_LIGHT_BASE = dict(
    bg         = "#f5f2eb",
    bg_alt     = "#ede9df",
    surface    = "#ffffff",
    border     = "#d6d0c4",
    text       = "#1a1714",
    text_muted = "#6b6459",
)

# Shared dark base (all themes share the same warm-dark neutrals)
_DARK_BASE = dict(
    bg         = "#141210",
    bg_alt     = "#1e1b18",
    surface    = "#252118",
    border     = "#352f27",
    text       = "#f0ece3",
    text_muted = "#9e9588",
)


def _theme(name, label,
           light_accent, light_accent_alt, light_accent_bg,
           dark_accent,  dark_accent_alt,  dark_accent_bg) -> _Theme:
    return _Theme(
        name  = name,
        label = label,
        light = _Mode(**_LIGHT_BASE,
                      accent=light_accent, accent_alt=light_accent_alt,
                      accent_bg=light_accent_bg),
        dark  = _Mode(**_DARK_BASE,
                      accent=dark_accent, accent_alt=dark_accent_alt,
                      accent_bg=dark_accent_bg),
    )


THEMES: dict[str, _Theme] = {t.name: t for t in [

    _theme("slate-blue",   "Slate Blue",
           "#3730a3", "#312e81", "#eef2ff",   # light
           "#6366f1", "#818cf8", "#2e1d4f"),  # dark  ← original / default

    _theme("purple",       "Purple",
           "#7c3aed", "#6d28d9", "#faf5ff",
           "#a78bfa", "#c4b5fd", "#3f2f5f"),

    _theme("teal",         "Teal",
           "#0d9488", "#0f766e", "#f0fdfa",
           "#14b8a6", "#2dd4bf", "#134e4a"),

    _theme("deep-green",   "Deep Green",
           "#065f46", "#064e3b", "#f0fdf4",
           "#6ee7b7", "#a7f3d0", "#134e4a"),

    _theme("green",        "Green",
           "#16a34a", "#15803d", "#f0fdf4",
           "#86efac", "#bbf7d0", "#1b3a1f"),

    _theme("gold",         "Gold",
           "#d97706", "#b45309", "#fffbeb",
           "#fcd34d", "#fde68a", "#3d2817"),

    _theme("burnt-orange", "Burnt Orange",
           "#92400e", "#7c2d12", "#fef3c7",
           "#d97706", "#f59e0b", "#3d2817"),
]}

DEFAULT_THEME = "slate-blue"
DEFAULT_MODE  = "dark"


# ── Resolved palette (what the renderer actually uses) ────────────────────────

class Palette:
    """
    A resolved set of video color roles derived from a theme + mode.
    All values are (R, G, B) tuples ready for PIL.
    """

    def __init__(self, theme: _Theme, mode: str):
        m = theme.dark if mode == "dark" else theme.light

        # Raw tokens
        self.BG         = _hex(m.bg)
        self.BG_ALT     = _hex(m.bg_alt)
        self.SURFACE    = _hex(m.surface)
        self.BORDER     = _hex(m.border)
        self.TEXT       = _hex(m.text)
        self.TEXT_MUTED = _hex(m.text_muted)
        self.ACCENT     = _hex(m.accent)
        self.ACCENT_ALT = _hex(m.accent_alt)
        self.ACCENT_BG  = _hex(m.accent_bg)

        # ── Video roles ───────────────────────────────────────────────────────

        # Background gradient
        self.BG_CENTER  = self.BG_ALT       # radial center (slightly lighter)
        self.BG_EDGE    = self.BG           # radial edge (deepest)

        # Rings
        self.RING_INNER = self.ACCENT       # primary accent → innermost ring
        self.RING_MID   = self.ACCENT_ALT   # secondary accent → middle ring
        self.RING_OUTER = self.BORDER       # muted neutral → outermost quiet ring

        # Beat flash: accent lightened ~55% toward white
        self.RING_BEAT  = _lighten(self.ACCENT, 0.55)

        # Waveform bars
        self.BAR_COLOR  = self.ACCENT

        # Particles
        self.SPARK_COLOR = self.TEXT        # near-white in dark mode

        # Glow halo (the soft bloom behind rings)
        self.GLOW_TINT  = self.ACCENT_BG

        # Logo auto-invert threshold (luminance < this → invert to light)
        # In light mode the video bg is light, so we invert dark logos.
        # In dark mode the video bg is dark, so we invert light logos.
        self.LOGO_INVERT_DARK = (mode == "dark")

        # Hex helpers for FFmpeg filter strings
        self.BG_HEX  = m.bg.lstrip("#")
        self.BAR_HEX = m.accent.lstrip("#")

        self._theme_name = theme.name
        self._mode       = mode

    def __repr__(self):
        return f"<Palette {self._theme_name}/{self._mode}>"


# ── Public API ────────────────────────────────────────────────────────────────

def load_theme(theme_name: str = DEFAULT_THEME,
               mode: str = DEFAULT_MODE) -> Palette:
    """
    Load a named theme in the specified mode.

    Args:
        theme_name: one of the keys in THEMES (e.g. "slate-blue", "gold")
        mode:       "dark" or "light"

    Returns:
        Palette instance with all video color roles resolved.

    Raises:
        ValueError: if theme_name or mode is unrecognized.
    """
    theme_name = theme_name.lower().strip()
    mode       = mode.lower().strip()

    if theme_name not in THEMES:
        available = ", ".join(sorted(THEMES))
        raise ValueError(
            f"Unknown theme '{theme_name}'. Available: {available}"
        )
    if mode not in ("dark", "light"):
        raise ValueError(f"Mode must be 'dark' or 'light', got '{mode}'")

    return Palette(THEMES[theme_name], mode)


def list_themes() -> list[str]:
    return sorted(THEMES)
