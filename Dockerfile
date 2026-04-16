FROM python:3.12-slim AS base

WORKDIR /app

# System deps (for aiosqlite / cffi if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY bot/ bot/
COPY partner_bot/ partner_bot/
COPY media/ media/
COPY config.yaml ./
# Google SA key is mounted at runtime, not baked into image

RUN pip install --no-cache-dir -e .

# Default: client bot. Override via docker-compose `command`.
CMD ["python", "-m", "bot"]
