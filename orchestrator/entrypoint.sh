#!/bin/sh
set -e

mkdir -p /reports /downloads /dubbed /config

# Run one orchestration cycle on startup so the pipeline runs immediately.
python -m orchestrator --once || echo "initial orchestration run failed"

# Schedule the every-2-hour cycle (even hours at minute 5, IST), just after
# the trending agent (minute 0) refreshes its report so the graph always has
# fresh input.
cat > /etc/cron.d/orchestrator <<EOF
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin
5 */2 * * * root cd /app && python -m orchestrator --once >> /var/log/orchestrator.log 2>&1
EOF
chmod 644 /etc/cron.d/orchestrator

touch /var/log/orchestrator.log
echo "[entrypoint] starting cron; 2-hourly orchestration job installed"
exec cron -f -L 2