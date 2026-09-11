# syntax=docker/dockerfile:1.5

FROM node:26-alpine AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend .
RUN npm run build

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_ROOT=/app/static \
    POSTI_DATA_ROOT=/app/data
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils \
    && rm -rf /var/lib/apt/lists/*
COPY builder_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 1000 posti \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin posti
COPY --chown=1000:1000 builder_service/main.py ./main.py
COPY --chown=1000:1000 --from=frontend /web/dist ./static
RUN mkdir -p /app/data/projects /app/data/generated_binary \
    && chown -R 1000:1000 /app/data
USER 1000:1000
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "20", "--timeout-keep-alive", "5", "--no-server-header"]
