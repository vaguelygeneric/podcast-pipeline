#!/usr/bin/env python3
"""
test_audio.py — Preview audio normalization without running the full pipeline.

Writes both a single-pass and a two-pass loudnorm variant of the given input
file to output/, so you can listen and compare before committing to an
episode. Calls the same functions in pipeline/audio.py that run.py uses in
production — no separate normalization logic to keep in sync.

This is audio-only: no episode metadata, no reservations, no Jekyll page,
no uploads. It never touches run.py's pipeline state.

Usage:
  python3 test_audio.py recording.m4a

Output:
  output/test-<input-stem>-v1-singlepass.mp3
  output/test-<input-stem>-v2-doublepass.mp3
"""

import sys
import argparse
from pathlib import Path

from pipeline.audio import loudnorm_pass1, loudnorm_pass2, single_pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Path to the source audio file (e.g. a .m4a recording)")
    args = p.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Error: input file not found: {input_path}")

    base_name = input_path.stem

    Path("output").mkdir(exist_ok=True)

    print("\n=== TEST AUDIO MODE ===")
    single_pass(input_path, f"output/test-{base_name}-v1-singlepass.mp3")
    stats = loudnorm_pass1(input_path)
    loudnorm_pass2(input_path, f"output/test-{base_name}-v2-doublepass.mp3", stats)
    print(f"\nCreated two variants for comparison:\n"
          f"  output/test-{base_name}-v1-singlepass.mp3\n"
          f"  output/test-{base_name}-v2-doublepass.mp3")


if __name__ == "__main__":
    main()
