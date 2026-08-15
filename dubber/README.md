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
   original speaker's speech rate and fundamental frequency (gently clamped
   so voices stay natural).
5. **Natural voice** — each TTS clip is post-processed through
   high-pass/low-pass EQ, gentle compression, a subtle reverb, and a limiter,
   so the dubbed speech sounds like a warm studio voice instead of raw TTS.
6. **Dedupe** — overlapping or near-duplicate dialog segments are dropped.
7. **Timeline mix** — speech, kept-original music, and a soft ambient music
   bed are mixed into a single numpy timeline:
   - **music bed**: silent regions (no speech, no music) get a synthesized
     mellow pad whose gain follows the scene's loudness envelope, so music
     appears only where it belongs and "blends with the scene";
   - **crossfades**: every clip is faded in/out (~30–40 ms) and the bed mask
     is smoothed, so transitions between speech, music, and silence are
     seamless instead of abrupt.
8. **Mux** — the mixed track replaces the video's audio; video is unchanged.

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

Outputs: each video's dubbed file keeps the **original filename**
(`<video-id>.mp4`) in the `dubbed` volume, plus `dub-manifest.json` with
per-video segment counts, music segments, and voice distribution.

## Notes

- `openai-whisper`, `edge-tts`, `deep-translator`, `numpy`, and
  `static-ffmpeg` are required.
- Each edge-tts call is retried up to 3×; a tuned-parameter failure falls
  back to default rate/pitch so one bad segment never kills a video.