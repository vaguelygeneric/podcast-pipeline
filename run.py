#!/usr/bin/env python3
"""
run.py — Single entry point for the podcast pipeline.

Handles the full publish workflow in sequence:
  1. Audio cleanup & loudness normalization  (m4a → mp3)
  2. Video generation                        (mp3 → mp4)
  3. Jekyll markdown page                    (metadata → .md)
  4. Platform uploads                        (Internet Archive, Buzzsprout)

Run from the project root. All paths are relative to here.

Usage examples are in README.md, but quick reference:

  # Full production run
  python run.py episode.m4a --ep 42 --show mypodcast --title "My Title" --desc "..."

  # Audio only, no upload
  python run.py episode.m4a --ep 42 --show mypodcast --desc "..." --no-video --no-upload

  # Test audio processing only (compares single-pass vs double-pass output)
  python run.py episode.m4a --ep 42 --show mypodcast --desc "..." --test-audio

  # Generate video from an already-processed mp3
  python run.py output/mypodcast_ep0042.mp3 --ep 42 --show mypodcast --desc "..." --no-audio --no-upload
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # reads .env from cwd

# Theme default imports.
from video.src.palette import list_themes, DEFAULT_THEME, DEFAULT_MODE

from video.src.renderer import (
    DEFAULT_RING_SCALE, DEFAULT_N_BARS,
    DEFAULT_BAR_HEIGHT, DEFAULT_N_SPARKS, DEFAULT_GLOW_BLUR,
)

def parse_args():
    p = argparse.ArgumentParser(
        description="Podcast publish pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Required ──────────────────────────────────────────────────────────────
    p.add_argument("input",
                   help="Source audio file (.m4a preferred, .mp3 also accepted)")
    p.add_argument("--ep",   type=int, required=True, help="Episode number")
    p.add_argument("--show", required=True,           help="Show slug (e.g. 'mypodcast')")
    p.add_argument("--desc", required=True,           help="Episode description / show notes")

    # ── Optional metadata ─────────────────────────────────────────────────────
    p.add_argument("--title",
                   help="Episode title (default: 'Episode NNNN')")
    p.add_argument("--logo",
                   default=".files/images/logo.png",
                   help="Logo PNG for video overlay (default: assets/images/logo.png)")

    # ── Stage toggles ─────────────────────────────────────────────────────────
    p.add_argument("--no-audio",  action="store_true",
                   help="Skip audio processing (input must already be a clean mp3)")
    p.add_argument("--no-video",  action="store_true",
                   help="Skip video generation")
    p.add_argument("--no-upload", action="store_true",
                   help="Skip all platform uploads (still writes local files)")

    # ── Upload targets (only matter if --no-upload is not set) ───────────────
    p.add_argument("--archive",    action="store_true",
                   help="Upload mp3 to Internet Archive")
    p.add_argument("--buzzsprout", action="store_true",
                   help="Upload mp3 to Buzzsprout")
    p.add_argument("--test-upload", action="store_true",
                   help="Mark Internet Archive upload as [TEST] item")

    # ── Dev / QA modes ────────────────────────────────────────────────────────
    p.add_argument("--test-audio", action="store_true",
                   help="Write two audio variants (single-pass and double-pass) for comparison; then exit")
    p.add_argument("--quick-video", action="store_true",
                   help="Use the faster (simpler) video renderer")

    # ── Video tuning knobs ────────────────────────────────────────────────────
    p.add_argument("--resolution", default="1280x720",
                   help="Video resolution WxH (default: 1280x720)")
    p.add_argument("--fps",        type=int, default=30,
                   help="Video framerate (default: 30)")

    # Video Theme & style options (only matter if --no-video is not set)
    p.add_argument("--ring-scale", type=float, default=DEFAULT_RING_SCALE,
                        help=f"Ring size multiplier (default: {DEFAULT_RING_SCALE})")
    p.add_argument("--n-bars",     type=int,   default=DEFAULT_N_BARS,
                        help=f"Arc waveform bar count (default: {DEFAULT_N_BARS})")
    p.add_argument("--bar-height", type=float, default=DEFAULT_BAR_HEIGHT,
                        help=f"Bar height as fraction of canvas short-edge (default: {DEFAULT_BAR_HEIGHT})")
    p.add_argument("--n-sparks",   type=int,   default=DEFAULT_N_SPARKS,
                        help=f"Spark particle count (default: {DEFAULT_N_SPARKS})")
    p.add_argument("--glow-blur",  type=int,   default=DEFAULT_GLOW_BLUR,
                        help=f"Glow ring blur radius px (default: {DEFAULT_GLOW_BLUR})")
    p.add_argument("--style",      default="v2", choices=["v1", "v2"],
                        help="Renderer style: v1=circular waveform, v2=bottom waveform + freeform particles (default: v2)")
    p.add_argument("--watermark",         default=None,
                        help="Path to watermark PNG (shown bottom-right, fades in/out)")
    p.add_argument("--watermark-opacity", type=float, default=0.35,
                        help="Steady-state watermark opacity 0.0–1.0 (default: 0.35)")
    p.add_argument("--watermark-size",    type=float, default=0.08,
                        help="Watermark height as fraction of canvas height (default: 0.08)")
    p.add_argument("--watermark-margin",    type=int, default=24,
                        help="Watermark margin (default: 24)")   
    p.add_argument("--theme",  default=DEFAULT_THEME,
                        help=f"Color theme. Available: {", ".join(list_themes())} (default: {DEFAULT_THEME})")
    p.add_argument("--mode",   default=DEFAULT_MODE, choices=["dark", "light"],
                        help="Color mode: dark or light (default: dark)")
    return p.parse_args()


def main():
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Error: input file not found: {input_path}")

    ep_str   = f"{args.ep:04d}"
    base_name = f"{args.show}_ep{ep_str}"
    title     = args.title or f"Episode {ep_str}"

    # Ensure output dirs exist
    Path("output").mkdir(exist_ok=True)
    Path("temp").mkdir(exist_ok=True)

    # ── Stage 1: Audio ────────────────────────────────────────────────────────
    if args.test_audio:
        # Write both variants and bail out — user compares manually
        from pipeline.audio import loudnorm_pass1, loudnorm_pass2, single_pass
        print("\n=== TEST AUDIO MODE ===")
        single_pass(input_path, f"output/test-{base_name}-v1-singlepass.mp3")
        stats = loudnorm_pass1(input_path)
        loudnorm_pass2(input_path, f"output/test-{base_name}-v2-doublepass.mp3", stats)
        print("\nCreated two variants for comparison. Pick one, then run without --test-audio.")
        return

    if args.no_audio:
        # Audio stage skipped — input must already be a clean mp3
        final_audio = input_path
        print(f"[audio] Skipped — using existing file: {final_audio}")
    else:
        from pipeline.audio import loudnorm_pass1, loudnorm_pass2
        final_audio = Path(f"output/{base_name}.mp3")
        print("\n=== Stage 1: Audio Processing ===")
        stats = loudnorm_pass1(input_path)
        loudnorm_pass2(input_path, final_audio, stats)

    # ── Stage 2: Video ────────────────────────────────────────────────────────
    if not args.no_video:
        from pipeline.video import build_video
        print("\n=== Stage 2: Video Generation ===")
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

    # ── Stage 3: Metadata & Jekyll page ──────────────────────────────────────
    from pipeline.publish import get_duration, get_file_size, parse_date_from_filename, generate_markdown
    print("\n=== Stage 3: Metadata ===")
    date     = parse_date_from_filename(str(input_path))
    duration = get_duration(final_audio)
    size     = get_file_size(final_audio)

    # ── Stage 4: Platform uploads ─────────────────────────────────────────────
    identifier = f"{args.show}_ep{args.ep:04d}"   # default (no upload)

    if not args.no_upload:
        print("\n=== Stage 4: Uploads ===")

        if args.archive:
            from pipeline.publish import upload_to_archive
            identifier = upload_to_archive(
                file        = final_audio,
                ep_num      = args.ep,
                title       = title,
                description = args.desc,
                date        = date,
                show        = args.show,
                test        = args.test_upload,
            )

        if args.buzzsprout:
            from pipeline.publish import upload_to_buzzsprout
            upload_to_buzzsprout(
                file        = final_audio,
                title       = title,
                description = args.desc,
                date        = date,
                ep_num      = args.ep,
            )

    # ── Generate Jekyll page (always) ────────────────────────────────────────
    md_path = generate_markdown(
        ep          = args.ep,
        show        = args.show,
        title       = title,
        description = args.desc,
        duration    = duration,
        audio_size  = size,
        date        = date,
        identifier  = identifier,
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n=== Done ===")
    print(f"  Audio : {final_audio}")
    print(f"  Jekyll: {md_path}")
    if not args.no_video:
        print(f"  Video : output/{Path(final_audio).stem}.mp4")


if __name__ == "__main__":
    main()
