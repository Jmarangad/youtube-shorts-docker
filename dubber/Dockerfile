FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY dubber/ dubber/
COPY entrypoint.sh /entrypoint.sh

RUN pip install --no-cache-dir \
        openai-whisper \
        edge-tts \
        deep-translator \
        static-ffmpeg \
        numpy \
        certifi \
    && chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
