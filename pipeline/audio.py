"""
pipeline/audio.py — Audio cleanup and loudness normalization.

Converts source m4a (or any ffmpeg-readable format) to a broadcast-ready
mono mp3. Defaults to -16 LUFS / -1.5 dB true peak / 11 LU range, with a
noise-reduction pre-filter chain — all overridable per audio profile (see
"profile overrides" below).

Two-pass loudnorm is the default for production: pass 1 measures the file,
pass 2 applies linear normalization using those exact measurements.  The
result is noticeably cleaner than a single-pass estimate.

Single-pass is available as a quick preview / comparison tool (--test-audio).

Default audio filter chain applied before loudnorm:
  highpass=f=80     — rolls off low-end rumble (room noise, mic handling)
  lowpass=f=14000   — rolls off high-frequency hiss above vocal range
  afftdn=nf=-20     — spectral noise reduction at -20 dB floor

PROFILE OVERRIDES:

  Every function here takes an optional `profile` dict — the same kind of
  dict pipeline/audio_stereo.py's dual-channel functions accept, sourced
  from a named entry in config/defaults.json's "audio_profiles" registry.
  Recognized keys, all optional (falls back to this module's own defaults
  below when a key or the whole profile is omitted, so direct/manual calls
  keep working unchanged):

    lufs, tp, lra     — loudnorm targets (see constants below for meaning)
    pre_filter        — replaces the whole noise-reduction chain above
                         (NOT appended to it — if you override this,
                         include whatever cleanup you still want)
    fade_out_seconds  — linear fade-out over the last N seconds of the
                         episode. Folded into the same -af chain and the
                         same ffmpeg invocation that already does loudnorm
                         and encodes to mp3 — NOT a separate pass. That
                         matters: duration and bitrate (-b:a 96k) are then
                         guaranteed unchanged by adding a fade, since
                         nothing about the encode itself changes, just one
                         more filter in the same chain. A separate
                         re-encode pass for this is a real footgun — it's
                         very easy for such a pass to end up on a different
                         bitrate than the rest of the pipeline uses, which
                         changes file size independently of anything to do
                         with the fade itself. Duration is measured via
                         ffprobe on the (unmodified-duration) input file,
                         since none of this module's filters change
                         duration, so input duration == output duration.
                         Only applied to the final encode -- NOT included
                         in loudnorm_pass1()'s measurement pass, since
                         that pass exists purely to measure the original
                         file's loudness for pass 2's correction; the fade
                         is a delivery-only cosmetic step layered on top,
                         applied after loudnorm in the filter chain so it
                         fades the final normalized signal rather than
                         interacting with loudnorm's gain calculation.

  This is what makes it possible to test a filter-chain change (or a new
  show with a different mic/room) against a throwaway profile name without
  touching the "daily" profile real episodes are published under — add a
  new "audio_profiles" entry, point a test episode's --audio-profile at it.
"""

import re
import json
import subprocess
from pathlib import Path


# Defaults — used whenever a profile omits a key, or no profile is given at
# all (e.g. test_audio.py's calls, or any other direct/manual caller).
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


def _probe_duration_seconds(file: Path) -> float:
    """
    Raw-seconds duration via ffprobe -- deliberately separate from
    pipeline/publish.py's get_duration(), which truncates to whole seconds
    for display ("MM:SS"). A fade-out start time computed from a truncated
    duration could be off by up to a second, which matters at the
    resolution of a 1-2 second fade.
    """
    cmd = [
        "ffprobe", "-i", str(file),
        "-show_entries", "format=duration",
        "-v", "quiet",
        "-of", "csv=p=0",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def _fade_out_filter(input_file: Path, fade_out_seconds) -> str:
    """
    Build an afade filter string for the last `fade_out_seconds` of
    `input_file`, or return "" if fade_out_seconds is falsy (None or 0) --
    the common case, so every call site can unconditionally append this
    with no branching of its own.

    Clamps the fade start to 0 if fade_out_seconds turns out to be longer
    than the file itself, rather than handing ffmpeg a negative start time.
    """
    if not fade_out_seconds:
        return ""

    duration = _probe_duration_seconds(input_file)
    start = max(0.0, duration - float(fade_out_seconds))
    return f",afade=t=out:st={start:.3f}:d={fade_out_seconds}"


def loudnorm_pass1(input_file: Path, profile: dict = None) -> dict:
    """
    Pass 1: measure the file's integrated loudness with ffmpeg loudnorm.

    Returns the JSON stats block ffmpeg prints to stderr, which pass 2 needs
    to apply perfectly linear (not dynamically compressed) normalization.

    profile: optional dict of overrides — see module docstring. Whatever is
             passed here MUST also be passed to the matching loudnorm_pass2()
             call — pass 2's measured_* values only mean what they say if
             the file was measured with the same filter chain and targets.
    """
    profile = profile or {}
    pre_filter = profile.get("pre_filter", _PRE_FILTER)
    lufs = profile.get("lufs", _LUFS)
    tp = profile.get("tp", _TP)
    lra = profile.get("lra", _LRA)

    filter_chain = f"{pre_filter},loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json"
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


def loudnorm_pass2(input_file: Path, output_file: Path, stats: dict, profile: dict = None):
    """
    Pass 2: apply linear loudness normalization using the measurements from pass 1.

    Output is mono 44.1 kHz mp3 at 96 kbps — suitable for spoken-word podcast
    distribution (small file, full vocal clarity).

    profile: optional dict of overrides — see module docstring. Must match
             whatever was passed to the loudnorm_pass1() call that produced
             `stats`. fade_out_seconds, if set, is applied here (not in
             pass 1 — see module docstring).
    """
    profile = profile or {}
    pre_filter = profile.get("pre_filter", _PRE_FILTER)
    lufs = profile.get("lufs", _LUFS)
    tp = profile.get("tp", _TP)
    lra = profile.get("lra", _LRA)

    ln = (
        f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:print_format=summary"
    )
    fade = _fade_out_filter(input_file, profile.get("fade_out_seconds"))
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-af", f"{pre_filter},{ln}{fade}",
        "-ar", "44100",    # sample rate
        "-ac", "1",        # mono (podcast standard)
        "-b:a", "96k",     # bitrate — fine for speech; bump to 128k for music
        str(output_file),
    ]
    _run(cmd)


def single_pass(input_file: Path, output_file: Path, profile: dict = None):
    """
    Single-pass normalization — faster but uses ffmpeg's internal estimate
    rather than a measured target.  Use for quick A/B comparisons only
    (--test-audio mode); always use the two-pass version for production.

    profile: optional dict of overrides — see module docstring.
    """
    profile = profile or {}
    pre_filter = profile.get("pre_filter", _PRE_FILTER)
    lufs = profile.get("lufs", _LUFS)
    tp = profile.get("tp", _TP)
    lra = profile.get("lra", _LRA)

    fade = _fade_out_filter(input_file, profile.get("fade_out_seconds"))
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-af", f"{pre_filter},loudnorm=I={lufs}:TP={tp}:LRA={lra}{fade}",
        "-ar", "44100",
        "-ac", "1",
        "-b:a", "96k",
        str(output_file),
    ]
    _run(cmd)
