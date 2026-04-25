"""
pipeline/audio.py — Audio cleanup and loudness normalization.

Converts source m4a (or any ffmpeg-readable format) to a broadcast-ready
mono mp3 at -16 LUFS / -1.5 dB true peak.

Two-pass loudnorm is the default for production: pass 1 measures the file,
pass 2 applies linear normalization using those exact measurements.  The
result is noticeably cleaner than a single-pass estimate.

Single-pass is available as a quick preview / comparison tool (--test-audio).

Audio filter chain applied to every output:
  highpass=f=80     — rolls off low-end rumble (room noise, mic handling)
  lowpass=f=14000   — rolls off high-frequency hiss above vocal range
  afftdn=nf=-20     — spectral noise reduction at -20 dB floor
"""

import re
import json
import subprocess
from pathlib import Path


# Shared pre-processing filter applied before loudnorm in both passes.
# Edit here to adjust the noise-reduction chain for all outputs.
_PRE_FILTER = "highpass=f=80,lowpass=f=14000,afftdn=nf=-20"

# Loudnorm targets — matches most podcast platform recommendations.
_LUFS   = -16     # integrated loudness
_TP     = -1.5    # true peak
_LRA    = 11      # loudness range


def _run(cmd: list, capture: bool = False):
    """Run a shell command, printing it first.  capture=True returns CompletedProcess."""
    print(f"\n>> {' '.join(str(c) for c in cmd)}")
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    subprocess.run(cmd, check=True)


def loudnorm_pass1(input_file: Path) -> dict:
    """
    Pass 1: measure the file's integrated loudness with ffmpeg loudnorm.

    Returns the JSON stats block ffmpeg prints to stderr, which pass 2 needs
    to apply perfectly linear (not dynamically compressed) normalization.
    """
    filter_chain = f"{_PRE_FILTER},loudnorm=I={_LUFS}:TP={_TP}:LRA={_LRA}:print_format=json"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-af", filter_chain,
        "-f", "null", "-",       # discard output — we only want the printed stats
    ]
    result = _run(cmd, capture=True)

    # ffmpeg writes the loudnorm JSON to stderr
    match = re.search(r'\{.*\}', result.stderr, re.DOTALL)
    if not match:
        raise RuntimeError("loudnorm pass 1 failed — no JSON found in ffmpeg stderr")

    return json.loads(match.group(0))


def loudnorm_pass2(input_file: Path, output_file: Path, stats: dict):
    """
    Pass 2: apply linear loudness normalization using the measurements from pass 1.

    Output is mono 44.1 kHz mp3 at 96 kbps — suitable for spoken-word podcast
    distribution (small file, full vocal clarity).
    """
    ln = (
        f"loudnorm=I={_LUFS}:TP={_TP}:LRA={_LRA}:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:print_format=summary"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-af", f"{_PRE_FILTER},{ln}",
        "-ar", "44100",    # sample rate
        "-ac", "1",        # mono (podcast standard)
        "-b:a", "96k",     # bitrate — fine for speech; bump to 128k for music
        str(output_file),
    ]
    _run(cmd)


def single_pass(input_file: Path, output_file: Path):
    """
    Single-pass normalization — faster but uses ffmpeg's internal estimate
    rather than a measured target.  Use for quick A/B comparisons only
    (--test-audio mode); always use the two-pass version for production.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-af", f"{_PRE_FILTER},loudnorm=I={_LUFS}:TP={_TP}:LRA={_LRA}",
        "-ar", "44100",
        "-ac", "1",
        "-b:a", "96k",
        str(output_file),
    ]
    _run(cmd)
