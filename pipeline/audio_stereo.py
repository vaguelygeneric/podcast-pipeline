"""
pipeline/audio_singlefile_conversation.py

Two-speaker conversation audio pipeline.

Input:
    Single stereo m4a where:
        Left  channel = Speaker A lav
        Right channel = Speaker B lav

Workflow:
    1. Split stereo channels
    2. Process each speaker independently
    3. Apply light gating + compression
    4. Mix both speakers to mono
    5. Apply two-pass loudnorm
    6. Export podcast-ready mono mp3

Goals:
    - Keep speakers at similar perceived volume
    - Reduce room/background noise
    - Preserve natural conversational dynamics
    - Avoid overprocessed "radio voice" sound
"""

import re
import json
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


def loudnorm_pass1(input_file: Path) -> dict:
    """
    Pass 1:
        Measure loudness stats after full processing chain.

    Returns:
        ffmpeg loudnorm JSON stats block.
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
    loudnorm=I={_LUFS}:TP={_TP}:LRA={_LRA}:print_format=json
    """

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-filter_complex",
        filter_complex,
        "-f",
        "null",
        "-"
    ]

    result = _run(cmd, capture=True)

    match = re.search(r'\{.*\}', result.stderr, re.DOTALL)

    if not match:
        raise RuntimeError(
            "loudnorm pass 1 failed — no JSON found in ffmpeg stderr"
        )

    return json.loads(match.group(0))


def loudnorm_pass2(
    input_file: Path,
    output_file: Path,
    stats: dict
):
    """
    Pass 2:
        Apply measured loudness normalization.
    """

    loudnorm_filter = (
        f"loudnorm=I={_LUFS}:TP={_TP}:LRA={_LRA}:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:"
        f"print_format=summary"
    )

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
    {loudnorm_filter}
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


def process_conversation_audio(
    input_file: Path,
    output_file: Path
):
    """
    Full two-pass production pipeline.
    """

    stats = loudnorm_pass1(input_file)

    loudnorm_pass2(
        input_file=input_file,
        output_file=output_file,
        stats=stats
    )


def single_pass(
    input_file: Path,
    output_file: Path
):
    """
    Faster single-pass version for testing.
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
