FROM denoland/deno:bin-2.3.0 AS deno

FROM node:22-slim AS web

WORKDIR /web
COPY web/package*.json ./
RUN npm install
COPY web ./
RUN npm run build

FROM python:3.12-slim

COPY --from=deno /deno /usr/local/bin/deno

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2026.08.19 replaces the android_vr default client that now returns HTTP 403.
# Keep EJS in sync with yt-dlp through its default dependency group.
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]>=2026.08.19" spotdl \
    && pip check

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /web/dist ./src/media_dl/static
RUN pip install --no-cache-dir .

ENV MUSIC_ROOT=/music \
    STATE_DIR=/state \
    QUEUE_DIR=/queue \
    DOWNLOAD_DIR=/downloads \
    AUDIO_FORMAT=m4a \
    SERVICE_PORT=8765

VOLUME ["/music", "/state", "/queue", "/downloads"]

ENTRYPOINT ["media-dl"]
CMD ["serve"]
