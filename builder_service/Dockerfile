FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POSTI_DATA_ROOT=/app/data

RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .

RUN groupadd --gid 1000 posti \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin posti \
    && mkdir -p /app/data/projects /app/data/generated_binary \
    && chown -R 1000:1000 /app/data /app/main.py

USER 1000:1000

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "20", "--timeout-keep-alive", "5", "--no-server-header"]
