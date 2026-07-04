#!/usr/bin/env python3
"""
manage_history.py — Small maintenance tool for .files/history.json.

Episode numbers are reserved the moment run.py starts processing an
episode, and per the pipeline's own design, reserved numbers are NEVER
reused — even if that run turns out to be a mistake (wrong file, wrong
show, duplicate retry, etc). That's the right call for archive.org
identifiers, but it does mean a bad reservation just sits there in
history.json looking like an unexplained gap.

This tool lets you annotate a bad reservation as cancelled, so future
readers of history.json (including you, six months from now) see it was
a deliberate cleanup rather than a mystery.

IMPORTANT: this tool only edits history.json. It does NOT touch
Internet Archive or Buzzsprout — if the episode was already uploaded,
clean that up manually on the platform first.

Usage:

    # Mark daily episode 38 as cancelled
    python3 manage_history.py cancel daily 38
"""

import argparse
import json
import sys

from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILE = Path(".files/history.json")


def load_history():
    if not HISTORY_FILE.exists():
        return {}

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history):
    HISTORY_FILE.parent.mkdir(exist_ok=True)

    tmp_file = HISTORY_FILE.with_suffix(".tmp")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    tmp_file.replace(HISTORY_FILE)


def cancel(show: str, ep: int):
    history = load_history()
    show_history = history.get(show)

    if not show_history:
        sys.exit(f"No history found for show '{show}'.")

    matches = [entry for entry in show_history if entry["ep"] == ep]

    if not matches:
        sys.exit(f"No entry found for {show} #{ep:04d}.")

    # If a run was retried, there may be more than one entry for the same
    # ep (shouldn't normally happen, but be defensive) — take the most
    # recent.
    entry = matches[-1]

    if entry["status"] == "cancelled":
        print(f"{show} #{ep:04d} is already marked cancelled. Nothing to do.")
        return

    if entry["status"] == "completed":
        response = input(
            f"{show} #{ep:04d} is marked 'completed', not just 'reserved'. "
            f"Cancel anyway? [y/N]: "
        ).strip().lower()

        if response not in ("y", "yes"):
            print("Aborted.")
            return

    entry["status"] = "cancelled"
    entry["cancelled_at"] = datetime.now(timezone.utc).isoformat()

    save_history(history)

    print(f"Marked {show} #{ep:04d} as cancelled.")
    print("Note: the episode number itself is still never reused — this only documents the gap.")
    print("If this episode was already uploaded to Archive.org/Buzzsprout, clean that up manually.")


def parse_args():
    p = argparse.ArgumentParser(
        description="Maintenance tool for .files/history.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    sub = p.add_subparsers(dest="command", required=True)

    cancel_p = sub.add_parser(
        "cancel",
        help="Mark a reserved/completed episode entry as cancelled"
    )
    cancel_p.add_argument("show", help="Show slug (e.g. 'daily')")
    cancel_p.add_argument("ep", type=int, help="Episode number")

    return p.parse_args()


def main():
    args = parse_args()

    if args.command == "cancel":
        cancel(args.show, args.ep)


if __name__ == "__main__":
    main()
