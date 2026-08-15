#!/bin/sh
set -e

mkdir -p /reports

# Persist the API key and PATH so cron inherits them
# (cron does not read container env and uses a minimal PATH).
{
  echo "export YOUTUBE_API_KEY='${YOUTUBE_API_KEY}'"
  echo "export PATH='/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin'"
} > /app/runenv.sh
chmod 600 /app/runenv.sh

# Run once on startup so the very first report exists immediately.
. /app/runenv.sh
python -m agent --top 5 --language non-hindi --recent-hours 24 --output /reports || echo "initial run failed"

# Schedule the hourly job (every hour at minute 0, IST).
cat > /etc/cron.d/agent <<EOF
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin
0 * * * * root . /app/runenv.sh && cd /app && python -m agent --top 5 --language non-hindi --recent-hours 24 --output /reports >> /var/log/agent.log 2>&1
EOF
chmod 644 /etc/cron.d/agent

touch /var/log/agent.log
echo "[entrypoint] starting cron; hourly trending job installed"
exec cron -f -L 2
