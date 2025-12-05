# syntax=docker/dockerfile:1.5

FROM node:20-alpine AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend .
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_ROOT=/app/static \
    POSTI_DATA_ROOT=/app/data
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY builder_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY builder_service/main.py ./main.py
COPY --from=frontend /web/dist ./static
RUN mkdir -p /app/data/generated_binary
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
