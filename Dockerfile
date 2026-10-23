FROM python:3.13-alpine@sha256:c50ccfb66e5cc97a610dcd0b9597de6ef5b52c96280b51997b353a0fc87a8361
ARG VCS_REF=local
LABEL org.opencontainers.image.source="https://github.com/SamuraiSBS/localhost-3000"
LABEL org.opencontainers.image.revision=$VCS_REF
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_REVISION=$VCS_REF DATA_DIR=/data SOURCE_SNAPSHOT_PATH=/app/data/seed.json
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && mkdir /data && chown 65532:65532 /data
COPY --chown=65532:65532 app ./app
COPY --chown=65532:65532 web ./web
COPY --chown=65532:65532 design ./design
COPY --chown=65532:65532 data/seed.json ./data/seed.json
USER 65532:65532
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/healthz', timeout=2).read()"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
