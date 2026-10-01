# The image only needs three things at runtime: a Python, the dependencies, and
# git. The compiler and the build toolchain exist only to build the two
# tree-sitter packages, so they live in a builder stage and are not part of the
# image that runs.
FROM python:3.11-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /build/requirements.txt


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# git is not optional: analysis clones the repository it is asked about.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# A fixed uid, so a mounted volume can be given the right ownership ahead of
# time, and a user that is not root, because the API clones and reads
# arbitrary public repositories and runs git.
RUN useradd --create-home --uid 10001 codelens && \
    mkdir -p /data && \
    chown -R codelens:codelens /data

WORKDIR /app
COPY --chown=codelens:codelens . /app
USER codelens

# Analyses are written here. Mount a volume at this path to keep them across
# deployments; without one they live in the container filesystem and disappear
# with it, which is survivable but loses the cache.
ENV CODELENS_DATA_DIR=/data \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# --proxy-headers makes the client address the real one behind the platform's
# load balancer, which is what per-client rate limits and guest ids are keyed on.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
