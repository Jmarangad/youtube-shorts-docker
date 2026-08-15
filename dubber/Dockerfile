FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY entrypoint.sh /entrypoint.sh
COPY requirements.txt /tmp/requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /entrypoint.sh \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir static-ffmpeg

COPY dubber/ dubber/

CMD ["/entrypoint.sh"]
