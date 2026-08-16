#!/bin/sh
set -e

mkdir -p /downloads /dubbed

# Wait for the downloader to produce at least one MP4 before dubbing.
attempt=0
while ! ls /downloads/*.mp4 >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "no downloaded videos after 100s; continuing"
    break
  fi
  echo "waiting for downloaded videos (${attempt}/20)..."
  sleep 5
done

# Run once on startup so already-downloaded videos get dubbed immediately.
# --clean deletes previously dubbed files so every run re-dubs afresh.
python -m dubber \
    --downloads-dir /downloads \
    --out-dir /dubbed \
    --whisper-model "${WHISPER_MODEL:-base}" \
    --clean \
    --male-voice "${DUB_MALE_VOICE:-hi-IN-MadhurNeural}" \
    --female-voice "${DUB_FEMALE_VOICE:-hi-IN-SwaraNeural}" \
    --kids-voice "${DUB_KIDS_VOICE:-hi-IN-SwaraNeural}" \
    || echo "initial run failed"

# Schedule the every-2-hour job (even hours at minute 25, IST), after the
# downloader (minute 10). No --clean: each cycle only new/undubbed videos
# are processed; the startup run above re-dubs everything fresh once per deploy.
cat > /etc/cron.d/dubber <<EOF
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin
25 */2 * * * root cd /app && python -m dubber --downloads-dir /downloads --out-dir /dubbed --whisper-model "${WHISPER_MODEL:-base}" --male-voice "${DUB_MALE_VOICE:-hi-IN-MadhurNeural}" --female-voice "${DUB_FEMALE_VOICE:-hi-IN-SwaraNeural}" --kids-voice "${DUB_KIDS_VOICE:-hi-IN-SwaraNeural}" >> /var/log/dubber.log 2>&1
EOF
chmod 644 /etc/cron.d/dubber

touch /var/log/dubber.log
echo "[entrypoint] starting cron; 2-hourly dubber job installed"
exec cron -f -L 2
