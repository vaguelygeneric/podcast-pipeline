"""
pipeline/audio_profiles.py — Named audio processing profile dispatch.

Routes each episode run to the right audio pipeline based on a *named*
profile (looked up from config/defaults.json's "audio_profiles" registry)
instead of branching on show name in run.py:

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

Profiles are named independently of show slug (config/defaults.json's
"audio_profiles" registry) precisely so a one-off/test show can reuse an
existing profile (e.g. "daily") without needing its own audio_profiles
entry. Each show's standing choice of profile — if it has one — lives in
that show's bundle under "shows"[show]["audio_profile"], which run.py
resolves before calling into this module; a show with no standing choice
just gets prompted each run (see prompt_choice() in run.py).

NOTE: "single_channel" mode currently ignores the lufs/tp/lra fields in the
profile and just calls pipeline.audio's existing hardcoded constants
(-16 LUFS / -1.5 TP / 11 LRA) -- those fields are there for when/if that
path gets parameterized the same way audio_stereo's did. They're set to
match the current constants so there's no discrepancy either way.
"""

from pathlib import Path


def get_profile(defaults: dict, profile_name: str) -> dict:
    """
    Look up a named audio profile from the merged config (as returned by
    run.py's load_defaults()). Raises KeyError with the list of valid
    names if profile_name isn't registered -- config/defaults.json is
    hand-edited, so a typo there should fail loudly and early rather than
    silently falling back to the wrong loudness targets.
    """

    profiles = defaults.get("audio_profiles", {})

    if profile_name not in profiles:
        raise KeyError(
            f"Unknown audio profile {profile_name!r} — available: "
            f"{sorted(profiles)} (see \"audio_profiles\" in config/defaults.json)"
        )

    return profiles[profile_name]


def process(
    input_file: Path,
    output_file: Path,
    profile: dict,
    profile_name: str = "?",
    input_file2: Path = None,
):
    """
    Run whichever audio pipeline `profile` specifies.

    This is the single entry point run.py's Stage 1 calls. Adding a new
    mode combination should only ever require a config/defaults.json
    "audio_profiles" entry -- not a change here or in run.py.

    profile_name is used only for error messages -- pass the name you
    looked profile up with via get_profile() so failures are easy to trace
    back to a specific config/defaults.json entry.

    input_file2 is only used (and required) for "dual_mono_files" mode --
    it's each speaker's own separately-recorded mono file. It's ignored for
    every other mode.
    """

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
                f"audio profile {profile_name!r} is configured for "
                "dual_mono_files mode, which needs a second speaker's "
                "file -- pass it with --input2"
            )
        from pipeline.audio_stereo import process_dual_mono_files
        process_dual_mono_files(input_file, input_file2, output_file, profile=profile)

    else:
        raise ValueError(
            f"Unknown audio profile mode: {mode!r} (profile={profile_name!r})"
        )
