# podcast-pipeline

End-to-end podcast publish pipeline: m4a → clean mp3 → mp4 video → Jekyll page → platform uploads.

---

## Folder structure

```
podcast-pipeline/
│
├── run.py                      ← Single entry point for everything
│
├── pipeline/                   ← Core pipeline modules (import from run.py)
│   ├── __init__.py
│   ├── audio.py                ← Audio cleanup & loudness normalisation (m4a → mp3)
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
pip install -r requirements.txt
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
| `--show` | *(required)* | Show slug used in filenames and URLs |
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

## Stereo video (future)

`video/src/audio_analysis.py` already has `extract_amplitude_stereo()` that writes
`temp/amplitude_left.json` and `temp/amplitude_right.json`.  To activate it:

1. Call `extract_amplitude_stereo()` instead of `extract_amplitude()` in `pipeline/video.py`
2. Pass both files to a dual-waveform version of `render_frames()` in `renderer.py`
