# Self-hostable bird-id server (ingest service + dashboard).
#
# This image carries the full BirdNET stack (TensorFlow + librosa) so the ingest
# service can identify uploaded sensor clips. It uses the Linux requirements.txt
# (NOT requirements-intel-mac.txt) — the same set the Raspberry Pi target will use.
# The local mic monitor is intentionally NOT containerized (it needs host audio
# hardware); the Docker story is "self-host the server, point ESP32-S3 sensors at it."
FROM python:3.12-slim

# ffmpeg     — clip mp3 transcode (clips.py)
# libsndfile1 — soundfile backend (clip extraction / spectrograms)
# libgomp1   — OpenMP runtime needed by TensorFlow / scikit-learn
# tzdata     — zoneinfo so config "timezone" resolves (else times stay UTC)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching (this layer is large and rarely changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# `db` and `recordings_dir` are resolved relative to the CWD. docker-compose sets
# working_dir to /data (a mounted volume), so state persists there. Code and config
# stay under /app and resolve via __file__, independent of CWD.
EXPOSE 8081 8080

# Default to the ingest service; the dashboard service overrides this command.
CMD ["python", "/app/birdid.py", "ingest-server", "--host", "0.0.0.0", "--port", "8081"]
