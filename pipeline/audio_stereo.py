"""
pipeline/audio_singlefile_conversation.py

Two-speaker conversation audio pipeline.

Input:
    Single stereo m4a where:
        Left  channel = Speaker A lav
        Right channel = Speaker B lav

Workflow:
    1. Split stereo channels
    2. Process each speaker independently (gate + compressor cleanup)
    3. Two-pass loudnorm EACH channel independently to the same target,
       so an unevenly-recorded pair (one lav hot, one quiet) gets leveled
       before they're ever combined -- a single loudnorm pass on the mixed
       signal can't do this, since it just "averages" both speakers and the
       louder one's peaks suppress how much correction the quieter one gets
    4. Mix both speakers to mono (no auto-attenuation -- each channel is
       already at target level, so a plain sum is correct)
    5. Run one more two-pass loudnorm on the mixdown as a safety net, since
       summing two independently-normalized speech signals typically lands
       a few dB above target
    6. Export podcast-ready mono mp3

Goals:
    - Keep speakers at similar perceived volume
    - Reduce room/background noise
    - Preserve natural conversational dynamics
    - Avoid overprocessed "radio voice" sound
"""

import re
import json
import tempfile
import subprocess
from pathlib import Path


# Final delivery targets
_LUFS = -16
_TP = -1.5
_LRA = 11


def _run(cmd: list, capture: bool = False):
    """Run shell command."""
    print(f"\n>> {' '.join(str(c) for c in cmd)}")

    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)

    subprocess.run(cmd, check=True)


# -------------------------------------------------------------------
# CHANNEL PROCESSING
# -------------------------------------------------------------------

# Conservative lav-mic cleanup chain.
#
# Tuned for:
#   - conversational podcasts
#   - inconsistent recording locations
#   - low-gain lav recording
#
# Philosophy:
#   light cleanup > aggressive cleanup
#
# Each channel is processed independently before mixdown.
#
_CHANNEL_FILTER = (
    "highpass=f=80,"
    "lowpass=f=14000,"
    "agate=threshold=-45dB:ratio=8:"
        "attack=20:release=250,"
    "acompressor=threshold=-18dB:"
        "ratio=3:"
        "attack=20:"
        "release=250:"
        "makeup=3"
)


# -------------------------------------------------------------------
# PER-CHANNEL LEVELING HELPERS
#
# These extract and independently loudness-match each speaker before
# mixdown -- see module docstring step 3 for why this matters.
# -------------------------------------------------------------------

def _extract_and_filter_channel(
    input_file: Path,
    channel: str,
    out_wav: Path,
    channel_filter: str
):
    """
    Split the input stereo file and pull out just one channel
    ("left" or "right"), running it through the per-speaker cleanup
    filter chain. Output is a 44.1kHz mono wav, ready for independent
    loudnorm measurement.
    """

    filter_complex = (
        f"channelsplit=channel_layout=stereo[left][right];"
        f"[{channel}]{channel_filter}[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "44100",
        str(out_wav),
    ]

    _run(cmd)


def _loudnorm_measure(input_wav: Path, lufs: float, tp: float, lra: float) -> dict:
    """Pass 1 for a single (already-filtered) mono channel wav."""

    filter_chain = f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_wav),
        "-af", filter_chain,
        "-f", "null", "-",
    ]

    result = _run(cmd, capture=True)

    match = re.search(r'\{.*\}', result.stderr, re.DOTALL)

    if not match:
        raise RuntimeError(
            f"loudnorm measurement failed for {input_wav} — "
            "no JSON found in ffmpeg stderr"
        )

    return json.loads(match.group(0))


def _loudnorm_apply(
    input_wav: Path,
    output_wav: Path,
    stats: dict,
    lufs: float,
    tp: float,
    lra: float
):
    """Pass 2 for a single (already-filtered) mono channel wav."""

    ln = (
        f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:print_format=summary"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_wav),
        "-af", ln,
        "-ar", "44100",
        str(output_wav),
    ]

    _run(cmd)


def process_conversation_audio(
    input_file: Path,
    output_file: Path,
    profile: dict = None
):
    """
    Full production pipeline, with independent loudness leveling per
    channel before mixdown (see module docstring for the full workflow
    and why per-channel matters).

    profile: optional dict of overrides from audio_profiles.json
             (keys: lufs, tp, lra, channel_filter). Falls back to this
             module's own defaults when not given, so direct/manual
             calls keep working unchanged.
    """

    profile = profile or {}
    lufs = profile.get("lufs", _LUFS)
    tp = profile.get("tp", _TP)
    lra = profile.get("lra", _LRA)
    channel_filter = profile.get("channel_filter", _CHANNEL_FILTER)

    with tempfile.TemporaryDirectory(prefix="dual_lav_") as tmp_str:
        tmp = Path(tmp_str)

        left_filtered = tmp / "left_filtered.wav"
        right_filtered = tmp / "right_filtered.wav"
        _extract_and_filter_channel(input_file, "left", left_filtered, channel_filter)
        _extract_and_filter_channel(input_file, "right", right_filtered, channel_filter)

        left_stats = _loudnorm_measure(left_filtered, lufs, tp, lra)
        right_stats = _loudnorm_measure(right_filtered, lufs, tp, lra)

        left_norm = tmp / "left_norm.wav"
        right_norm = tmp / "right_norm.wav"
        _loudnorm_apply(left_filtered, left_norm, left_stats, lufs, tp, lra)
        _loudnorm_apply(right_filtered, right_norm, right_stats, lufs, tp, lra)

        # Mix the two already-leveled channels. normalize=0 so amix doesn't
        # halve them back down -- each is already sitting at target loudness.
        mixed = tmp / "mixed.wav"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(left_norm),
            "-i", str(right_norm),
            "-filter_complex", "amix=inputs=2:duration=longest:normalize=0",
            "-ar", "44100",
            str(mixed),
        ]
        _run(cmd)

        # Safety-net pass: summing two independently-normalized speech
        # channels typically lands a few dB above target, so re-measure
        # and correct once more on the actual mixdown, encoding straight
        # to the final mono mp3.
        final_stats = _loudnorm_measure(mixed, lufs, tp, lra)
        ln = (
            f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:"
            f"measured_I={final_stats['input_i']}:"
            f"measured_LRA={final_stats['input_lra']}:"
            f"measured_TP={final_stats['input_tp']}:"
            f"measured_thresh={final_stats['input_thresh']}:"
            f"offset={final_stats['target_offset']}:"
            f"linear=true:print_format=summary"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(mixed),
            "-af", ln,
            "-ar", "44100",
            "-ac", "1",
            "-b:a", "96k",
            str(output_file),
        ]
        _run(cmd)


def single_pass(
    input_file: Path,
    output_file: Path
):
    """
    Faster single-pass version for testing. Note this does NOT do
    per-channel leveling -- it's a whole-mix single loudnorm pass, same
    limitation as before. Use process_conversation_audio() for anything
    where the two speakers were recorded at noticeably different gain.
    """

    filter_complex = f"""
    channelsplit=channel_layout=stereo[left][right];

    [left]
    {_CHANNEL_FILTER}
    [left_processed];

    [right]
    {_CHANNEL_FILTER}
    [right_processed];

    [left_processed][right_processed]
    amix=inputs=2:normalize=0,
    loudnorm=I={_LUFS}:TP={_TP}:LRA={_LRA}
    """

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-filter_complex",
        filter_complex,
        "-ar",
        "44100",
        "-ac",
        "1",
        "-b:a",
        "96k",
        str(output_file),
    ]

    _run(cmd)
