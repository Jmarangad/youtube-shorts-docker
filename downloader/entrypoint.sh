#!/bin/sh
set -e

mkdir -p /reports /downloads

# Wait for the trending agent to produce at least one report before running,
# so the startup run processes the day's videos instead of an empty set.
attempt=0
while ! ls /reports/trending-shorts-*.json >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "no trending reports after 100s; continuing with empty set"
    break
  fi
  echo "waiting for trending reports (${attempt}/20)..."
  sleep 5
done

# Run once on startup so the day's videos are fetched immediately.
python -m downloader \
    --reports-dir /reports \
    --download-dir /downloads \
    --whisper-model "${WHISPER_MODEL:-tiny}" \
    || echo "initial run failed"

# Schedule the daily job (23:30 IST), after the day's hourly trending reports.
cat > /etc/cron.d/downloader <<EOF
SHELL=/bin/sh
30 23 * * * root cd /app && python -m downloader --reports-dir /reports --download-dir /downloads --whisper-model "${WHISPER_MODEL:-tiny}" >> /var/log/downloader.log 2>&1
EOF
chmod 644 /etc/cron.d/downloader

touch /var/log/downloader.log
echo "[entrypoint] starting cron; daily 23:30 downloader job installed"
exec cron -f -L 2
