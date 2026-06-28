# syntax=docker/dockerfile:1

# ---- builder ----------------------------------------------------------------
FROM python:3.12-slim AS builder

ARG VERSION=0.0.0

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Build deps for any wheels that need compiling (kept out of the runtime image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /src

# Copy only what the build backend needs first, then the package.
COPY pyproject.toml README.md LICENSE ./
COPY avatar ./avatar

# Stamp the version so the wheel metadata matches the build-arg, then install
# the project with all optional provider extras into the venv.
RUN sed -i "s/^version = .*/version = \"${VERSION}\"/" pyproject.toml \
    && pip install --no-cache-dir ".[all]"

# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ARG VERSION=0.0.0

LABEL org.opencontainers.image.title="avatar" \
      org.opencontainers.image.description="A lightweight, trigger-based social media bot." \
      org.opencontainers.image.source="https://github.com/t11z/avatar" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AVATAR_VERSION="${VERSION}"

# Non-root user and a data dir it owns.
RUN useradd --create-home --uid 10001 avatar \
    && mkdir -p /data /config \
    && chown -R avatar:avatar /data /config

# Bring the fully-built virtualenv across from the builder.
COPY --from=builder /opt/venv /opt/venv

USER avatar
WORKDIR /home/avatar

VOLUME ["/data"]

# 8080 = health (/healthz, /readyz), 9090 = Prometheus metrics (/metrics).
EXPOSE 8080 9090

# slim has no curl; use python's stdlib to probe the liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=3).status==200 else 1)"]

ENTRYPOINT ["avatar", "run", "-c", "/config/config.yaml"]
