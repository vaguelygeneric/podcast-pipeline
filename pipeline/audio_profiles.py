"""
pipeline/audio_profiles.py — Per-show audio processing profile dispatch.

Loads audio_profiles.json (repo root) and routes each show to the right
audio pipeline instead of branching on show name in run.py:

  "single_channel"   -> pipeline.audio         existing mono two-pass loudnorm
                                                 path (Daily's field recordings)
  "dual_lav_stereo"  -> pipeline.audio_stereo  per-channel loudness leveling +
                                                 mixdown, for two-lav-mic
                                                 conversational recordings
                                                 (VotR)

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


def process(input_file: Path, output_file: Path, show: str):
    """
    Run whichever audio pipeline `show`'s profile specifies.

    This is the single entry point run.py's Stage 1 calls. Adding a new
    show/mode combination should only ever require an audio_profiles.json
    entry -- not a change here or in run.py.
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

    else:
        raise ValueError(f"Unknown audio profile mode: {mode!r} (show={show!r})")
