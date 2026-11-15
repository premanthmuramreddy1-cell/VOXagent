FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres
RUN uv run playwright install --with-deps chromium
COPY app ./app
RUN useradd --create-home vox && mkdir -p /data && chown vox /data
USER vox
ENV VOX_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK CMD curl -sf http://localhost:8000/healthz || exit 1
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
