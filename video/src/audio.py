# src/audio.py

import subprocess
import json
from pathlib import Path
import struct


def extract_amplitude(input_mp3: Path, output_json: Path, fps: int):
    """
    Extract RMS amplitude aligned to video FPS.
    """

    # Step 1: get higher resolution PCM (important)
    sample_rate = fps * 100  # e.g. 3000 Hz for 30 fps

    cmd = [
        "ffmpeg",
        "-i", str(input_mp3),
        "-ac", "1",                 # mono
        "-ar", str(sample_rate),   # high-res sampling
        "-f", "f32le",
        "-"
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE)

    # Step 2: decode binary floats
    samples = [s[0] for s in struct.iter_unpack("f", result.stdout)]

    if not samples:
        with open(output_json, "w") as f:
            json.dump([], f)
        return

    # Step 3: compute RMS per frame window
    window_size = sample_rate // fps  # should be ~100

    amplitudes = []

    for i in range(0, len(samples), window_size):
        window = samples[i:i + window_size]

        if not window:
            continue

        # RMS calculation
        square_sum = sum(s * s for s in window)
        rms = (square_sum / len(window)) ** 0.5

        amplitudes.append(rms)

    # Step 4: normalize
    max_val = max(amplitudes) if amplitudes else 1.0
    normalized = [v / max_val for v in amplitudes]

    # Step 5: optional shaping (Option B: medium responsiveness)
    shaped = [v ** 1.8 for v in normalized]

    # Step 6: light smoothing (keeps sync intact)
    smoothed = []
    window = 2

    for i in range(len(shaped)):
        start = max(0, i - window)
        end = min(len(shaped), i + window + 1)

        avg = sum(shaped[start:end]) / (end - start)
        smoothed.append(avg)

    with open(output_json, "w") as f:
        json.dump(smoothed, f)