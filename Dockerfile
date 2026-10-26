FROM caddy:2.10.2 AS edge
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=127.0.0.1 PORT=8000 APP_PORT=8001 CADDY_ENABLED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=edge /usr/bin/caddy /usr/bin/caddy
COPY requirements.txt ./requirements.txt
# Published wheels contain SQLCipher 4 on both amd64 and arm64. Fail if absent.
RUN pip install --no-cache-dir --only-binary=sqlcipher3 -r requirements.txt \
    && python -c "from sqlcipher3 import dbapi2 as d; assert d.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0].startswith('4.')"
COPY app ./app
COPY resources ./resources
COPY run.py ./run.py
COPY backup.py ./backup.py
COPY manage.py ./manage.py
COPY Caddyfile ./Caddyfile
RUN mkdir -p /app/data && useradd --uid 10001 --create-home orbita && chown -R orbita:orbita /app
USER orbita
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,run; raise SystemExit(0 if run.health_ok(int(os.getenv('PORT','8000'))) else 1)"
CMD ["python", "run.py", "all"]
