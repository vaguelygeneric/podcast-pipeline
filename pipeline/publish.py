"""
pipeline/publish.py — Metadata helpers, platform uploads, and Jekyll page generation.

Responsibilities:
  - Parse recording date from filename (for archive metadata and Jekyll front matter)
  - Probe duration and file size from a finished mp3
  - Upload to Internet Archive (primary audio host)
  - Upload to Buzzsprout (podcast RSS host)
  - Generate a Jekyll episode page as a .md file under output/_podcast/

Credentials are read from environment variables (loaded from .env by run.py):
  IA_ACCESS_KEY, IA_SECRET_KEY       — Internet Archive
  BUZZSPROUT_API_TOKEN               — Buzzsprout API token
  BUZZSPROUT_PODCAST_ID              — Buzzsprout numeric podcast ID
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date_from_filename(filename: str) -> datetime:
    """
    Extract recording date/time from filenames like 20240420_143022_episode.m4a.
    Falls back to today's date if the pattern isn't found.
    """
    base  = os.path.basename(filename)
    match = re.match(r'(\d{8})_(\d{6})', base)
    if not match:
        return datetime.today()
    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")


def get_duration(file: Path) -> str:
    """Return a human-readable duration string ("MM:SS") using ffprobe."""
    cmd = [
        "ffprobe", "-i", str(file),
        "-show_entries", "format=duration",
        "-v", "quiet",
        "-of", "csv=p=0",
    ]
    result = subprocess.check_output(cmd).decode().strip()
    seconds = int(float(result))
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def get_file_size(file: Path) -> int:
    """Return file size in bytes (used in RSS enclosure tags)."""
    return os.path.getsize(file)


# ── Internet Archive ──────────────────────────────────────────────────────────

def upload_to_archive(
    file:        Path,
    ep_num:      int,
    title:       str,
    description: str,
    date:        datetime,
    show:        str,
    test:        bool = False,
) -> str:
    """
    Upload the mp3 to Internet Archive and return the item identifier.

    test=True prefixes the identifier with 'test_' and marks the title
    with [TEST] so it can be identified and cleaned up after QA.
    """
    from internetarchive import upload

    access_key = os.getenv("IA_ACCESS_KEY")
    secret_key = os.getenv("IA_SECRET_KEY")
    if not access_key or not secret_key:
        raise RuntimeError("Missing IA credentials — check .env for IA_ACCESS_KEY / IA_SECRET_KEY")

    identifier = f"{show}_ep{ep_num:04d}"
    if test:
        identifier = f"test_{identifier}"

    metadata = {
        "title":       f"{'[TEST] ' if test else ''}{show.capitalize()} – {title}",
        "creator":     "Vaguely Generic",
        "mediatype":   "audio",
        "collection":  "opensource_audio",
        "date":        str(date.date()),
        "description": description,
        "subject":     ["podcast", show, "vaguely generic"],
        "series":      show.capitalize(),
    }

    print(f"\n=== Uploading to Internet Archive: {identifier} ===")
    upload(
        identifier,
        files       = [str(file)],
        metadata    = metadata,
        access_key  = access_key,
        secret_key  = secret_key,
    )
    return identifier


# ── Buzzsprout ────────────────────────────────────────────────────────────────

def upload_to_buzzsprout(
    file:        Path,
    title:       str,
    description: str,
    date:        datetime,
    ep_num:      int,
) -> dict:
    """
    Upload the episode mp3 to Buzzsprout via their REST API.
    Returns the API response JSON dict on success.
    """
    import requests

    api_token  = os.getenv("BUZZSPROUT_API_TOKEN")
    podcast_id = os.getenv("BUZZSPROUT_PODCAST_ID")
    if not api_token or not podcast_id:
        raise RuntimeError("Missing Buzzsprout credentials — check .env")

    url     = f"https://www.buzzsprout.com/api/{podcast_id}/episodes.json"
    headers = {"Authorization": f"Token token={api_token}"}
    data    = {
        "title":          title,
        "description":    description,
        "published_at":   str(date),
        "explicit":       False,
        "episode_number": ep_num,
    }

    print("\n=== Uploading to Buzzsprout ===")
    with open(file, "rb") as audio_fh:
        response = requests.post(
            url,
            headers = headers,
            data    = data,
            files   = {"audio_file": audio_fh},
        )

    if response.status_code not in (200, 201):
        raise RuntimeError(f"Buzzsprout upload failed ({response.status_code}): {response.text}")

    result = response.json()
    print(f"Buzzsprout episode created: {result.get('id')}")
    return result


# ── Jekyll page ───────────────────────────────────────────────────────────────

def generate_markdown(
    ep:          int,
    show:        str,
    title:       str,
    description: str,
    duration:    str,
    audio_size:  int,
    date:        datetime,
    identifier:  str,
) -> Path:
    """
    Write a Jekyll episode page to output/_podcast/<show>/<NNNN>.md.

    The front matter drives the Jekyll episode layout; the body is the full
    description / show notes.  The audio URL points to the Internet Archive
    download, using the deterministic identifier we assigned at upload time.
    """
    ep_str     = f"{ep:04d}"
    audio_file = f"{identifier}.mp3"
    audio_url  = f"https://archive.org/download/{identifier}/{audio_file}"

    # Truncate description to 150 chars for the meta description field
    meta_desc = description[:150] + ("..." if len(description) > 150 else "")

    front_matter = f"""\
---
layout: episode
show: {show}
title: "{title}"
description: "{meta_desc}"
date: {date.date()}
episode_number: {ep}
duration: "{duration}"
audio_url: "{audio_url}"
audio_size: "{audio_size}"
audio_type: "audio/mp3"
permalink: /podcast/{show}/{ep_str}/
---

{description}
"""

    path = Path(f"output/_podcast/{show}/{ep_str}.md")
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(front_matter)
    print(f"Jekyll page written: {path}")
    return path
