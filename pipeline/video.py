"""
pipeline/video.py — Orchestrates video generation from a processed mp3.

Delegates the actual frame rendering to video/src/renderer.py and the
amplitude analysis to video/src/audio_analysis.py.  This module's only job
is to wire those pieces together and call ffmpeg to assemble the final mp4.

Stereo note: when two waveform files are available (left + right channels),
render_frames() accepts both.  Currently only mono is wired in; the stereo
path is stubbed in audio_analysis.py and ready to activate when needed.
"""

import subprocess
import sys
from pathlib import Path


def build_video(
    mp3_path:   Path,
    logo_path:  Path,
    resolution: str = "1280x720",
    fps:        int = 30,
    quick:      bool = False,
):
    """
    Full video build:
      1. Extract amplitude data from the mp3 → temp/amplitude.json
      2. Render one PNG frame per video frame → temp/frames/
      3. Mux frames + audio into output/<stem>.mp4 via ffmpeg

    Args:
        mp3_path:   Clean mp3 produced by the audio stage.
        logo_path:  PNG with transparency to overlay in the center ring.
        resolution: "WIDTHxHEIGHT" string, e.g. "1920x1080".
        fps:        Target framerate. 30 is standard; lower for faster renders.
        quick:      Use the simpler renderer (faster, fewer visual effects).
    """
    try:
        width, height = map(int, resolution.lower().split("x"))
    except ValueError:
        sys.exit(f"Error: --resolution must be WIDTHxHEIGHT, got '{resolution}'")

    # ── Step 1: amplitude extraction ─────────────────────────────────────────
    # Importing here keeps startup fast when video stage is skipped.
    from video.src.audio_analysis import extract_amplitude

    amp_file = Path("temp/amplitude.json")
    amp_file.parent.mkdir(exist_ok=True)
    extract_amplitude(mp3_path, amp_file, fps)

    # ── Step 2: frame rendering ───────────────────────────────────────────────
    from video.src.renderer import render_frames, render_frames_quick

    frames_dir = Path("temp/frames")

    if quick:
        print("Using quick renderer (faster, simpler visuals)…")
        render_frames_quick(amp_file, frames_dir, width, height)
    else:
        # Import the visual tuning defaults so they stay in one place
        from video.src.renderer import (
            DEFAULT_RING_SCALE, DEFAULT_N_BARS,
            DEFAULT_BAR_HEIGHT, DEFAULT_N_SPARKS, DEFAULT_GLOW_BLUR,
        )
        print("Using full renderer…")
        render_frames(
            amplitude_file = amp_file,
            output_dir     = frames_dir,
            width          = width,
            height         = height,
            logo_path      = logo_path,
            ring_scale     = DEFAULT_RING_SCALE,
            n_bars         = DEFAULT_N_BARS,
            bar_height     = DEFAULT_BAR_HEIGHT,
            n_sparks       = DEFAULT_N_SPARKS,
            glow_blur      = DEFAULT_GLOW_BLUR,
        )

    # ── Step 3: mux frames + audio into mp4 ──────────────────────────────────
    output_mp4 = Path("output") / (mp3_path.stem + ".mp4")
    _mux_video(frames_dir, mp3_path, output_mp4, fps)
    print(f"\nVideo created: {output_mp4}")


def _mux_video(frames_dir: Path, audio: Path, output: Path, fps: int):
    """Combine the rendered PNG frames and the source audio into an mp4."""
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),   # frame sequence from renderer
        "-i", str(audio),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",       # broadest player compatibility
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",                 # stop when the shorter stream ends
        str(output),
    ]
    print(f"\n>> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
