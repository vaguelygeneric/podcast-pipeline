# podcast-pipeline

End-to-end podcast publish pipeline: m4a → clean mp3 → mp4 video → Jekyll page → platform uploads.

---

## Folder structure

```
podcast-pipeline/
│
├── run.py                      ← Single entry point for everything
│
├── audio_profiles.json         ← Per-show audio processing config (see "Audio profiles" below)
│
├── pipeline/                   ← Core pipeline modules (import from run.py)
│   ├── __init__.py
│   ├── audio.py                ← Single-mic/field-recording path: loudness normalisation (m4a → mono mp3)
│   ├── audio_stereo.py         ← Dual-lav-mic conversation path: per-channel leveling → mono mixdown
│   ├── audio_profiles.py       ← Reads audio_profiles.json, routes each show to audio.py or audio_stereo.py
│   ├── video.py                ← Video stage orchestration (mp3 → mp4)
│   └── publish.py              ← Metadata helpers, IA/Buzzsprout uploads, Jekyll page
│
├── video/                      ← Video rendering internals
│   ├── __init__.py
│   └── src/
│       ├── __init__.py
│       ├── audio_analysis.py   ← Amplitude extraction (mono + stereo-ready)
│       ├── renderer.py         ← Frame-by-frame PNG renderer (full + quick modes)
│       └── palette.py          ← Color palette — edit here to retheme
│
├── assets/
│   └── images/                 ← Logo, cover art, etc. (gitignored if large)
│       ├── logo.png
│       └── VG_Podcast.png
│
├── output/                     ← Generated files (gitignored)
│   └── _podcast/<show>/        ← Jekyll episode pages
│
├── temp/                       ← Intermediate files (gitignored)
│   ├── amplitude.json          ← Per-frame amplitude data
│   └── frames/                 ← Rendered PNGs (deleted after mux)
│
├── .env                        ← API credentials (never commit)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Setup

```bash
git clone https://github.com/vaguelygeneric/podcast-pipeline.git
or
git clone git@github.com:vaguelygeneric/podcast-pipeline.git

cd podcast-pipeline
```

Make sure `ffmpeg` is on your PATH.

Copy `.env.example` to `.env` and fill in your credentials:
```
IA_ACCESS_KEY=...
IA_SECRET_KEY=...
BUZZSPROUT_API_TOKEN=...
BUZZSPROUT_PODCAST_ID=...
```

---

## Usage — test commands

All commands run from the project root.

### Audio processing only — no video, no upload
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --title "My Episode Title" \
  --desc "Episode description / show notes here." \
  --no-video --no-upload
```

### Compare single-pass vs double-pass audio (A/B test before committing)
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --desc "..." \
  --test-audio
# Writes output/test-mypodcast_ep0042-v1-singlepass.mp3
#        output/test-mypodcast_ep0042-v2-doublepass.mp3
# Then exits. Listen and pick.
```

### Full production run — audio + video + Jekyll page (no upload)
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --title "My Episode Title" \
  --desc "Episode description." \
  --no-upload
```

### Full production run with uploads
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --title "My Episode Title" \
  --desc "Episode description." \
  --archive --buzzsprout
```

### Test upload to Internet Archive (marks item as [TEST])
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --desc "..." \
  --archive --test-upload
```

### Skip audio (already have a clean mp3) — video only
```bash
python run.py output/mypodcast_ep0042.mp3 \
  --ep 42 --show mypodcast \
  --desc "..." \
  --no-audio --no-upload
```

### Quick video (faster render, simpler visuals)
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --desc "..." \
  --no-upload --quick-video
```

### High-resolution video
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --desc "..." \
  --no-upload --resolution 1920x1080
```

### Custom logo
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast \
  --desc "..." \
  --no-upload --logo assets/images/VG_Podcast.png
```

---

## All flags

| Flag | Default | Description |
|---|---|---|
| `input` | *(required)* | Source audio file — m4a preferred, mp3 accepted |
| `--ep` | *(required)* | Episode number (integer) |
| `--show` | *(required)* | Show slug used in filenames/URLs, and to look up the show's entry in `audio_profiles.json` (see below) |
| `--input2` | `None` | Speaker B's file, for shows configured with `dual_mono_files` mode (see below). Unused otherwise. |
| `--desc` | *(required)* | Episode description / show notes |
| `--title` | `Episode NNNN` | Episode title |
| `--logo` | `assets/images/logo.png` | Logo PNG for video overlay |
| `--no-audio` | off | Skip audio stage; input must be a clean mp3 |
| `--no-video` | off | Skip video generation |
| `--no-upload` | off | Skip all uploads |
| `--archive` | off | Upload mp3 to Internet Archive |
| `--buzzsprout` | off | Upload mp3 to Buzzsprout |
| `--test-upload` | off | Mark IA upload as `[TEST]` |
| `--test-audio` | off | Write single-pass + double-pass variants for comparison |
| `--quick-video` | off | Use faster, simpler renderer |
| `--resolution` | `1280x720` | Video output resolution |
| `--fps` | `30` | Video framerate |

---

## Audio profiles

Not every podcast records audio the same way, so Stage 1 (audio processing)
is driven by `audio_profiles.json` at the project root rather than
hardcoded per show. Each entry is keyed by the same show slug you pass to
`--show`:

```json
{
  "_default": "daily",

  "daily": {
    "mode": "single_channel",
    "lufs": -16,
    "tp": -1.5,
    "lra": 11
  },

  "vault-of-the-raw": {
    "mode": "dual_lav_stereo",
    "lufs": -16,
    "tp": -1.5,
    "lra": 11,
    "channel_filter": "highpass=f=80,lowpass=f=14000,agate=threshold=-45dB:ratio=8:attack=20:release=250,acompressor=threshold=-18dB:ratio=3:attack=20:release=250:makeup=3"
  },

  "some-remote-show": {
    "mode": "dual_mono_files",
    "lufs": -16,
    "tp": -1.5,
    "lra": 11,
    "channel_filter": "highpass=f=80,lowpass=f=14000,agate=threshold=-45dB:ratio=8:attack=20:release=250,acompressor=threshold=-18dB:ratio=3:attack=20:release=250:makeup=3"
  }
}
```

A show not listed here falls back to whatever `_default` points at.

**Modes:**

| Mode | Use for | What it does |
|---|---|---|
| `single_channel` | Solo shows, field recordings, one mic | Two-pass loudnorm on the whole file → mono mp3 (`pipeline/audio.py`) |
| `dual_lav_stereo` | Two people, each on their own lav mic, recorded to a single stereo file (left = speaker A, right = speaker B) | Splits the channels, runs each through a cleanup filter chain, then loudnorms each channel **independently** to the same target — so an unevenly-recorded pair (one mic hot, one quiet) gets leveled *before* mixing, which a single loudnorm pass on the combined signal can't do. Mixes to mono, then runs one more loudnorm pass on the mixdown as a safety net (summing two independently-normalized channels tends to land a few dB hot). This is `pipeline/audio_stereo.py`. |
| `dual_mono_files` | Remote/video-call podcasts, where each participant records locally on their own device and you end up with two separate mono files instead of one stereo file | Same leveling and mixdown as `dual_lav_stereo` — independent per-speaker cleanup + loudnorm, then mix, then a safety-net loudnorm pass — just skipping the channel-split step since each file is already one speaker only. Pass speaker A's file as the usual `input` argument and speaker B's file with `--input2`. Also lives in `pipeline/audio_stereo.py`. |

To add a show, give it a `mode` and your target `lufs`/`tp`/`lra`. `dual_lav_stereo` and `dual_mono_files` shows also need a `channel_filter` — an ffmpeg audio-filter-chain string applied to each speaker before normalization (adjust this if your mics need a different noise-reduction/gating approach than the default).

For a `dual_mono_files` show, pass both files on the command line:

```bash
python run.py speaker_a.wav --input2 speaker_b.wav --show some-remote-show
```

**Caveats:**
- `--test-audio` currently only previews the `single_channel` path (single-pass vs. two-pass mp3s for A/B listening), regardless of what mode a show is configured for — there's no equivalent quick-preview command for `dual_lav_stereo` or `dual_mono_files` yet.
- `dual_mono_files` doesn't do anything to line up the two files in time — if the two participants didn't start recording at exactly the same moment, sync them (trim the lead-in) before running the pipeline.


---

## Stereo video (future)

`video/src/audio_analysis.py` already has `extract_amplitude_stereo()` that writes
`temp/amplitude_left.json` and `temp/amplitude_right.json`.  To activate it:

1. Call `extract_amplitude_stereo()` instead of `extract_amplitude()` in `pipeline/video.py`
2. Pass both files to a dual-waveform version of `render_frames()` in `renderer.py`
