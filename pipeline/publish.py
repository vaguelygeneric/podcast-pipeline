"""
pipeline/publish.py — Metadata helpers, platform uploads, and Jekyll page generation.
IMPROVED VERSION: resilient to API failures with retry logic, circuit breakers, and graceful degradation.

Responsibilities:
  - Parse recording date from filename (for archive metadata and Jekyll front matter)
  - Probe duration and file size from a finished mp3
  - Upload to Internet Archive (primary audio host) — with retries and circuit breaker
  - Upload to Buzzsprout (podcast RSS host) — with retries and circuit breaker
  - Generate a Jekyll episode page as a .md file under output/_podcast/

Credentials are read from environment variables (loaded from .env by run.py):
  IA_ACCESS_KEY, IA_SECRET_KEY       — Internet Archive
  BUZZSPROUT_API_TOKEN               — Buzzsprout API token
  BUZZSPROUT_PODCAST_ID              — Buzzsprout numeric podcast ID

RESILIENCE IMPROVEMENTS:
  ✓ Retry logic with exponential backoff for transient failures
  ✓ Circuit breaker pattern to prevent cascading failures
  ✓ Graceful degradation: failed uploads don't crash the pipeline
  ✓ Failed operation logging for manual recovery
  ✓ Timeout management on all network requests
  ✓ Distinction between permanent (404, 401) and temporary failures (5xx, timeouts)
"""

import os
import re
import difflib
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import resilience utilities
from .resilience import (
    RetryableError,
    PermanentError,
    retry_with_backoff,
    archive_breaker,
    buzzsprout_breaker,
    failure_log,
    ARCHIVE_RETRY_CONFIG,
    BUZZSPROUT_RETRY_CONFIG,
)

logger = logging.getLogger("podcast_pipeline")


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date_from_filename(filename: str) -> datetime:
    """
    Extract recording date/time from filenames like 20240420_143022_episode.m4a.
    Falls back to today's date if the pattern isn't found.
    """
    base = os.path.basename(filename)
    match = re.match(r'(\d{8})_(\d{6})', base)
    if not match:
        return datetime.today()
    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")

def get_duration(file: Path) -> str:
    """
    Return a human-readable duration string ("MM:SS") using ffprobe.
    
    Raises:
        PermanentError: if ffprobe fails (should not happen with valid mp3)
    """
    cmd = [
        "ffprobe", "-i", str(file),
        "-show_entries", "format=duration",
        "-v", "quiet",
        "-of", "csv=p=0",
    ]
    try:
        result = subprocess.check_output(cmd, timeout=10).decode().strip()
        seconds = int(float(result))
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    except (subprocess.CalledProcessError, ValueError) as e:
        raise PermanentError(f"Failed to probe duration from {file}: {e}")


def get_file_size(file: Path) -> int:
    """Return file size in bytes (used in RSS enclosure tags)."""
    return os.path.getsize(file)


# ── Internet Archive (with resilience) ─────────────────────────────────────────

@retry_with_backoff(ARCHIVE_RETRY_CONFIG)
def _upload_to_archive_impl(
    file: Path,
    ep_num: int,
    title: str,
    description: str,
    date: datetime,
    show: str,
    test: bool = False,
) -> str:
    """
    Internal implementation of archive.org upload.
    Wrapped by upload_to_archive() to add circuit breaker.

    Raises:
        RetryableError: for temporary failures (network, 5xx)
        PermanentError: for unrecoverable errors (missing credentials, 404)
    """
    try:
        from internetarchive import upload
    except ImportError:
        raise PermanentError(
            "internetarchive library not installed. "
            "Install with: pip install internetarchive"
        )

    access_key = os.getenv("IA_ACCESS_KEY")
    secret_key = os.getenv("IA_SECRET_KEY")
    if not access_key or not secret_key:
        raise PermanentError(
            "Missing IA credentials. Set IA_ACCESS_KEY and IA_SECRET_KEY in .env"
        )

    identifier = f"{show}_ep{ep_num:04d}"
    if test:
        identifier = f"test_{identifier}"

    metadata = {
        "title": f"{'[TEST] ' if test else ''}{show.capitalize()} – {title}",
        "creator": "Vaguely Generic",
        "mediatype": "audio",
        "collection": "opensource_audio",
        "date": str(date.date()),
        "description": description,
        "subject": ["podcast", show, "vaguely generic"],
        "series": show.capitalize(),
    }

    logger.info(f"Uploading to Internet Archive: {identifier}")
    try:
        # internetarchive.upload() can raise various exceptions
        # Wrap network/timeout errors as RetryableError, validation errors as PermanentError
        upload(
            identifier,
            files=str(file),
            metadata=metadata,
            access_key=access_key,
            secret_key=secret_key,
        )
        logger.info(f"Successfully uploaded to Internet Archive: {identifier}")
        return identifier

    except Exception as e:
        error_str = str(e).lower()
        # Classify the error
        if any(term in error_str for term in ["timeout", "connection", "reset", "503", "502", "500"]):
            raise RetryableError(
                f"Internet Archive upload failed (temporary): {e}",
                original_error=e,
            )
        elif any(term in error_str for term in ["401", "403", "unauthorized", "forbidden"]):
            raise PermanentError(f"Internet Archive authentication failed: {e}")
        elif any(term in error_str for term in ["404", "not found"]):
            raise PermanentError(f"Internet Archive resource not found: {e}")
        else:
            # Unknown error — treat as retryable to be safe
            raise RetryableError(
                f"Internet Archive upload failed (unknown): {e}",
                original_error=e,
            )


def upload_to_archive(
    file: Path,
    ep_num: int,
    title: str,
    description: str,
    date: datetime,
    show: str,
    test: bool = False,
) -> Optional[str]:
    """
    Upload the mp3 to Internet Archive with resilience.

    Returns:
        identifier if successful, None if failed but pipeline continues
        (failure is logged to .failures/ for manual retry)

    test=True prefixes the identifier with 'test_' and marks the title
    with [TEST] so it can be identified and cleaned up after QA.
    """
    try:
        return archive_breaker.call(
            _upload_to_archive_impl,
            file=file,
            ep_num=ep_num,
            title=title,
            description=description,
            date=date,
            show=show,
            test=test,
        )
    except Exception as e:
        # Log failure for manual recovery
        failure_log.record_failure(
            service="archive.org",
            operation="upload_episode",
            error=str(e),
            context={
                "file": str(file),
                "episode": ep_num,
                "show": show,
                "title": title,
            },
        )
        logger.error(
            f"Internet Archive upload failed (will retry manually): {e}"
        )
        return None


# ── Buzzsprout (with resilience) ──────────────────────────────────────────────

@retry_with_backoff(BUZZSPROUT_RETRY_CONFIG)
def _upload_to_buzzsprout_impl(
    file: Path,
    title: str,
    description: str,
    date: datetime,
    ep_num: int,
) -> dict:
    """
    Internal implementation of Buzzsprout upload.
    Wrapped by upload_to_buzzsprout() to add circuit breaker.

    Raises:
        RetryableError: for temporary failures
        PermanentError: for unrecoverable errors
    """
    try:
        import requests
    except ImportError:
        raise PermanentError(
            "requests library not installed. "
            "Install with: pip install requests"
        )

    api_token = os.getenv("BUZZSPROUT_API_TOKEN")
    podcast_id = os.getenv("BUZZSPROUT_PODCAST_ID")
    if not api_token or not podcast_id:
        raise PermanentError(
            "Missing Buzzsprout credentials. Set BUZZSPROUT_API_TOKEN and "
            "BUZZSPROUT_PODCAST_ID in .env"
        )

    url = f"https://www.buzzsprout.com/api/{podcast_id}/episodes.json"
    headers = {"Authorization": f"Token token={api_token}"}
    data = {
        "title": title,
        "description": description,
        "published_at": str(date),
        "explicit": False,
        "episode_number": ep_num,
    }

    logger.info(f"Uploading to Buzzsprout: {title}")
    try:
        with open(file, "rb") as audio_fh:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                files={"audio_file": audio_fh},
                timeout=120,  # Buzzsprout uploads can be slow
            )

        # Classify the response
        if response.status_code in (200, 201):
            result = response.json()
            logger.info(f"Successfully uploaded to Buzzsprout: episode {result.get('id')}")
            return result

        error_str = response.text.lower()
        if response.status_code == 401:
            raise PermanentError(f"Buzzsprout authentication failed (401): {response.text}")
        elif response.status_code == 403:
            raise PermanentError(f"Buzzsprout forbidden (403): {response.text}")
        elif response.status_code == 404:
            raise PermanentError(f"Buzzsprout resource not found (404): {response.text}")
        elif response.status_code in (408, 429, 500, 502, 503, 504):
            # Retryable server errors
            raise RetryableError(
                f"Buzzsprout upload failed ({response.status_code}): {response.text[:200]}",
                original_error=Exception(response.text),
            )
        else:
            # Unknown status — treat as retryable
            raise RetryableError(
                f"Buzzsprout upload failed ({response.status_code}): {response.text[:200]}",
                original_error=Exception(response.text),
            )

    except requests.Timeout as e:
        raise RetryableError(
            f"Buzzsprout upload timed out: {e}",
            original_error=e,
        )
    except requests.ConnectionError as e:
        raise RetryableError(
            f"Buzzsprout connection failed: {e}",
            original_error=e,
        )


def upload_to_buzzsprout(
    file: Path,
    title: str,
    description: str,
    date: datetime,
    ep_num: int,
) -> Optional[dict]:
    """
    Upload the episode mp3 to Buzzsprout with resilience.

    Returns:
        API response JSON dict if successful, None if failed but pipeline continues
        (failure is logged to .failures/ for manual retry)
    """
    try:
        return buzzsprout_breaker.call(
            _upload_to_buzzsprout_impl,
            file=file,
            title=title,
            description=description,
            date=date,
            ep_num=ep_num,
        )
    except Exception as e:
        # Log failure for manual recovery
        failure_log.record_failure(
            service="buzzsprout",
            operation="upload_episode",
            error=str(e),
            context={
                "file": str(file),
                "episode": ep_num,
                "title": title,
            },
        )
        logger.error(
            f"Buzzsprout upload failed (will retry manually): {e}"
        )
        return None


# ── Jekyll page ────────────────────────────────────────────────────────────────

def generate_markdown(
    ep: int,
    show: str,
    title: str,
    description: str,
    duration: str,
    audio_size: int,
    date: datetime,
    publish_date=None,
    identifier: Optional[str] = None,
    audio_url: Optional[str] = None,
) -> Path:
    """
    Write a Jekyll episode page to output/_podcast/<show>/<NNNN>.md.

    The front matter drives the Jekyll episode layout; the body is the full
    description / show notes.

    Args:
        date: recording date/time (drives `created` in front matter)
        publish_date: release date (drives `publish_date` in front matter).
            Distinct from `date` to support front-loading — recording
            several episodes in one sitting but releasing them on a daily
            cadence. Defaults to date's calendar date if not given, to
            preserve behavior for any external callers.
        identifier: Internet Archive identifier (for fallback audio_url)
        audio_url: explicit audio URL (takes precedence over identifier)

    If both are None, the audio_url field is left empty (Jekyll page still generated).
    This allows the pipeline to continue even if Archive upload failed.
    """

    if publish_date is None:
        publish_date = date.date()
    ep_str = f"{ep:04d}"

    # Build audio URL from identifier or use explicit audio_url
    if audio_url:
        final_audio_url = audio_url
    elif identifier:
        audio_file = f"{identifier}.mp3"
        final_audio_url = f"https://archive.org/download/{identifier}/{audio_file}"
    else:
        # Upload failed — leave URL empty, user can update later
        final_audio_url = ""
        logger.warning(f"No audio URL available for episode {ep}; Jekyll page will have empty audio_url")

    # Truncate description to 150 chars for the meta description field
    meta_desc = description[:150] + ("..." if len(description) > 150 else "")

    front_matter = f"""\
---
layout: episode
show: {show}
title: "{title}"
description: "{meta_desc}"
created: {date.isoformat()}
publish_date: {publish_date}
episode_number: {ep}
duration: "{duration}"
audio_url: "{final_audio_url}"
audio_size: "{audio_size}"
audio_type: "audio/mp3"
permalink: /podcast/{show}/{ep_str}/
---

{description}
"""

    path = Path(f"output/_podcast/{show}/{ep_str}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(front_matter)
    logger.info(f"Jekyll page written: {path}")
    return path


def generate_website_patch(md_path: Path, show: str, ep_str: str, jekyll_site_repo=None) -> Path:
    """
    Generate a git-apply-compatible patch that adds or updates this
    episode's Jekyll page in the website repo, at _podcast/<show>/<NNNN>.md.

    Two cases:

    1. The file doesn't exist yet in the website repo (or jekyll_site_repo
       isn't configured/found) — plain new-file patch, same as before.

    2. The file already exists (e.g. show notes pre-written before
       recording). Rather than overwrite it wholesale, MERGE: front-matter
       fields the pipeline is authoritative on (created, duration,
       audio_url, audio_size, audio_type — none of which can be known
       before the audio exists) are refreshed with fresh values.
       Everything else pre-written — every other front-matter field, and
       the entire body — is left untouched. The resulting patch is a real
       diff against the pre-written file, so it's small, reviewable, and
       applies cleanly with `git apply` instead of failing because the
       path already exists.
    """

    target_rel_path = f"_podcast/{show}/{ep_str}.md"
    new_content = md_path.read_text(encoding="utf-8")

    existing_path = None

    if jekyll_site_repo:
        jekyll_site_repo_path = Path(jekyll_site_repo)

        if jekyll_site_repo_path.is_dir():
            candidate = jekyll_site_repo_path / target_rel_path
            if candidate.exists():
                existing_path = candidate
        else:
            logger.warning(
                f"Jekyll site repo not found at '{jekyll_site_repo_path}' — "
                f"generating a new-file patch without checking for "
                f"pre-written content."
            )

    if existing_path is None:
        old_content = ""
        final_content = new_content
    else:
        old_content = existing_path.read_text(encoding="utf-8")

        new_fm_lines, _ = parse_jekyll_file(new_content)
        existing_fm_lines, existing_body = parse_jekyll_file(old_content)

        merged_fm_lines = merge_front_matter(existing_fm_lines, new_fm_lines)

        final_content = "---\n" + "\n".join(merged_fm_lines) + "\n---\n\n" + existing_body

        logger.info(
            f"Pre-written content found at {existing_path} — merging "
            f"instead of replacing"
        )

    patch_text = build_unified_patch(target_rel_path, old_content, final_content)

    patch_dir = Path("output/patches")
    patch_dir.mkdir(parents=True, exist_ok=True)

    patch_path = patch_dir / f"{show}-{ep_str}.patch"
    patch_path.write_text(patch_text, encoding="utf-8")

    return patch_path


# Front-matter fields the pipeline is authoritative on: none of these can
# be known before the audio has actually been processed, so they always
# take the freshly-computed value, even when merging into a pre-written
# file.
ALWAYS_OVERWRITE_FRONT_MATTER_KEYS = {
    "created", "duration", "audio_url", "audio_size", "audio_type",
}


def parse_jekyll_file(content: str):
    """
    Split a Jekyll file into (front_matter_lines, body).

    front_matter_lines is the raw list of 'key: value' lines (original
    formatting preserved) found between the two '---' delimiters. body is
    everything after the closing '---', with the conventional leading
    blank line stripped.
    """

    lines = content.split("\n")

    if not lines or lines[0].strip() != "---":
        return [], content

    try:
        closing_idx = lines[1:].index("---") + 1
    except ValueError:
        return [], content

    fm_lines = lines[1:closing_idx]
    body = "\n".join(lines[closing_idx + 1:]).lstrip("\n")

    return fm_lines, body


def front_matter_lines_to_dict(fm_lines):
    """{'key': 'raw value string'} — value keeps its original formatting
    (quotes, etc.) so preserved/overwritten lines round-trip cleanly."""

    result = {}

    for line in fm_lines:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()

    return result


def merge_front_matter(existing_fm_lines, new_fm_lines):
    """
    Reconcile a pre-written episode's front matter with the freshly
    generated one.

    - Keys in ALWAYS_OVERWRITE_FRONT_MATTER_KEYS always take the fresh
      value.
    - Any other key already present in the pre-written file keeps its
      original value untouched (title, description, publish_date, etc.).
    - Any key present in the fresh output but missing from the pre-written
      file is appended (covers genuinely new front-matter fields, or ones
      the person simply hadn't filled in yet).
    """

    new_fields = front_matter_lines_to_dict(new_fm_lines)

    merged_lines = []
    seen_keys = set()

    for line in existing_fm_lines:
        if ":" not in line:
            merged_lines.append(line)
            continue

        key = line.split(":", 1)[0].strip()
        seen_keys.add(key)

        if key in ALWAYS_OVERWRITE_FRONT_MATTER_KEYS and key in new_fields:
            merged_lines.append(f"{key}: {new_fields[key]}")
        else:
            merged_lines.append(line)

    for line in new_fm_lines:
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key not in seen_keys:
            merged_lines.append(line)

    return merged_lines


def build_unified_patch(target_rel_path: str, old_content: str, new_content: str) -> str:
    """
    Build a git-apply-compatible unified diff between old_content and
    new_content (old_content == "" means "file doesn't exist yet").
    """

    is_new = old_content == ""

    old_lines = old_content.splitlines(keepends=True) if old_content else []
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="/dev/null" if is_new else f"a/{target_rel_path}",
        tofile=f"b/{target_rel_path}",
    )

    header = [f"diff --git a/{target_rel_path} b/{target_rel_path}\n"]

    if is_new:
        header.append("new file mode 100644\n")

    header.append("index 0000000..0000000 100644\n")

    body = "".join(diff)

    # unified_diff doesn't add a trailing newline marker for content whose
    # last line lacks one — git's patch format expects that to be explicit.
    if new_lines and not new_lines[-1].endswith("\n"):
        body += "\n\\ No newline at end of file\n"

    return "".join(header) + body
