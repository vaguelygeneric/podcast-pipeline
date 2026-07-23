"""
pipeline/audio_profiles.py — Per-show audio processing profile dispatch.

Loads audio_profiles.json (repo root) and routes each show to the right
audio pipeline instead of branching on show name in run.py:

  "single_channel"   -> pipeline.audio         existing mono two-pass loudnorm
                                                 path (Daily's field recordings)
  "dual_lav_stereo"  -> pipeline.audio_stereo  per-channel loudness leveling +
                                                 mixdown, one stereo file, two
                                                 lav mics (VotR)
  "dual_mono_files"  -> pipeline.audio_stereo  same leveling + mixdown, but for
                                                 two separate mono files -- each
                                                 speaker recording locally on
                                                 their own device (remote/
                                                 video-call podcasts)

A show not listed in audio_profiles.json falls back to whatever "_default"
points at, so anything not yet configured behaves exactly as it did before
this module existed.

NOTE: "single_channel" mode currently ignores the lufs/tp/lra fields in the
profile and just calls pipeline.audio's existing hardcoded constants
(-16 LUFS / -1.5 TP / 11 LRA) -- those fields are there for when/if that
path gets parameterized the same way audio_stereo's did. They're set to
match the current constants so there's no discrepancy either way.
"""

import json
from pathlib import Path

_PROFILES_PATH = Path(__file__).parent.parent / "audio_profiles.json"


def _load_profiles() -> dict:
    with open(_PROFILES_PATH) as f:
        return json.load(f)


def get_profile(show: str) -> dict:
    """Look up the audio profile for a show slug, falling back to _default."""
    profiles = _load_profiles()
    default_key = profiles.get("_default", "daily")
    return profiles.get(show, profiles[default_key])


def process(input_file: Path, output_file: Path, show: str, input_file2: Path = None):
    """
    Run whichever audio pipeline `show`'s profile specifies.

    This is the single entry point run.py's Stage 1 calls. Adding a new
    show/mode combination should only ever require an audio_profiles.json
    entry -- not a change here or in run.py.

    input_file2 is only used (and required) for "dual_mono_files" mode --
    it's each speaker's own separately-recorded mono file. It's ignored for
    every other mode.
    """
    profile = get_profile(show)
    mode = profile.get("mode", "single_channel")

    if mode == "single_channel":
        from pipeline.audio import loudnorm_pass1, loudnorm_pass2
        stats = loudnorm_pass1(input_file)
        loudnorm_pass2(input_file, output_file, stats)

    elif mode == "dual_lav_stereo":
        from pipeline.audio_stereo import process_conversation_audio
        process_conversation_audio(input_file, output_file, profile=profile)

    elif mode == "dual_mono_files":
        if input_file2 is None:
            raise ValueError(
                f"show={show!r} is configured for dual_mono_files mode, "
                "which needs a second speaker's file -- pass it with --input2"
            )
        from pipeline.audio_stereo import process_dual_mono_files
        process_dual_mono_files(input_file, input_file2, output_file, profile=profile)

    else:
        raise ValueError(f"Unknown audio profile mode: {mode!r} (show={show!r})")
