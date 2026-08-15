#!/bin/sh
set -e

mkdir -p /dubbed /config /reports /downloads

# Wait for the dubber to produce at least one dubbed MP4 before uploading.
attempt=0
while ! ls /dubbed/*.mp4 >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "no dubbed videos after 100s; continuing"
    break
  fi
  echo "waiting for dubbed videos (${attempt}/20)..."
  sleep 5
done

# Run once on startup so freshly dubbed videos upload immediately.
python -m uploader \
    --dubbed-dir /dubbed \
    --reports-dir /reports \
    --downloads-dir /downloads \
    --config-dir /config \
    --privacy "${YT_PRIVACY:-unlisted}" \
    || echo "initial upload run failed"

# Schedule the daily job (23:50 IST), after the dubber (23:40) finishes.
cat > /etc/cron.d/uploader <<EOF
SHELL=/bin/sh
50 23 * * * root cd /app && python -m uploader --dubbed-dir /dubbed --reports-dir /reports --downloads-dir /downloads --config-dir /config --privacy "${YT_PRIVACY:-unlisted}" >> /var/log/uploader.log 2>&1
EOF
chmod 644 /etc/cron.d/uploader

touch /var/log/uploader.log
echo "[entrypoint] starting cron; daily 23:50 uploader job installed"
exec cron -f -L 2
