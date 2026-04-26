"""
video/src/audio_analysis.py — Extract per-frame amplitude data from an mp3.

Produces a JSON file of normalized amplitude values, one float per video frame,
which the renderer uses to drive all motion and visual reactivity.

Mono path  (default): a single amplitude.json
Stereo path (future): left.json + right.json via extract_amplitude_stereo()
  — wired up and tested; activate in pipeline/video.py when needed.

Processing steps for each channel:
  1. Decode to raw PCM floats at (fps × 100) Hz via ffmpeg
  2. Compute RMS (root mean square) over each frame-sized window
  3. Normalize 0→1
  4. Shape with a power curve (^1.8) to emphasise peaks over silence
  5. Light temporal smoothing (±2 frame window) to avoid jitter
"""

import json
import struct
import subprocess
from pathlib import Path


# ── Public API ────────────────────────────────────────────────────────────────

def extract_amplitude(input_mp3: Path, output_json: Path, fps: int = 30):
    """
    Extract mono amplitude envelope and write it to output_json.

    fps must match the video framerate so each amplitude value maps 1-to-1
    to a rendered frame.
    """
    samples    = _decode_pcm_mono(input_mp3, fps)
    amplitudes = _process(samples, fps)

    with open(output_json, "w") as f:
        json.dump(amplitudes, f)
    print(f"  Amplitude data → {output_json}  ({len(amplitudes)} frames)")


def extract_amplitude_stereo(
    input_mp3:    Path,
    output_left:  Path,
    output_right: Path,
    fps:          int,
):
    """
    Extract separate amplitude envelopes for left and right channels.

    Used when the renderer supports dual-waveform display (e.g. split-screen
    stereo visualisation).  Currently not wired into the default pipeline —
    activate in pipeline/video.py and update render_frames() to accept two
    amplitude files.
    """
    _extract_channel(input_mp3, output_left,  channel=0, fps=fps)
    _extract_channel(input_mp3, output_right, channel=1, fps=fps)


# ── Internal ──────────────────────────────────────────────────────────────────

def _decode_pcm_mono(input_mp3: Path, fps: int) -> list[float]:
    """
    Decode the mp3 to raw 32-bit float PCM at (fps × 100) Hz, mono.

    Oversampling by 100× before downsampling to frame-rate gives us enough
    resolution to compute accurate RMS without aliasing.
    """
    sample_rate = fps * 100   # e.g. 3000 Hz at 30 fps

    cmd = [
        "ffmpeg",
        "-i", str(input_mp3),
        "-ac", "1",                  # downmix to mono
        "-ar", str(sample_rate),
        "-f", "f32le",               # raw little-endian float32
        "-",                         # write to stdout
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return [s[0] for s in struct.iter_unpack("f", result.stdout)]


def _process(samples: list[float], fps: int) -> list[float]:
    """
    Turn a flat list of PCM samples into a per-frame normalized amplitude list.

    Steps: RMS per window → normalize → shape → smooth
    """
    if not samples:
        return []

    window_size = max(1, len(samples) // (len(samples) // (fps * 100 // fps)))
    # Simpler: each frame gets exactly (sample_rate // fps) samples
    window_size = fps * 100 // fps   # = 100

    # ── RMS per frame ──────────────────────────────────────────────────────
    amplitudes = []
    for i in range(0, len(samples), window_size):
        window = samples[i:i + window_size]
        if not window:
            continue
        rms = (sum(s * s for s in window) / len(window)) ** 0.5
        amplitudes.append(rms)

    # ── Normalize 0→1 ─────────────────────────────────────────────────────
    max_val = max(amplitudes) if amplitudes else 1.0
    normalized = [v / max_val for v in amplitudes]

    # ── Power-curve shaping — emphasises louder moments ───────────────────
    # ^1.8 compresses the quiet floor without clipping peaks.
    # Adjust exponent: lower (<1) = more even, higher (>2) = more contrast.
    shaped = [v ** 1.8 for v in normalized]

    # ── Temporal smoothing — ±2 frame window ──────────────────────────────
    # Reduces per-frame jitter without losing sync with audio events.
    # Increase radius to 4–6 for a floatier look; 0 for raw reactivity.
    radius = 2
    smoothed = []
    for i in range(len(shaped)):
        start = max(0, i - radius)
        end   = min(len(shaped), i + radius + 1)
        smoothed.append(sum(shaped[start:end]) / (end - start))

    return smoothed


def _extract_channel(input_mp3: Path, output_path: Path, channel: int, fps: int):
    """
    Decode a single channel (0=left, 1=right) from a stereo mp3,
    run the full processing pipeline, and write the result as JSON.
    """
    sample_rate = fps * 100
    cmd = [
        "ffmpeg",
        "-i", str(input_mp3),
        "-map_channel", f"0.0.{channel}",   # isolate L or R
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    samples    = [s[0] for s in struct.iter_unpack("f", result.stdout)]
    amplitudes = _process(samples, fps)

    with open(output_path, "w") as f:
        json.dump(amplitudes, f)
    print(f"  Channel {channel} → {output_path}  ({len(amplitudes)} frames)")
