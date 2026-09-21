# squack backend: one uvicorn process, ffmpeg, local Whisper baked in so the first transmission
# does not wait on a 150 MB download. Build from this directory:  docker build -t squack-backend .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/app/.hf TOWER_DATA_DIR=/app/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, from pyproject alone, so a code change does not reinstall them.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install $(python -c "import tomllib;print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")

COPY . .

# The local fallback model, in the image. CPU int8 is what asr.py picks on a box with no GPU.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
