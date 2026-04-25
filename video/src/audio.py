# src/audio.py

import subprocess
import json
from pathlib import Path
import struct


# src/audio.py

import subprocess
import json
from pathlib import Path
import struct


def extract_amplitude(input_mp3: Path, output_json: Path, fps: int = 30):
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

def extract_amplitude_stereo(
    input_mp3: Path,
    output_left: Path,
    output_right: Path,
    fps: int
):
    """
    Extract RMS amplitude per channel (stereo).
    """

    def process_channel(channel: str, output_path: Path):
        sample_rate = fps * 100

        cmd = [
            "ffmpeg",
            "-i", str(input_mp3),

            "-map_channel", f"0.0.{channel}",  # 0 = left, 1 = right
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "f32le",
            "-"
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE)

        samples = [s[0] for s in struct.iter_unpack("f", result.stdout)]

        if not samples:
            with open(output_path, "w") as f:
                json.dump([], f)
            return

        window_size = sample_rate // fps
        amplitudes = []

        for i in range(0, len(samples), window_size):
            window = samples[i:i + window_size]
            if not window:
                continue

            square_sum = sum(s * s for s in window)
            rms = (square_sum / len(window)) ** 0.5
            amplitudes.append(rms)

        # Normalize → shape → smooth → renormalize
        max_val = max(amplitudes) or 1.0
        normalized = [v / max_val for v in amplitudes]

        shaped = [v ** 1.8 for v in normalized]

        smoothed = []
        smooth_radius = 2

        for i in range(len(shaped)):
            start = max(0, i - smooth_radius)
            end = min(len(shaped), i + smooth_radius + 1)
            avg = sum(shaped[start:end]) / (end - start)
            smoothed.append(avg)

        # Final normalize
        max_val = max(smoothed) or 1.0
        final = [v / max_val for v in smoothed]

        with open(output_path, "w") as f:
            json.dump(final, f)

    # Process left (0) and right (1)
    process_channel("0", output_left)
    process_channel("1", output_right)