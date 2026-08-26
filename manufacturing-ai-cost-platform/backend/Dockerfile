# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Backend image.
#
# No secret is baked into this image. Credentials arrive as environment
# variables at run time (SECURITY.md section 6).
# ---------------------------------------------------------------------------

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container healthcheck.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Dependencies are installed from pyproject.toml alone so this layer is cached
# and does not rebuild on every source change.
# ---------------------------------------------------------------------------
COPY pyproject.toml ./
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install "."

# ---------------------------------------------------------------------------
# Application source
# ---------------------------------------------------------------------------
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Run as a non-root user, and give it ownership of the SQLite data directory.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Probes the contract endpoint, which lives under the /api/v1 prefix.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
