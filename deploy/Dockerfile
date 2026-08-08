# Runtime image for the api and worker (ADR-0002, ADR-0006).
#
# Two stages so the runtime image carries no build toolchain and no compilers --
# threat model T6 asks for a minimal runtime surface.

# --- builder ---------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.9.9-python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a source change does not invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./
COPY alembic.ini ./
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime ---------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

# Non-root, matching the uid/gid the compose file pins.
RUN groupadd --gid 10001 healthcurve \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin healthcurve

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/src /app/src
COPY --from=builder --chown=10001:10001 /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=10001:10001 /app/migrations /app/migrations

USER 10001:10001
EXPOSE 8000

# ADR-0006: the deployed interpreter must be the pinned one. Failing here is better
# than discovering a base-image bump changed the runtime in production.
RUN python -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version"

CMD ["uvicorn", "healthcurve.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
