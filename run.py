#!/usr/bin/env python3
"""
run.py — Single entry point for the podcast pipeline (IMPROVED VERSION).

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
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # reads .env from cwd

# Theme default imports.
from video.src.palette import list_themes, DEFAULT_THEME, DEFAULT_MODE

from video.src.renderer import (
    DEFAULT_RING_SCALE, DEFAULT_N_BARS,
    DEFAULT_BAR_HEIGHT, DEFAULT_N_SPARKS, DEFAULT_GLOW_BLUR,
)

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("podcast_pipeline")


def parse_args():
    p = argparse.ArgumentParser(
        description="Podcast publish pipeline (resilient version)",
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
                   help="Logo PNG for video overlay (default: .files/images/logo.png)")

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
    if args.test_audio:
        # Write both variants and bail out — user compares manually
        from pipeline.audio import loudnorm_pass1, loudnorm_pass2, single_pass
        logger.info("\n=== TEST AUDIO MODE ===")
        single_pass(input_path, f"output/test-{base_name}-v1-singlepass.mp3")
        stats = loudnorm_pass1(input_path)
        loudnorm_pass2(input_path, f"output/test-{base_name}-v2-doublepass.mp3", stats)
        logger.info("\nCreated two variants for comparison. Pick one, then run without --test-audio.")
        return

    if args.no_audio:
        # Audio stage skipped — input must already be a clean mp3
        final_audio = input_path
        logger.info(f"[SKIP] Audio processing — using existing file: {final_audio}")
        results["audio"] = "skipped"
    else:
        try:
            from pipeline.audio import loudnorm_pass1, loudnorm_pass2
            logger.info("\n=== Stage 1: Audio Processing ===")
            stats = loudnorm_pass1(input_path)
            final_audio = Path(f"output/{base_name}.mp3")
            loudnorm_pass2(input_path, final_audio, stats)
            results["audio"] = "success"
            logger.info(f"[OK] Audio: {final_audio}")
        except Exception as e:
            logger.error(f"[FAIL] Audio processing failed: {e}")
            results["audio"] = f"error: {e}"
            results["failures"].append(("audio", str(e)))
            sys.exit(f"Audio processing is required and failed. Cannot continue.")

    # ── Stage 2: Video ────────────────────────────────────────────────────────
    if not args.no_video:
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
        from pipeline.publish import (
            get_duration, get_file_size, parse_date_from_filename, generate_markdown
        )
        logger.info("\n=== Stage 3: Metadata ===")
        date     = parse_date_from_filename(str(input_path))
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
    if not args.no_upload:
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
                    date        = date,
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
                    date        = date,
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

    # ── Generate Jekyll page (always) ────────────────────────────────────────
    try:
        md_path = generate_markdown(
            ep          = args.ep,
            show        = args.show,
            title       = title,
            description = args.desc,
            duration    = duration,
            audio_size  = size,
            date        = date,
            identifier  = archive_identifier,
        )
        results["jekyll"] = "success"
        logger.info(f"[OK] Jekyll page: {md_path}")
    except Exception as e:
        logger.error(f"[FAIL] Jekyll page generation failed: {e}")
        results["jekyll"] = f"error: {e}"
        results["failures"].append(("jekyll", str(e)))

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

    if results["failures"]:
        logger.warning(f"\n{len(results['failures'])} operation(s) failed. Check .failures/ directory for details.")
        logger.info("To retry failed uploads, run: python retry_failed.py")

    logger.info(f"\nFiles generated:")
    logger.info(f"  Audio : {final_audio}")
    logger.info(f"  Jekyll: {md_path}")
    if not args.no_video and results["video"] == "success":
        logger.info(f"  Video : output/{Path(final_audio).stem}.mp4")

    logger.info("="*70)

    # Exit with success if core stages completed
    if results["audio"] and results["jekyll"]:
        logger.info("\n✓ Pipeline completed (core stages)")
        sys.exit(0)
    else:
        logger.error("\n✗ Pipeline failed (required stage failed)")
        sys.exit(1)


if __name__ == "__main__":
    main()
