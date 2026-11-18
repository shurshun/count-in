# Separation worker for a RunPod serverless endpoint. Build from the repo root:
#   docker buildx build --platform linux/amd64 -f runpod/Dockerfile -t <registry>/count-in-worker:<tag> .
# The separation model is baked in, so a cold start loads it from local disk instead of downloading 640 MB.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy COUNTIN_ASSETS=/app/models PYTHONUNBUFFERED=1

# Dependencies first, so code changes do not reinstall torch. The GUI and the beat tracker (a git
# dependency, and the worker only separates) stay out.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project --no-install-package pyside6-essentials --no-install-package beat-this

COPY models/separator /app/models/separator
COPY src ./src
COPY runpod/handler.py ./
# runpod is not in the lock, and a sync removes packages outside it, so it goes in last.
RUN uv sync --locked --no-dev --no-install-package pyside6-essentials --no-install-package beat-this \
    && uv pip install runpod==2.2.1

CMD ["uv", "run", "--no-sync", "python", "handler.py"]
