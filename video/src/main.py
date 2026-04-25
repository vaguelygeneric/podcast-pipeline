# src/main.py

import subprocess
from pathlib import Path
import argparse
from audio import extract_amplitude
from renderer import (
    render_frames,
    render_frames_quick,
    DEFAULT_RING_SCALE, DEFAULT_N_BARS,
    DEFAULT_BAR_HEIGHT, DEFAULT_N_SPARKS, DEFAULT_GLOW_BLUR,
)


def render_video(input_mp3: Path, output_mp4: Path, fps: int):
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", "temp/frames/frame_%05d.png",
        "-i", str(input_mp3),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_mp4)
    ]
    print("Running FFmpeg…")
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="MP3 to video renderer")

    parser.add_argument("input",        help="Path to input .mp3")
    parser.add_argument("--resolution", default="1280x720",
                        help="Output resolution e.g. 1920x1080 (default: 1280x720)")
    parser.add_argument("--fps",        type=int, default=30,
                        help="Video framerate (default: 30)")
    parser.add_argument("--logo",       default=".files/images/logo.png",
                        help="Logo PNG with transparency")

    parser.add_argument("--ring-scale", type=float, default=DEFAULT_RING_SCALE,
                        help=f"Ring size multiplier (default: {DEFAULT_RING_SCALE})")
    parser.add_argument("--n-bars",     type=int,   default=DEFAULT_N_BARS,
                        help=f"Arc waveform bar count (default: {DEFAULT_N_BARS})")
    parser.add_argument("--bar-height", type=float, default=DEFAULT_BAR_HEIGHT,
                        help=f"Bar height as fraction of canvas short-edge (default: {DEFAULT_BAR_HEIGHT})")
    parser.add_argument("--n-sparks",   type=int,   default=DEFAULT_N_SPARKS,
                        help=f"Spark particle count (default: {DEFAULT_N_SPARKS})")
    parser.add_argument("--glow-blur",  type=int,   default=DEFAULT_GLOW_BLUR,
                        help=f"Glow ring blur radius px (default: {DEFAULT_GLOW_BLUR})")
    parser.add_argument("--quick", action="store_true",
                        help="Use faster rendering method (default: false)")
    args = parser.parse_args()

    input_mp3 = Path(args.input)
    if not input_mp3.exists():
        print(f"Error: file not found: {input_mp3}"); exit(1)

    try:
        width, height = map(int, args.resolution.lower().split("x"))
    except Exception:
        print("Error: --resolution must be WIDTHxHEIGHT"); exit(1)

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_mp4 = output_dir / (input_mp3.stem + ".mp4")

    amp_file = Path("temp/amplitude.json")
    amp_file.parent.mkdir(exist_ok=True)
    extract_amplitude(input_mp3, amp_file, args.fps)

    frames_dir = Path("temp/frames")
    if args.quick:
        print("Using quick rendering method (faster, less features)…")
        render_frames_quick(amp_file, frames_dir, width, height)
    else:
        print("Using full rendering method (slower, more features)…")
        render_frames(
            amp_file, frames_dir, width, height,
            logo_path  = Path(args.logo),
            ring_scale = args.ring_scale,
            n_bars     = args.n_bars,
            bar_height = args.bar_height,
            n_sparks   = args.n_sparks,
            glow_blur  = args.glow_blur,
        )

    render_video(input_mp3, output_mp4, args.fps)
    print(f"\nVideo created: {output_mp4}")


if __name__ == "__main__":
    main()
