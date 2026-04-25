#!/usr/bin/env python3

import argparse
import subprocess
import os
import re
import json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# -------------------------
# Helpers
# -------------------------

def run(cmd, capture=False):
    print(f"\n>> {' '.join(cmd)}")
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        subprocess.run(cmd, check=True)

def parse_date_from_filename(filename):
    base = os.path.basename(filename)
    match = re.match(r'(\d{8})_(\d{6})', base)
    if not match:
        return datetime.today()

    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")

def get_duration(file):
    cmd = [
        "ffprobe", "-i", file,
        "-show_entries", "format=duration",
        "-v", "quiet",
        "-of", "csv=p=0"
    ]
    result = subprocess.check_output(cmd).decode().strip()
    seconds = int(float(result))
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"

def get_file_size(file):
    return os.path.getsize(file)


# -------------------------
# Audio Processing
# -------------------------

BASE_FILTER = "highpass=f=80,lowpass=f=14000,afftdn=nf=-20"


def loudnorm_pass1(input_file):
    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-af", f"{BASE_FILTER},loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-"
    ]

    result = run(cmd, capture=True)

    match = re.search(r'\{.*\}', result.stderr, re.DOTALL)
    if not match:
        raise RuntimeError("Failed to parse loudnorm pass1")

    return json.loads(match.group(0))


def loudnorm_pass2(input_file, output_file, stats):
    ln = (
        f"loudnorm=I=-16:TP=-1.5:LRA=11:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:print_format=summary"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-af", f"{BASE_FILTER},{ln}",
        "-ar", "44100",
        "-ac", "1",
        "-b:a", "96k",
        output_file
    ]

    run(cmd)


def single_pass(input_file, output_file):
    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-af", f"{BASE_FILTER},loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "44100",
        "-ac", "1",
        "-b:a", "96k",
        output_file
    ]
    run(cmd)


# -------------------------
# Media Upload
# -------------------------

def upload_to_archive(file, ep_num, title, description, date, show, test=False):
    from internetarchive import upload
    
    access_key = os.getenv("IA_ACCESS_KEY")
    secret_key = os.getenv("IA_SECRET_KEY")

    if not access_key or not secret_key:
        raise RuntimeError("Missing IA credentials in environment (.env not loaded?)")

    base_identifier = f"{show}_ep{ep_num:04d}"

    if test:
        identifier = f"test_{base_identifier}"
    else:
        identifier = base_identifier

    metadata = {
        "title": f"{show.capitalize()} – {title}",
        "creator": "Vaguely Generic",
        "mediatype": "audio",
        "collection": "opensource_audio", 
        "date": str(date.date()),
        "description": description,
        "subject": ["podcast", show, "vaguely generic"],
        "series": show.capitalize(),
    }

    if test:
        metadata["title"] = f"[TEST] {metadata['title']}"

    print(f"\n=== Uploading to Internet Archive: {identifier} ===")

    upload(
    identifier,
    files=[file],
    metadata=metadata,
    access_key=access_key,
    secret_key=secret_key
    )

    return identifier

def upload_to_buzzsprout(file, title, description, date, ep_num):
    import requests

    api_token = os.getenv("BUZZSPROUT_API_TOKEN")
    podcast_id = os.getenv("BUZZSPROUT_PODCAST_ID")

    if not api_token or not podcast_id:
        raise RuntimeError("Missing Buzzsprout credentials in .env")

    url = f"https://www.buzzsprout.com/api/{podcast_id}/episodes.json"

    headers = {
        "Authorization": f"Token token={api_token}"
    }

    data = {
        "title": title,
        "description": description,
        "published_at": str(date),
        "explicit": False,
        "episode_number": ep_num,
    }

    files = {
        "audio_file": open(file, "rb")
    }

    print(f"\n=== Uploading to Buzzsprout ===")

    response = requests.post(url, headers=headers, data=data, files=files)

    if response.status_code != 200 and response.status_code != 201:
        raise RuntimeError(f"Buzzsprout upload failed: {response.text}")

    result = response.json()

    print(f"Buzzsprout episode created: {result.get('id')}")

    return result

def upload_to_spotify(*args, **kwargs):
    print("\n=== Spotify upload not implemented ===")

# -------------------------
# Vaguely Generic Markdown - Jekyll
# -------------------------

def generate_markdown(ep, show, title, description, duration, audio_size, date, identifier):
    ep_str = f"{ep:04d}"

    # Default to MP3 for new pipeline
    audio_filename = f"{identifier}.mp3"
    audio_url = f"https://archive.org/download/{identifier}/{audio_filename}"

    # Jekyll front matter
    md = f"""---
layout: episode
show: {show}
title: "{title}"
description: "{description[:150]}{'...' if len(description) > 150 else ''}"
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

    path = f"output/_podcast/{show}/{ep_str}.md"

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w") as f:
        f.write(md)

    return path

# -------------------------
# Main
# -------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--ep", type=int, required=True)
    parser.add_argument("--show", required=True)
    parser.add_argument("--title", help="Episode title (optional - defaults to 'Episode XX')")
    parser.add_argument("--desc", required=True)
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--testupload", action="store_true")
    parser.add_argument("--buzzsprout", action="store_true")
    parser.add_argument("--spotify", action="store_true")


    args = parser.parse_args()

    date = parse_date_from_filename(args.input)
    ep_str = f"{args.ep:04d}"
    base_name = f"{args.show}_ep{ep_str}"
    title = args.title if args.title else f"Episode {ep_str}"



    # -------------------------
    # TEST MODE
    # -------------------------
    if args.test:
        print("\n=== TEST MODE ===")

        single_pass(args.input, f"output/test-{base_name}-v1-singlepass.mp3")

        stats = loudnorm_pass1(args.input)
        loudnorm_pass2(args.input, f"output/test-{base_name}-v2-doublepass.mp3", stats)

        print("\nCreated test variants. Compare and choose.")

        return

    # -------------------------
    # PRODUCTION MODE
    # -------------------------
    stats = loudnorm_pass1(args.input)
    final_audio = f"output/{base_name}.mp3"
    loudnorm_pass2(args.input, final_audio, stats)

    duration = get_duration(final_audio)
    size = get_file_size(final_audio)

    identifier = None

    if args.archive:
        identifier = upload_to_archive(
            final_audio,
            args.ep,
            title,
            args.desc,
            date,
            args.show,
            test=args.testupload

        )
    else:
        # fallback (still deterministic)
        identifier = f"{args.show}_ep{args.ep:04d}"

    md = generate_markdown(
        args.ep,
        args.show,
        title,
        args.desc,
        duration,
        size,
        date,
        identifier
    )


    print("\n=== Done ===")
    print(final_audio)
    print(md)


if __name__ == "__main__":
    main()
