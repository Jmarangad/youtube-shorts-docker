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
python -m dubber \
    --downloads-dir /downloads \
    --out-dir /dubbed \
    --whisper-model "${WHISPER_MODEL:-base}" \
    --voice "${DUB_VOICE:-hi-IN-MadhurNeural}" \
    || echo "initial run failed"

# Schedule the daily job (23:40 IST), after the downloader (23:30) finishes.
cat > /etc/cron.d/dubber <<EOF
SHELL=/bin/sh
40 23 * * * root cd /app && python -m dubber --downloads-dir /downloads --out-dir /dubbed --whisper-model "${WHISPER_MODEL:-base}" --voice "${DUB_VOICE:-hi-IN-MadhurNeural}" >> /var/log/dubber.log 2>&1
EOF
chmod 644 /etc/cron.d/dubber

touch /var/log/dubber.log
echo "[entrypoint] starting cron; daily 23:40 dubber job installed"
exec cron -f -L 2
