#!/usr/bin/env python3
"""
run.py — Single entry point for the podcast pipeline.

Handles the full publish workflow in sequence:
  1. Audio cleanup & loudness normalization  (m4a → mp3)
  2. Video generation                        (mp3 → mp4)
  3. Jekyll markdown page                    (metadata → .md)
  4. Platform uploads                        (Internet Archive, Buzzsprout)

RESILIENCE IMPROVEMENTS:
  ✓ Graceful error handling: API failures don't crash the pipeline
  ✓ Failed uploads are logged to .failures/ for manual recovery
  ✓ Uploads are optional: --no-upload skips them entirely
  ✓ Circuit breakers prevent cascading failures
  ✓ Retry logic with exponential backoff for transient failures
  ✓ Clear progress reporting with success/failure summaries

Run from the project root. All paths are relative to here.

Usage examples are in README.md, but quick reference:

  # Interactive mode — omit --show/--desc and just confirm the prompts
  python run.py input.m4a

  # Full production run, no prompts (all fields given explicitly)
  python run.py episode.m4a --ep 42 --show mypodcast --title "My Title" --desc "..." --video

  # Audio only, no upload
  python run.py episode.m4a --ep 42 --show mypodcast --desc "..." --no-upload

  # Generate video from an already-processed mp3
  python run.py output/mypodcast_ep0042.mp3 --ep 42 --show mypodcast --desc "..." --no-audio --no-upload --video

NOTE: video generation is OFF by default — pass --video to enable it.

INTERACTIVE PROMPTS:

  Any of --show, --desc, --video/--no-video, --archive/--no-archive,
  --buzzsprout/--no-buzzsprout, --upload/--no-upload, --jekyll/--no-jekyll,
  --audio-profile, and --publish-date that are NOT given explicitly on the
  command line will be prompted for interactively, pre-filled with sensible
  defaults (see config/defaults.json below) so the common case is just
  hitting Enter repeatedly. Passing a flag explicitly always skips its
  prompt.

CONFIG FILE:

  config/defaults.json holds your standing preferences (e.g. which show you
  publish most often, whether video/archive/buzzsprout/upload/jekyll should
  default on or off) and the named audio-profile registry that used to live
  in audio_profiles.json. It's created automatically with sensible defaults
  on first run, and can be hand-edited afterward. Unlike the old
  .files/defaults.json, this file is tracked in git — it's project
  configuration, not personal/local state.

───────────────────────────────────────────────────────────────────────────────
EPISODE HISTORY / AUTO-NUMBERING SYSTEM
───────────────────────────────────────────────────────────────────────────────

This pipeline maintains a persistent history file at:

    .files/history.json

The history file is used to:

  • Automatically assign episode numbers
  • Prevent accidental duplicate uploads
  • Detect skipped or out-of-order episodes
  • Preserve archive.org identifiers for future recovery/debugging

WHY THIS EXISTS:

A common failure mode is accidentally uploading the wrong episode number,
which creates a permanent archive.org identifier such as:

    daily_ep0019

Even if the upload is deleted later, the identifier may remain reserved,
causing future uploads to fail or require manual intervention.

To avoid this:

  • Episode numbers are auto-assigned by default
  • Episode numbers are RESERVED immediately
  • Reserved numbers are NEVER reused
  • Manual overrides require confirmation if unexpected

IDs are cheap, permanent, and should never be recycled.

NORMAL USAGE:

    python run.py input.m4a --show daily --desc "..."

MANUAL OVERRIDE:

    python run.py input.m4a --show daily --ep 42 --desc "..."

If a manual override does not match the expected next episode,
the script warns and requires confirmation.
"""

import argparse
import json
import logging
import os
import re
import sys
import uuid

from datetime import datetime, timezone, date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Theme default imports.
from video.src.palette import list_themes, DEFAULT_THEME, DEFAULT_MODE

from video.src.renderer import (
    DEFAULT_RING_SCALE, DEFAULT_N_BARS,
    DEFAULT_BAR_HEIGHT, DEFAULT_N_SPARKS, DEFAULT_GLOW_BLUR,
)

# Needed early (before stages run) to compute publish-date defaults.
from pipeline.publish import parse_date_from_filename

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("podcast_pipeline")

# ──────────────────────────────────────────────────────────────────────────────
# History / Episode Tracking
# ──────────────────────────────────────────────────────────────────────────────

HISTORY_FILE = Path(".files/history.json")

# ──────────────────────────────────────────────────────────────────────────────
# Config file (standing preferences for interactive prompts, plus the named
# audio-profile registry)
# ──────────────────────────────────────────────────────────────────────────────

# Lives under config/ rather than the gitignored .files/ — unlike history.json
# (a personal run log) this is show/profile configuration worth tracking and
# reviewing in git, the same way audio_profiles.json used to be before it was
# merged in here.
DEFAULTS_FILE = Path("config/defaults.json")

DEFAULT_DEFAULTS = {
    "show": "daily",
    "video": False,
    "archive": True,
    "buzzsprout": False,
    # Off by default — this is the bootstrap value for a brand-new show
    # with no "shows" bundle of its own yet. An established show (with its
    # own bundle entry) sets this explicitly and isn't affected.
    "upload": False,
    "jekyll": True,
    "test_upload": False,
    "jekyll_site_repo": "../website",
    # Named audio profiles (formerly audio_profiles.json at the repo root).
    # Keyed by profile name -- NOT show slug, so a one-off/test show can
    # reuse an existing profile (e.g. "daily") by name. See
    # pipeline/audio_profiles.py for what each "mode" does.
    "audio_profiles": {},
    # Which named profile above to suggest when a show has no standing
    # choice of its own (mirrors audio_profiles.json's old "_default").
    "default_audio_profile": "daily",
    # Per-show bundles. Keyed by show slug; any subset of the prompted
    # fields above (video/upload/archive/buzzsprout/jekyll/audio_profile)
    # can be given. When a run's --show matches a key here, and none of
    # those fields were passed explicitly on the CLI, the user is offered
    # a single "use these defaults?" prompt instead of one prompt per field.
    # "audio_profile" here is a NAME referencing "audio_profiles" above,
    # not an inline profile definition.
    "shows": {},
}


def load_defaults(show: str):
    """
    Load standing preferences for a given show.

    Global fields at the top level of defaults.json act as the fallback;
    a per-show block under "shows"[show] (if present) overrides them.
    Since the caller already knows which show it's running, resolution
    happens here rather than handing back the whole file (including every
    other show's bundle) for the caller to pick apart.

    Returns (resolved, show_bundle):
      - resolved: a single flat dict — the value to pre-fill each
        interactive prompt with, global fields overridden by this show's
        bundle where present (including "jekyll_site_repo" and "audio_profile").
        Also carries the full "audio_profiles" registry through unchanged,
        for pipeline.audio_profiles.get_profile() to look names up in.
      - show_bundle: the raw per-show dict as written in defaults.json
        (or {} if this show has no entry yet). Used only to decide
        whether to offer the "use these defaults?" bundle prompt and to
        display what it contains.

    Created automatically with DEFAULT_DEFAULTS on first run if missing.
    Hand-editable afterward — same spirit as history.json. Any keys missing
    from the file (e.g. after an upgrade adds a new prompted field) fall
    back to DEFAULT_DEFAULTS rather than erroring.
    """

    DEFAULTS_FILE.parent.mkdir(exist_ok=True)

    if not DEFAULTS_FILE.exists():
        with open(DEFAULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DEFAULTS, f, indent=2)
        on_disk = {}
    else:
        try:
            with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load defaults file, using built-in defaults: {e}")
            on_disk = {}

    merged = dict(DEFAULT_DEFAULTS)
    merged.update(on_disk)

    show_bundle = merged.get("shows", {}).get(show, {})

    resolved = dict(merged)
    resolved.pop("shows", None)
    resolved.update(show_bundle)

    return resolved, show_bundle


# ──────────────────────────────────────────────────────────────────────────────
# Interactive prompts
# ──────────────────────────────────────────────────────────────────────────────

def prompt_str(label: str, default: str = None) -> str:
    """Prompt for a string; blank input accepts the default (if any)."""

    suffix = f" [{default}]" if default else ""

    while True:
        response = input(f"{label}{suffix}: ").strip()

        if response:
            return response

        if default is not None:
            return default

        print("This field is required.")


def prompt_yn(label: str, default: bool) -> bool:
    """Prompt for a yes/no toggle; blank input accepts the default."""

    hint = "Y/n" if default else "y/N"

    while True:
        response = input(f"{label} [{hint}]: ").strip().lower()

        if not response:
            return default

        if response in ("y", "yes"):
            return True

        if response in ("n", "no"):
            return False

        print("Please answer y or n.")


def env_vars_present(*names: str) -> bool:
    """Return True only if every named env var is set to a non-empty value."""

    return all(os.getenv(name) for name in names)


def prompt_choice(label: str, options: list, default: str) -> str:
    """
    Prompt for one of a fixed set of string choices; blank input accepts
    the default. Used for --audio-profile — free text would let a typo
    silently fall through to a KeyError deep in pipeline.audio_profiles.
    """

    listing = ", ".join(options)

    while True:
        response = input(f"{label} [{default}] (options: {listing}): ").strip()

        if not response:
            return default

        if response in options:
            return response

        print(f"Please choose one of: {listing}")


def prompt_date(label: str, default: "date") -> "date":
    """Prompt for a YYYY-MM-DD date; blank input accepts the default."""

    while True:
        response = input(f"{label} [{default.isoformat()}]: ").strip()

        if not response:
            return default

        try:
            return date.fromisoformat(response)
        except ValueError:
            print("Please enter a date as YYYY-MM-DD.")


# ──────────────────────────────────────────────────────────────────────────────
# Show slug safety
# ──────────────────────────────────────────────────────────────────────────────

# The convention every existing show follows ("daily", "vault-of-the-raw"):
# lowercase letters, digits, and single hyphens between words. Anything
# outside that breaks downstream in ways that don't fail loudly:
#   • archive.org identifiers (f"{show}_ep{ep:04d}") don't allow spaces —
#     the upload is rejected or the identifier gets silently mangled.
#   • the Jekyll permalink (/podcast/{show}/{ep}/) is a URL path segment —
#     a raw space there is invalid.
SHOW_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def slugify_show(raw: str) -> str:
    """
    Best-effort conversion of an arbitrary show name into the safe slug
    format: lowercase, non-alphanumeric runs collapsed to single hyphens,
    leading/trailing hyphens stripped. e.g. "Daily Temp!" -> "daily-temp".
    """

    slug = raw.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")

    return slug


def ensure_safe_show_slug(show: str) -> str:
    """
    Validate a show slug against SHOW_SLUG_RE; if it doesn't match, explain
    why (see module comment above) and offer to use the slugified version
    instead. Declining keeps the original value as-is — this only warns,
    it never aborts, since there may be a legitimate reason to proceed
    (e.g. deliberately probing what breaks).
    """

    if SHOW_SLUG_RE.match(show):
        return show

    slugified = slugify_show(show)

    logger.warning(
        f"\nShow slug {show!r} contains characters that break things "
        f"downstream — archive.org identifiers and the Jekyll permalink "
        f"URL don't allow spaces or most punctuation.\n"
    )

    if not slugified:
        sys.exit(f"Error: {show!r} has no usable characters once slugified.")

    if prompt_yn(f"Use {slugified!r} instead?", True):
        return slugified

    logger.warning(
        f"Continuing with {show!r} as-is — archive.org upload and/or the "
        f"Jekyll permalink may fail or produce broken output."
    )
    return show


# ──────────────────────────────────────────────────────────────────────────────
# Filename / episode-number sanity check
# ──────────────────────────────────────────────────────────────────────────────

def check_filename_episode_hint(input_path: Path, show: str, ep: int):
    """
    Cross-check the episode number against a hint embedded in the input
    filename, e.g. `daily_ep0036.mp3` or a renamed source file like
    `daily_ep0036_raw.m4a`.

    This exists because episode numbers are auto-assigned independent of
    the input file — a retry after a typo, or a renamed source file, can
    silently get assigned an episode number that doesn't match the content.
    If the filename carries an explicit episode hint that disagrees with
    the assigned/manual episode number, warn and require confirmation
    (same UX as validate_manual_episode).
    """

    match = re.search(rf"{re.escape(show)}_ep0*(\d+)", input_path.name)

    if not match:
        return

    hinted_ep = int(match.group(1))

    if hinted_ep == ep:
        return

    logger.warning(
        f"\n"
        f"Input filename suggests episode : {hinted_ep:04d}\n"
        f"Episode about to be assigned    : {ep:04d}\n"
    )

    response = input("Continue anyway? [y/N]: ").strip().lower()

    if response not in ("y", "yes"):
        logger.info("Aborted by user.")
        sys.exit(1)


def load_history():
    """
    Load the persistent history file.

    History structure:

    {
      "daily": [
        {
          "ep": 18,
          "status": "reserved",
          "timestamp": "...",
          ...
        }
      ]
    }

    We intentionally keep this simple JSON instead of using SQLite because:

      • single-user
      • append-only
      • tiny dataset
      • human-readable
      • easy to repair manually if needed

    If the file does not exist yet, return an empty dict.
    """

    HISTORY_FILE.parent.mkdir(exist_ok=True)

    if not HISTORY_FILE.exists():
        return {}

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        logger.warning(f"Failed to load history file: {e}")
        return {}


def save_history(history):
    """
    Atomically write history to disk.

    IMPORTANT:
    We write to a temporary file first and then replace the real file.

    This prevents corruption if:
      • the script crashes
      • power dies
      • Ctrl+C happens mid-write

    Atomic replace is much safer than writing directly to the target file.
    """

    HISTORY_FILE.parent.mkdir(exist_ok=True)

    tmp_file = HISTORY_FILE.with_suffix(".tmp")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    tmp_file.replace(HISTORY_FILE)


def get_show_history(show: str):
    """
    Convenience helper for retrieving a show's history list.
    """

    history = load_history()
    return history.get(show, [])


def get_next_episode(show: str) -> int:
    """
    Determine the next episode number for a show.

    IMPORTANT:
    We look at ALL reserved episodes, not just successful runs.

    This guarantees episode numbers are never reused.

    Example:

      latest reserved = 18
      next assigned   = 19

    Even if episode 18 failed halfway through,
    we still move forward to 19.
    """

    show_history = get_show_history(show)

    if not show_history:
        return 1

    latest = max(entry["ep"] for entry in show_history)

    return latest + 1


def get_next_publish_date(show: str, recording_date: datetime) -> "date":
    """
    Compute the default publish_date for a new episode.

    For front-loading (recording several episodes in one sitting), we want
    each one to iterate a day at a time rather than all landing on the same
    date. We only look at COMPLETED history entries (a failed/abandoned run
    shouldn't shift the schedule) and take the latest recorded publish_date
    + 1 day.

    If the show has no completed history yet, fall back to the recording
    date parsed from the input filename (unchanged prior behavior).
    """

    show_history = get_show_history(show)

    completed_dates = [
        entry["publish_date"]
        for entry in show_history
        if entry.get("status") == "completed" and entry.get("publish_date")
    ]

    if not completed_dates:
        return recording_date.date()

    latest = max(date.fromisoformat(d) for d in completed_dates)

    return latest + timedelta(days=1)


def validate_manual_episode(show: str, ep: int):
    """
    Validate a manually provided episode number.

    This only runs when the user explicitly passes --ep.

    If the provided episode does not match the expected next episode,
    the script warns and asks for confirmation.

    This protects against:
      • typos
      • skipped numbers
      • duplicate archive uploads
      • accidental regressions
    """

    expected = get_next_episode(show)

    if ep != expected:
        logger.warning(
            f"\n"
            f"Expected next episode : {expected:04d}\n"
            f"Provided episode      : {ep:04d}\n"
        )

        response = input("Continue anyway? [y/N]: ").strip().lower()

        if response not in ("y", "yes"):
            logger.info("Aborted by user.")
            sys.exit(1)


def reserve_episode(args):
    """
    Reserve an episode number IMMEDIATELY.

    We reserve BEFORE uploads happen.

    Because archive.org identifiers become effectively permanent.

    If we only saved successful runs, then:
      • a failed upload
      • a partial upload
      • a Ctrl+C
      • or a crash

    could accidentally reuse episode numbers later.

    Reusing episode numbers is dangerous.
    Skipping episode numbers is harmless for now.

    Therefore:
      • IDs are consumed permanently
      • gaps are acceptable
      • reuse is forbidden

    """

    history = load_history()

    show_history = history.setdefault(args.show, [])

    entry = {
        "run_id": str(uuid.uuid4()),
        "ep": args.ep,
        "status": "reserved",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "args": vars(args),
    }

    show_history.append(entry)

    show_history.sort(key=lambda x: x["ep"])

    save_history(history)

    logger.info(
        f"Reserved episode {args.show} #{args.ep:04d}"
    )


def finalize_episode(args, archive_identifier=None, publish_date=None):
    """
    Mark a reserved episode as completed.

    We update the latest matching reservation entry. `publish_date` (the
    date actually written into the episode's Jekyll front matter) is stored
    so future episodes for this show can iterate off of it — see
    get_next_publish_date().
    """

    history = load_history()

    show_history = history.get(args.show, [])

    for entry in reversed(show_history):

        if (
            entry["ep"] == args.ep and
            entry["status"] == "reserved"
        ):
            entry["status"] = "completed"
            entry["completed_at"] = datetime.now(timezone.utc).isoformat()
            entry["archive_identifier"] = archive_identifier
            if publish_date is not None:
                entry["publish_date"] = publish_date.isoformat()
            break

    save_history(history)


# ──────────────────────────────────────────────────────────────────────────────
# Args
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():

    p = argparse.ArgumentParser(
        description="Podcast publish pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Required
    p.add_argument(
        "input",
        help="Source audio file (.m4a preferred, .mp3 also accepted). "
             "For a show configured with dual_mono_files mode, this is "
             "speaker A's file and --input2 is required for speaker B's."
    )

    p.add_argument(
        "--input2",
        dest="input2",
        default=None,
        help="Speaker B's separately-recorded mono file. Only used (and "
             "required) when the resolved --audio-profile is in "
             "dual_mono_files mode."
    )

    p.add_argument(
        "--show",
        default=None,
        help="Show slug (e.g. 'daily'). Prompted interactively if omitted. "
             "Non-slug characters (spaces, punctuation, uppercase) trigger "
             "a prompt to auto-slugify."
    )

    p.add_argument(
        "--audio-profile",
        dest="audio_profile",
        default=None,
        help="Named audio profile from config/defaults.json's "
             "\"audio_profiles\" registry (e.g. 'daily', 'vault-of-the-raw'). "
             "Prompted interactively if omitted and the show has no "
             "standing choice of its own."
    )

    p.add_argument(
        "--desc",
        default=None,
        help="Episode description / show notes. Prompted interactively if omitted."
    )

    # Optional metadata
    p.add_argument(
        "--ep",
        type=int,
        help="Episode number (auto-generated if omitted)"
    )

    p.add_argument(
        "--title",
        help="Episode title (default: 'Episode NNNN')"
    )

    p.add_argument(
        "--publish-date",
        default=None,
        dest="publish_date",
        help=(
            "Release date (YYYY-MM-DD) for Jekyll front matter. Prompted "
            "interactively if omitted, defaulting to one day after the "
            "show's last completed episode (or the recording date, if "
            "this is the first episode)."
        )
    )

    p.add_argument(
        "--logo",
        default=".files/images/logo.png",
        help="Logo PNG for video overlay"
    )

    p.add_argument(
        "--jekyll-site-repo",
        dest="jekyll_site_repo",
        default=None,
        help=(
            "Path to the Jekyll site repo checkout, used to merge into a "
            "pre-written episode page instead of generating a plain "
            "new-file patch. Defaults to config/defaults.json's "
            "'jekyll_site_repo' value ('../website' out of the box), or a "
            "show-specific override under that show's \"shows\" bundle."
        )
    )

    # Stage toggles — default=None (not True/False) so we can tell whether
    # the person actually passed the flag vs. needs an interactive prompt.
    # Each supports both spellings, e.g. --video / --no-video.
    p.add_argument("--no-audio", action="store_true")
    p.add_argument(
        "--video", action=argparse.BooleanOptionalAction, default=None,
        help="Generate video (off by default — pass --video to enable)"
    )
    p.add_argument(
        "--upload", action=argparse.BooleanOptionalAction, default=None,
        help="Enable platform uploads (Archive/Buzzsprout gated separately below)"
    )
    p.add_argument(
        "--jekyll", action=argparse.BooleanOptionalAction, default=None,
        help="Generate the Jekyll markdown page"
    )

    # Uploads
    p.add_argument("--archive", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--buzzsprout", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--test-upload", action="store_true")

    # QA / Dev
    p.add_argument("--quick-video", action="store_true")

    # Video
    p.add_argument("--resolution", default="1280x720")
    p.add_argument("--fps", type=int, default=30)

    p.add_argument("--ring-scale", type=float, default=DEFAULT_RING_SCALE)
    p.add_argument("--n-bars", type=int, default=DEFAULT_N_BARS)
    p.add_argument("--bar-height", type=float, default=DEFAULT_BAR_HEIGHT)
    p.add_argument("--n-sparks", type=int, default=DEFAULT_N_SPARKS)
    p.add_argument("--glow-blur", type=int, default=DEFAULT_GLOW_BLUR)

    p.add_argument(
        "--style",
        default="v2",
        choices=["v1", "v2"]
    )

    p.add_argument("--watermark", default=None)
    p.add_argument("--watermark-opacity", type=float, default=0.35)
    p.add_argument("--watermark-size", type=float, default=0.08)
    p.add_argument("--watermark-margin", type=int, default=24)

    p.add_argument(
        "--theme",
        default=DEFAULT_THEME,
        help=f"Available: {', '.join(list_themes())}"
    )

    p.add_argument(
        "--mode",
        default=DEFAULT_MODE,
        choices=["dark", "light"]
    )

    return p.parse_args()


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():

    args = parse_args()

    # ── Resolve show first ────────────────────────────────────────────────
    # defaults.json (and any per-show "shows" bundle in it) is only loaded
    # once we know which show we're dealing with — no need to read it just
    # to prompt for the show slug itself.
    if args.show is None:
        args.show = prompt_str("Show slug", DEFAULT_DEFAULTS["show"])

    # Applies whether the slug came from the CLI or the prompt above — a
    # typo'd --show is just as capable of breaking archive.org/Jekyll as a
    # careless prompt answer.
    args.show = ensure_safe_show_slug(args.show)

    defaults, show_defaults = load_defaults(args.show)

    # ── Resolve remaining fields that support interactive prompting ──────────
    # Anything passed explicitly on the CLI skips its prompt.

    if args.desc is None:
        args.desc = prompt_str("Episode description")

    # ── Show-level default bundle ─────────────────────────────────────────
    # If defaults.json has a "shows" entry for this show, and none of the
    # fields it covers were already passed explicitly on the CLI, offer to
    # apply the whole bundle at once instead of prompting for each field.
    PROMPTED_FIELDS = ("video", "upload", "archive", "buzzsprout", "jekyll", "audio_profile")

    untouched = all(getattr(args, field) is None for field in PROMPTED_FIELDS)

    if show_defaults and untouched:
        print(f"\nDefaults detected for show '{args.show}':")
        for field in PROMPTED_FIELDS:
            if field in show_defaults:
                print(f"  {field}: {show_defaults[field]}")
        print()

        if prompt_yn("Use these defaults?", True):
            for field in PROMPTED_FIELDS:
                if field in show_defaults:
                    setattr(args, field, show_defaults[field])

            # Same credential gating as the per-field prompts below — never
            # silently attempt an upload we don't have credentials for.
            if args.archive and not env_vars_present("IA_ACCESS_KEY", "IA_SECRET_KEY"):
                args.archive = False
                logger.info(
                    "[SKIP] IA_ACCESS_KEY/IA_SECRET_KEY not set — "
                    "forcing archive=False despite show defaults"
                )

            if args.buzzsprout and not env_vars_present("BUZZSPROUT_API_TOKEN", "BUZZSPROUT_PODCAST_ID"):
                args.buzzsprout = False
                logger.info(
                    "[SKIP] BUZZSPROUT_API_TOKEN/BUZZSPROUT_PODCAST_ID not set — "
                    "forcing buzzsprout=False despite show defaults"
                )

            print("Using the following settings:")
            for field in PROMPTED_FIELDS:
                print(f"  {field}: {getattr(args, field)}")
            print()

    if args.audio_profile is None and not args.no_audio:
        available_profiles = sorted(defaults.get("audio_profiles", {}).keys())

        if not available_profiles:
            sys.exit(
                "Error: no audio profiles registered — add at least one "
                "entry under \"audio_profiles\" in config/defaults.json."
            )

        default_profile = defaults.get("default_audio_profile", available_profiles[0])
        if default_profile not in available_profiles:
            default_profile = available_profiles[0]

        args.audio_profile = prompt_choice(
            "Audio profile", available_profiles, default_profile
        )

    if args.video is None:
        args.video = prompt_yn("Generate video?", defaults["video"])

    if args.upload is None:
        args.upload = prompt_yn("Enable platform uploads?", defaults["upload"])

    if args.upload:
        if args.archive is None:
            if env_vars_present("IA_ACCESS_KEY", "IA_SECRET_KEY"):
                args.archive = prompt_yn("Upload to Internet Archive?", defaults["archive"])
            else:
                args.archive = False
                logger.info(
                    "[SKIP] IA_ACCESS_KEY/IA_SECRET_KEY not set — skipping "
                    "Internet Archive upload prompt"
                )

        if args.buzzsprout is None:
            if env_vars_present("BUZZSPROUT_API_TOKEN", "BUZZSPROUT_PODCAST_ID"):
                args.buzzsprout = prompt_yn("Upload to Buzzsprout?", defaults["buzzsprout"])
            else:
                args.buzzsprout = False
                logger.info(
                    "[SKIP] BUZZSPROUT_API_TOKEN/BUZZSPROUT_PODCAST_ID not set — "
                    "skipping Buzzsprout upload prompt"
                )

    if args.jekyll is None:
        args.jekyll = prompt_yn("Generate Jekyll page?", defaults["jekyll"])

    # Validate the input file BEFORE reserving an episode number — a typo'd
    # or missing path should never burn a reservation.
    input_path = Path(args.input)

    if not input_path.exists():
        sys.exit(f"Error: input file not found: {input_path}")

    input2_path = None
    if args.input2 is not None:
        input2_path = Path(args.input2)
        if not input2_path.exists():
            sys.exit(f"Error: --input2 file not found: {input2_path}")

    # If the resolved audio profile needs a second file, fail before
    # reserving an episode rather than partway through Stage 1.
    if not args.no_audio:
        from pipeline.audio_profiles import get_profile
        audio_profile = get_profile(defaults, args.audio_profile)
        if audio_profile.get("mode") == "dual_mono_files" and input2_path is None:
            sys.exit(
                f"Error: audio profile '{args.audio_profile}' is configured "
                "for dual_mono_files mode — pass speaker B's file with --input2"
            )

    # Auto-assign episode number if omitted.
    if args.ep is None:

        args.ep = get_next_episode(args.show)

        logger.info(
            f"Auto-assigned episode: {args.show} #{args.ep:04d}"
        )

    else:
        validate_manual_episode(args.show, args.ep)

    # Catch cases where the input filename hints at a different episode
    # number than the one about to be assigned/reserved (e.g. a typo'd
    # retry, or a renamed source file).
    check_filename_episode_hint(input_path, args.show, args.ep)

    # Recording date drives both the publish-date fallback and the
    # existing archive/Jekyll metadata.
    recording_date = parse_date_from_filename(str(input_path))

    if args.publish_date is None:
        default_publish_date = get_next_publish_date(args.show, recording_date)
        publish_date = prompt_date("Publish date", default_publish_date)
    else:
        publish_date = date.fromisoformat(args.publish_date)

    # Reserve immediately so episode numbers are never reused.
    reserve_episode(args)

    ep_str = f"{args.ep:04d}"

    base_name = f"{args.show}_ep{ep_str}"

    title = args.title or f"Episode {ep_str}"

    Path("output").mkdir(exist_ok=True)
    Path("temp").mkdir(exist_ok=True)

    # Track results for final summary
    results = {
        "episode": args.ep,
        "show": args.show,
        "audio": None,
        "video": None,
        "jekyll": None,
        "archive": None,
        "buzzsprout": None,
        "failures": [],
    }

    # ── Stage 1: Audio ────────────────────────────────────────────────────────
    if args.no_audio:
        # Audio stage skipped — input must already be a clean mp3
        final_audio = input_path
        logger.info(f"[SKIP] Audio processing — using existing file: {final_audio}")
        results["audio"] = "skipped"
    else:
        try:
            from pipeline.audio_profiles import process as process_audio
            logger.info("\n=== Stage 1: Audio Processing ===")
            logger.info(f"Using audio profile: {args.audio_profile}")
            final_audio = Path(f"output/{base_name}.mp3")
            process_audio(
                input_path,
                final_audio,
                profile=audio_profile,
                profile_name=args.audio_profile,
                input_file2=input2_path,
            )
            results["audio"] = "success"
            logger.info(f"[OK] Audio: {final_audio}")
        except Exception as e:
            logger.error(f"[FAIL] Audio processing failed: {e}")
            results["audio"] = f"error: {e}"
            results["failures"].append(("audio", str(e)))
            sys.exit(f"Audio processing is required and failed. Cannot continue.")

    # ── Stage 2: Video ────────────────────────────────────────────────────────
    if args.video:
        try:
            from pipeline.video import build_video
            logger.info("\n=== Stage 2: Video Generation ===")
            build_video(            
                mp3_path          = final_audio,
                logo_path         = Path(args.logo),
                quick             = args.quick_video,
                ring_scale        = args.ring_scale,
                resolution        = args.resolution,
                style             = args.style,
                n_bars            = args.n_bars,
                bar_height        = args.bar_height,
                n_sparks          = args.n_sparks,
                glow_blur         = args.glow_blur,
                watermark_path    = Path(args.watermark) if args.watermark else None,
                watermark_opacity = args.watermark_opacity,
                watermark_size    = args.watermark_size,
                fps               = args.fps,
                theme             = args.theme,
                mode              = args.mode,
                watermark_margin  = args.watermark_margin,
            )
            results["video"] = "success"
            logger.info(f"[OK] Video: output/{Path(final_audio).stem}.mp4")
        except Exception as e:
            logger.warning(f"[FAIL] Video generation failed: {e}")
            results["video"] = f"error: {e}"
            results["failures"].append(("video", str(e)))
            logger.info("Continuing without video (optional stage)")
    else:
        logger.info("[SKIP] Video generation")
        results["video"] = "skipped"

    # ── Stage 3: Metadata & Jekyll page ──────────────────────────────────────
    try:
        from pipeline.publish import get_duration, get_file_size, generate_markdown
        logger.info("\n=== Stage 3: Metadata ===")
        duration = get_duration(final_audio)
        size     = get_file_size(final_audio)
        logger.info(f"[OK] Metadata: duration={duration}, size={size} bytes")
    except Exception as e:
        logger.error(f"[FAIL] Metadata extraction failed: {e}")
        results["metadata"] = f"error: {e}"
        results["failures"].append(("metadata", str(e)))
        sys.exit(f"Metadata extraction is required and failed. Cannot continue.")

    # ── Stage 4: Platform uploads ─────────────────────────────────────────────
    archive_identifier = None
    if args.upload:
        logger.info("\n=== Stage 4: Platform Uploads ===")
        upload_description = f"{args.desc} - https://vaguelygeneric.website/podcast/{args.show}/{args.ep:04d}/"

        if args.archive:
            try:
                from pipeline.publish import upload_to_archive
                logger.info("Uploading to Internet Archive…")
                archive_identifier = upload_to_archive(
                    file        = final_audio,
                    ep_num      = args.ep,
                    title       = title,
                    description = upload_description,
                    date        = recording_date,
                    show        = args.show,
                    test        = args.test_upload,
                )
                if archive_identifier:
                    results["archive"] = "success"
                    logger.info(f"[OK] Internet Archive: {archive_identifier}")
                else:
                    results["archive"] = "failed (logged for retry)"
                    results["failures"].append(("archive.org", "upload failed"))
                    logger.warning("[RETRY] Internet Archive upload failed; logged to .failures/ for manual retry")
            except Exception as e:
                logger.error(f"[FAIL] Internet Archive upload error: {e}")
                results["archive"] = f"error: {e}"
                results["failures"].append(("archive.org", str(e)))

        if args.buzzsprout:
            try:
                from pipeline.publish import upload_to_buzzsprout
                logger.info("Uploading to Buzzsprout…")
                result = upload_to_buzzsprout(
                    file        = final_audio,
                    title       = title,
                    description = upload_description,
                    date        = recording_date,
                    ep_num      = args.ep,
                )
                if result:
                    results["buzzsprout"] = "success"
                    logger.info(f"[OK] Buzzsprout: episode {result.get('id')}")
                else:
                    results["buzzsprout"] = "failed (logged for retry)"
                    results["failures"].append(("buzzsprout", "upload failed"))
                    logger.warning("[RETRY] Buzzsprout upload failed; logged to .failures/ for manual retry")
            except Exception as e:
                logger.error(f"[FAIL] Buzzsprout upload error: {e}")
                results["buzzsprout"] = f"error: {e}"
                results["failures"].append(("buzzsprout", str(e)))
    else:
        logger.info("[SKIP] Platform uploads")
        results["archive"] = "skipped"
        results["buzzsprout"] = "skipped"

    # ── Generate Jekyll page ──────────────────────────────────────────────────
    website_patch_path = None

    if args.jekyll:
        try:
            from pipeline.publish import generate_website_patch
            md_path = generate_markdown(
                ep           = args.ep,
                show         = args.show,
                title        = title,
                description  = args.desc,
                duration     = duration,
                audio_size   = size,
                date         = recording_date,
                publish_date = publish_date,
                identifier   = archive_identifier,
            )
            results["jekyll"] = "success"
            logger.info(f"[OK] Jekyll page: {md_path}")

            website_patch_path = generate_website_patch(
                md_path, args.show, ep_str,
                jekyll_site_repo=args.jekyll_site_repo or defaults["jekyll_site_repo"],
            )
            logger.info(f"[OK] Website patch: {website_patch_path}")
        except Exception as e:
            logger.error(f"[FAIL] Jekyll page generation failed: {e}")
            results["jekyll"] = f"error: {e}"
            results["failures"].append(("jekyll", str(e)))
    else:
        logger.info("[SKIP] Jekyll page generation")
        md_path = None
        results["jekyll"] = "skipped"

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("\n" + "="*70)
    logger.info("PIPELINE SUMMARY")
    logger.info("="*70)
    logger.info(f"Episode: {args.show} #{args.ep:04d}")
    logger.info(f"  Audio    : {results['audio']}")
    logger.info(f"  Video    : {results['video']}")
    logger.info(f"  Metadata : {results.get('metadata', 'success')}")
    logger.info(f"  Jekyll   : {results['jekyll']}")
    logger.info(f"  Archive  : {results['archive']}")
    logger.info(f"  Buzzsprout: {results['buzzsprout']}")
    logger.info(f"  Publish date: {publish_date.isoformat()}")

    if results["failures"]:
        logger.warning(f"\n{len(results['failures'])} operation(s) failed. Check .failures/ directory for details.")
        logger.info("To retry failed uploads, run: python retry_failed.py")

    logger.info(f"\nFiles generated:")
    logger.info(f"  Audio : {final_audio}")
    logger.info(f"  Jekyll: {md_path}")
    if website_patch_path:
        logger.info(f"  Patch : {website_patch_path}")
    if args.video and results["video"] == "success":
        logger.info(f"  Video : output/{Path(final_audio).stem}.mp4")

    logger.info("="*70)

    # Exit with success if core stages completed
    if results["audio"] and results["jekyll"]:
        # Mark this reservation as completed and record the publish_date
        # actually used, so the next front-loaded episode for this show
        # can iterate off of it (see get_next_publish_date()).
        finalize_episode(args, archive_identifier=archive_identifier, publish_date=publish_date)
        logger.info("\n✓ Pipeline completed (core stages)")
        sys.exit(0)
    else:
        logger.error("\n✗ Pipeline failed (required stage failed)")
        sys.exit(1)


if __name__ == "__main__":
    main()