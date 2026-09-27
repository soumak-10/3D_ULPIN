# =============================================================================
#  FastAPI image.
#
#  Two targets:
#    development — source bind-mounted by compose, uvicorn --reload
#    production  — source copied in, no reload, non-root, multiple workers
#
#  Build from the repository root:
#      docker build -f infra/docker/api.Dockerfile --target production -t ulpin-api .
# =============================================================================

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq for psycopg, curl for the healthcheck. `--no-install-recommends` keeps
# a compiler toolchain out of the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# -----------------------------------------------------------------------------
# Dependencies resolve in their own layer so that editing application code does
# not re-resolve the whole tree on every build.
FROM base AS deps

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY apps/api/pyproject.toml ./
# Install dependencies without the project itself: there is no source here yet,
# and a wheel built from an empty tree would have to be rebuilt anyway.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install "fastapi>=0.115,<0.120" "uvicorn[standard]>=0.32,<0.40" \
      "python-multipart>=0.0.18" "pydantic>=2.9,<3" "pydantic-settings>=2.6,<3" \
      "email-validator>=2.2" "SQLAlchemy[asyncio]>=2.0.36,<2.1" "asyncpg>=0.30" \
      "psycopg[binary]>=3.2" "GeoAlchemy2>=0.15" "alembic>=1.14" \
      "argon2-cffi>=23.1" "PyJWT[crypto]>=2.9,<3" "anyio>=4.6" "redis>=5.2" \
      "httpx>=0.28"

# -----------------------------------------------------------------------------
FROM base AS development

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN /opt/venv/bin/pip install pytest pytest-asyncio pytest-cov ruff mypy faker

COPY apps/api/pyproject.toml ./
# Source arrives as a bind mount from compose; this copy is only so the image
# is runnable on its own.
COPY apps/api/app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/app"]

# -----------------------------------------------------------------------------
FROM base AS production

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app

# Unprivileged. A registry that can be rewritten by whatever runs in its own
# container is not a registry anyone should trust.
RUN useradd --system --uid 10001 --create-home ulpin \
 && chown -R ulpin:ulpin /app
USER ulpin

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail --silent http://localhost:8000/health || exit 1

# Workers are processes, not threads: the app is async, so a small number of
# workers each running an event loop beats a large number of either.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
