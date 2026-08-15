# YouTube Shorts Dubber

Re-dubs downloaded Shorts into **Hindi** with speaker-aware voices.

## Pipeline (per video)

1. **Transcribe** — Whisper (`base` by default) produces timed speech segments.
2. **Analyse audio** — each segment is classified by pitch:
   - `male` (f0 ≤ 150 Hz) → `hi-IN-MadhurNeural`
   - `female` (f0 ≥ 165 Hz) → `hi-IN-SwaraNeural`
   - undetermined gender → animated-kids voice (`hi-IN-SwaraNeural` pitched up)
3. **Music detection** — segments with continuous, low-variance energy
   (songs/background music) are **kept in the original audio**, so music is
   preserved instead of being dubbed over.
4. **Tone matching** — the Hindi TTS rate and pitch are tuned to the
   original speaker's speech rate and fundamental frequency.
5. **Dedupe** — overlapping or near-duplicate dialog segments are dropped.
6. **Mux** — Hindi TTS (speech) and original audio (music) are placed at
   their original timestamps and replace the video's audio track; the video
   stream is copied unchanged.

## Usage

```bash
python -m dubber \
    --downloads-dir /downloads \
    --out-dir /dubbed \
    --clean \
    --male-voice hi-IN-MadhurNeural \
    --female-voice hi-IN-SwaraNeural \
    --kids-voice hi-IN-SwaraNeural
```

| Flag | Default | Meaning |
|---|---|---|
| `--downloads-dir DIR` | downloads | downloaded MP4s |
| `--out-dir DIR` | dubbed | output for `*.hindi.mp4` |
| `--limit N` | all | max videos to dub |
| `--whisper-model NAME` | base | transcription model |
| `--clean` | off | delete previously dubbed files first |
| `--male-voice` | hi-IN-MadhurNeural | male-segment voice |
| `--female-voice` | hi-IN-SwaraNeural | female-segment voice |
| `--kids-voice` | hi-IN-SwaraNeural | undetermined-gender voice |

Outputs: `<video-id>.hindi.mp4` and `dub-manifest.json` (per-video segment
counts, music segments, and voice distribution).

## Notes

- `openai-whisper`, `edge-tts`, `deep-translator`, `numpy`, and
  `static-ffmpeg` are required.
- Each edge-tts call is retried up to 3×; a tuned-parameter failure falls
  back to default rate/pitch so one bad segment never kills a video.