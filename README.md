# podcast-pipeline

End-to-end podcast publish pipeline: m4a → clean mp3 → mp4 video → Jekyll page → platform uploads.

---

## Folder structure

```
podcast-pipeline/
│
├── run.py                      ← Single entry point for everything
│
├── config/
│   └── defaults.json           ← Show/audio-profile config (see "Configuration" below) — tracked in git
│
├── .files/
│   └── history.json            ← Episode/reservation history — local run log, gitignored
│
├── pipeline/                   ← Core pipeline modules (import from run.py)
│   ├── __init__.py
│   ├── audio.py                ← Single-mic/field-recording path: loudness normalisation (m4a → mono mp3)
│   ├── audio_stereo.py         ← Dual-lav-mic conversation path: per-channel leveling → mono mixdown
│   ├── audio_profiles.py       ← Named audio-profile dispatch — routes to audio.py or audio_stereo.py
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
  --ep 42 --show mypodcast --audio-profile daily \
  --title "My Episode Title" \
  --desc "Episode description / show notes here." \
  --no-video --no-upload
```

### Compare single-pass vs double-pass audio (A/B test before committing)
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --desc "..." \
  --test-audio
# Writes output/test-mypodcast_ep0042-v1-singlepass.mp3
#        output/test-mypodcast_ep0042-v2-doublepass.mp3
# Then exits. Listen and pick.
```

### Full production run — audio + video + Jekyll page (no upload)
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --title "My Episode Title" \
  --desc "Episode description." \
  --no-upload
```

### Full production run with uploads
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --title "My Episode Title" \
  --desc "Episode description." \
  --archive --buzzsprout
```

### Test upload to Internet Archive (marks item as [TEST])
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --desc "..." \
  --archive --test-upload
```

### Skip audio (already have a clean mp3) — video only
```bash
python run.py output/mypodcast_ep0042.mp3 \
  --ep 42 --show mypodcast --audio-profile daily \
  --desc "..." \
  --no-audio --no-upload
```

### Quick video (faster render, simpler visuals)
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --desc "..." \
  --no-upload --quick-video
```

### High-resolution video
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --desc "..." \
  --no-upload --resolution 1920x1080
```

### Custom logo
```bash
python run.py 20240420_143022_episode.m4a \
  --ep 42 --show mypodcast --audio-profile daily \
  --desc "..." \
  --no-upload --logo assets/images/VG_Podcast.png
```

---

## All flags

| Flag | Default | Description |
|---|---|---|
| `input` | *(required)* | Source audio file — m4a preferred, mp3 accepted |
| `--ep` | auto-assigned | Episode number (integer) |
| `--show` | prompted | Show slug used in filenames/URLs/archive.org IDs, and to look up the show's bundle in `config/defaults.json` (see "Configuration" below). Non-slug characters (spaces, punctuation, uppercase) trigger a prompt to auto-slugify. |
| `--audio-profile` | prompted | Named audio profile from `config/defaults.json`'s `audio_profiles` registry (see "Configuration" below) |
| `--input2` | `None` | Speaker B's file, for a profile configured with `dual_mono_files` mode. Unused otherwise. |
| `--desc` | prompted | Episode description / show notes |
| `--title` | `Episode NNNN` | Episode title |
| `--publish-date` | prompted | Release date (`YYYY-MM-DD`) for Jekyll front matter |
| `--logo` | `.files/images/logo.png` | Logo PNG for video overlay |
| `--jekyll-site-repo` | `config/defaults.json`'s `jekyll_site_repo` | Path to the Jekyll site repo checkout, for merging into a pre-written episode page |
| `--video` / `--no-video` | prompted (off by default) | Generate video |
| `--upload` / `--no-upload` | prompted (on by default) | Enable platform uploads (Archive/Buzzsprout gated separately below) |
| `--archive` / `--no-archive` | prompted, or skipped+`False` if `IA_ACCESS_KEY`/`IA_SECRET_KEY` aren't set | Upload mp3 to Internet Archive |
| `--buzzsprout` / `--no-buzzsprout` | prompted, or skipped+`False` if `BUZZSPROUT_API_TOKEN`/`BUZZSPROUT_PODCAST_ID` aren't set | Upload mp3 to Buzzsprout |
| `--jekyll` / `--no-jekyll` | prompted (on by default) | Generate the Jekyll markdown page |
| `--no-audio` | off | Skip audio stage; input must already be a clean mp3 |
| `--test-upload` | off | Mark IA upload as `[TEST]` |
| `--test-audio` | off | Write single-pass + double-pass variants for comparison |
| `--quick-video` | off | Use faster, simpler renderer |
| `--resolution` | `1280x720` | Video output resolution |
| `--fps` | `30` | Video framerate |
| `--ring-scale`, `--n-bars`, `--bar-height`, `--n-sparks`, `--glow-blur` | see `video/src/renderer.py` | Visualizer tuning knobs |
| `--style` | `v2` | `v1` or `v2` renderer style |
| `--theme` | see `video/src/palette.py` | Color theme — run with `--help` to list available themes |
| `--mode` | `dark` | `dark` or `light` |
| `--watermark` | `None` | Optional watermark image path |
| `--watermark-opacity`, `--watermark-size`, `--watermark-margin` | `0.35`, `0.08`, `24` | Watermark tuning knobs |

---

## Configuration

`config/defaults.json` is the single source of truth for standing
preferences and audio-profile definitions. It's tracked in git — this is
project configuration worth reviewing in diffs, not personal/local state
(that's what the gitignored `.files/history.json` is for). It's created
automatically with built-in fallback values on first run if missing, and
is meant to be hand-edited afterward.

```json
{
  "video": false,
  "upload": false,
  "jekyll": true,
  "test_upload": false,

  "audio_profiles": {
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
    }
  },

  "default_audio_profile": "daily",

  "shows": {
    "daily": {
      "audio_profile": "daily",
      "video": false,
      "archive": true,
      "buzzsprout": false,
      "upload": true,
      "jekyll": true,
      "test_upload": false,
      "jekyll_site_repo": "../website"
    },
    "vault-of-the-raw": {
      "audio_profile": "vault-of-the-raw"
    }
  }
}
```

**Top-level fields** are the bootstrap defaults for a show that has no
`"shows"` entry of its own yet — deliberately conservative (`upload:
false`) rather than mirroring any particular established show. `show`,
`archive`, `buzzsprout`, and `jekyll_site_repo` aren't listed at the top
level above; they still work there if you set them (anything omitted here
falls back to a built-in code-level default — see `DEFAULT_DEFAULTS` in
`run.py`), but the recommended pattern is to leave brand-new-show defaults
minimal and put a show's *real* settings in its own `shows` bundle once
you've run it a couple of times and know what they should be.

**`audio_profiles`** is a registry of named audio-processing profiles (this
used to be its own file, `audio_profiles.json`, at the project root — it's
merged in here now). Profile names are independent of show slugs on
purpose: a one-off or test show can reuse an existing profile (e.g.
`"daily"`) by name without needing its own registry entry. See "Audio
profile modes" below for what each `mode` does.

**`default_audio_profile`** is which registry entry to suggest when a show
has no standing choice of its own (this replaces `audio_profiles.json`'s
old `"_default"` key).

**`shows`** holds per-show bundles, keyed by show slug. Any subset of
`video`/`upload`/`archive`/`buzzsprout`/`jekyll`/`jekyll_site_repo`/
`audio_profile` can be given — `audio_profile` here is a *name* referencing
the `audio_profiles` registry above, not an inline profile definition. When
a run's `--show` matches a key here, and none of those fields were passed
explicitly on the command line, you're offered a single "use these
defaults?" prompt instead of one prompt per field. **Give a show's bundle
every prompted field** (as `daily`'s does above) and accepting the offer
skips every prompt outright — leave one out and that one field still gets
asked for individually, using the top-level value as its suggested answer.
A show with no bundle at all (like a brand-new test show) just gets
prompted individually for everything, including a first-run choice of
audio profile — which is exactly how you'd spin one up using an existing
profile without writing a bundle for it first.

`test_upload` doesn't currently do anything when set here or in a show's
bundle — it's only ever read from the `--test-upload` CLI flag, never from
this file. It's included above for the sake of a complete example, not
because it has any effect placed here.

### Audio profile modes

| Mode | Use for | What it does |
|---|---|---|
| `single_channel` | Solo shows, field recordings, one mic | Two-pass loudnorm on the whole file → mono mp3 (`pipeline/audio.py`) |
| `dual_lav_stereo` | Two people, each on their own lav mic, recorded to a single stereo file (left = speaker A, right = speaker B) | Splits the channels, runs each through a cleanup filter chain, then loudnorms each channel **independently** to the same target — so an unevenly-recorded pair (one mic hot, one quiet) gets leveled *before* mixing, which a single loudnorm pass on the combined signal can't do. Mixes to mono, then runs one more loudnorm pass on the mixdown as a safety net (summing two independently-normalized channels tends to land a few dB hot). This is `pipeline/audio_stereo.py`. |
| `dual_mono_files` | Remote/video-call podcasts, where each participant records locally on their own device and you end up with two separate mono files instead of one stereo file | Same leveling and mixdown as `dual_lav_stereo` — independent per-speaker cleanup + loudnorm, then mix, then a safety-net loudnorm pass — just skipping the channel-split step since each file is already one speaker only. Pass speaker A's file as the usual `input` argument and speaker B's file with `--input2`. Also lives in `pipeline/audio_stereo.py`. |

Every mode reads `lufs`/`tp`/`lra` from its profile, falling back to
-16/-1.5/11 if omitted. Each mode also has one filter-chain override key:
`single_channel` uses `pre_filter` (replaces the whole noise-reduction
chain — it's not appended to the default, so include whatever cleanup you
still want), `dual_lav_stereo` and `dual_mono_files` use `channel_filter`
(applied per-speaker before normalization, same idea). This is the
mechanism for testing a processing change safely: add a new
`audio_profiles` entry with the tweak, point a throwaway show or a test
`--audio-profile` at it, and the real `daily`/`vault-of-the-raw` profiles
stay untouched.

`fade_out_seconds` is accepted on any profile but **not implemented** by
any mode yet — setting it prints a `[NOT YET IMPLEMENTED]` notice rather
than silently doing nothing, so it's not a trap. If a future patch adds
this, it needs to land in the same audio pass that also encodes to mp3,
since ffmpeg's `-b:a 96k` there is a fixed bitrate — a correctly-implemented
fade shouldn't move the output file's size at all; a naive "trim + re-fade"
implementation done as a separate re-encode pass could easily reintroduce a
bitrate mismatch or an accidental content trim, either of which would.

To add a profile, give it a `mode` and your target `lufs`/`tp`/`lra` under
`audio_profiles` in `config/defaults.json`, plus a `pre_filter` or
`channel_filter` override if the default cleanup chain isn't right for it.

For a `dual_mono_files` profile, pass both files on the command line:

```bash
python run.py speaker_a.wav --input2 speaker_b.wav --show some-remote-show --audio-profile some-remote-show
```

**Caveats:**
- `--test-audio` (via `test_audio.py`) currently only previews the `single_channel` path (single-pass vs. two-pass mp3s for A/B listening) using the module's built-in defaults — it doesn't read `config/defaults.json` or accept a profile yet, so it can't preview a custom profile before committing to a real run. There's also no equivalent quick-preview command for `dual_lav_stereo` or `dual_mono_files`.
- `dual_mono_files` doesn't do anything to line up the two files in time — if the two participants didn't start recording at exactly the same moment, sync them (trim the lead-in) before running the pipeline.


---

## Stereo video (future)

`video/src/audio_analysis.py` already has `extract_amplitude_stereo()` that writes
`temp/amplitude_left.json` and `temp/amplitude_right.json`.  To activate it:

1. Call `extract_amplitude_stereo()` instead of `extract_amplitude()` in `pipeline/video.py`
2. Pass both files to a dual-waveform version of `render_frames()` in `renderer.py`
