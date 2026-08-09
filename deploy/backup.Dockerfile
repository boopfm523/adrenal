# Dedicated Python 3.13 backup image. It carries the application queue adapter plus
# PostgreSQL 16 client tools and age, but is never used for the API.

FROM postgres@sha256:64154d0babcb1741988719e703419af0382b19953706149f9872fbd0f438efa8 AS postgres-client

FROM ghcr.io/astral-sh/uv:0.9.9-python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm AS runtime

RUN apt-get update \
 && apt-get install --yes --no-install-recommends age ca-certificates liblz4-1 libpq5 libzstd1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 healthcurve \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin healthcurve

COPY --from=postgres-client /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=postgres-client /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER 10001:10001
RUN python -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version"
ENTRYPOINT ["python", "-m", "healthcurve.operations.backup"]
